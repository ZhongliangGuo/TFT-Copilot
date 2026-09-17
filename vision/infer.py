#!/usr/bin/env python3
"""端到端识别 (v3 两阶段): 一张 1920x1080 备战截图 -> 结构化棋盘(棋子/星级/装备, 中文名)。

后端 = onnxruntime(无 torch)。模型 vision/onnx/{identity,stars,equipment}.onnx。

两阶段固定模板(与 s18-first-v3 PROTOCOL 一致):
  阶段1  preprocess.py 按固定坐标切 37 格 context(RGB)+anchor -> identity 分类器(4通道)
         判每格棋子(含 empty) -> occupied(非空格 key 列表)。
  阶段2  hud_stage.py / hud_equipment_v5.py: 用 occupied 定位血条, 只对定位成功的格子
         裁 星级(64x?) 和 装备血条行 -> stars 分类器 + equipment YOLO(NMS 已融进 onnx)。
         血条定位失败/不唯一/疑似污染 -> 星级/装备标 unresolved(协议: 不推断为一星/无装备)。

用法: python vision/infer.py <screenshot.png> [--conf 0.5] [--eqconf 0.35] [--json out.json]
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

VIS = Path(__file__).resolve().parent
ROOT = VIS.parent
DATA = ROOT / "data" / "vision_dataset" / "s18-first-v3"          # identity/stars 协议
EQUIP = ROOT / "data" / "vision_dataset" / "s18-equipment-v5.1"   # 装备协议 ROI
sys.path.insert(0, str(DATA / "protocol"))              # preprocess / hud_stage
sys.path.insert(0, str(EQUIP / "protocol"))             # hud_equipment_v5
sys.path.insert(0, str(VIS))                            # onnx_backend
sys.path.insert(0, str(VIS / "hud"))                    # item_names
from onnx_backend import load_session, default_providers, resize_chw, gaussian_anchor, softmax, META

# 变身/召唤形态在 champions.json 无独立 apiName, 映射到本体/召唤棋子中文名(与主功能实体名一致)
FORM_OVERRIDE = {
    "DA_18_EliseSpider": "伊莉丝", "DA_18_GnarBig": "纳尔", "DA_18_NunuWillump": "努努",
    "DA_NidaleeCougar18_AD": "奈德丽",
    "DA_Elderwood18_Lifeblossom": "生命花", "DA_Elderwood18_Protector": "深林守卫",
    "DA_Elderwood18_StonebarkTree": "石皮树",
}


def name_maps():
    ch = json.loads((ROOT / "data" / "packs" / "set18" / "champions.json").read_text(encoding="utf-8"))
    api2cn = {c["apiName"]: c["name"] for c in ch}
    api2cn.update(FORM_OVERRIDE)
    from item_names import build_item_cn      # 与备战席共用同一套中文映射
    item2cn = build_item_cn(ROOT)
    return api2cn, item2cn


class BoardRecognizer:
    """己方棋盘识别(棋子/星级/装备)。构造时载入 onnx 会话, recognize() 逐图调用。"""

    def __init__(self, providers=None):
        providers = providers or default_providers()
        self.id_s = load_session("identity.onnx", providers)
        self.st_s = load_session("stars.onnx", providers)
        self.eq_s = load_session("equipment.onnx", providers)
        self.id_meta, self.st_meta, self.eq_meta = META["identity"], META["stars"], META["equipment"]
        self.eq_names = {int(k): v for k, v in self.eq_meta["names"].items()}
        self.api2cn, self.item2cn = name_maps()
        from preprocess import preprocess
        from hud_stage import get_slots, locate, save_crops
        import hud_equipment_v5 as eqv5
        self._pre = preprocess
        self._star_stage = (get_slots, locate, save_crops)
        self._eqv5 = eqv5

    # ---- 预处理 (与训练一致, 纯 numpy) ----
    def _id_input(self, crop_path, tp):
        img = Image.open(crop_path).convert("RGB")
        w, h = img.size
        rgb = np.asarray(img, np.float32).transpose(2, 0, 1) / 255.0
        x = np.concatenate([rgb, gaussian_anchor(w, h, *tp)[None]], 0)
        x = resize_chw(x, self.id_meta["size"])
        mean = np.array(self.id_meta["mean"], np.float32)[:, None, None]
        std = np.array(self.id_meta["std"], np.float32)[:, None, None]
        x[:3] = (x[:3] - mean) / std
        return x.astype(np.float32)

    def _star_input(self, crop_path):
        img = Image.open(crop_path).convert("RGB")
        x = np.asarray(img, np.float32).transpose(2, 0, 1) / 255.0
        x = resize_chw(x, self.st_meta["size"])
        mean = np.array(self.st_meta["mean"], np.float32)[:, None, None]
        std = np.array(self.st_meta["std"], np.float32)[:, None, None]
        return ((x - mean) / std).astype(np.float32)

    def _eq_letterbox(self, path):
        """复现 ultralytics letterbox: 等比缩放到 imgsz 见方, 114 填充, BGR->RGB, /255。"""
        im = cv2.imread(str(path))
        h0, w0 = im.shape[:2]
        s = self.eq_meta["imgsz"]
        r = min(s / h0, s / w0)
        nw, nh = round(w0 * r), round(h0 * r)
        rs = cv2.resize(im, (nw, nh), interpolation=cv2.INTER_LINEAR)
        canvas = np.full((s, s, 3), 114, np.uint8)
        dw, dh = (s - nw) // 2, (s - nh) // 2
        canvas[dh:dh + nh, dw:dw + nw] = rs
        blob = canvas[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        return np.ascontiguousarray(blob)

    def _eq_detect(self, path, eqconf):
        """onnx 输出 [300,6]=xyxy+conf+cls(NMS 已融进图)。取置信最高 top3, 按 x 从左到右排。"""
        out = self.eq_s.run(None, {self.eq_s.get_inputs()[0].name: self._eq_letterbox(path)})[0][0]
        cand = [(float(c), self.eq_names[int(k)], float((x1 + x2) / 2))
                for x1, y1, x2, y2, c, k in out if float(c) >= eqconf]
        cand = sorted(cand, key=lambda c: -c[0])[:3]
        return [(nm, round(cf, 3)) for cf, nm, _ in sorted(cand, key=lambda c: c[2])]

    def recognize(self, image_path, conf=0.5, eqconf=0.35, mode="ally"):
        """mode='ally' 己方棋盘(下半屏), 'enemy' 敌方棋盘(上半屏, 位置镜像但血条仍在棋子上方)。
        敌方几何用 regions.json 的 enemyCells; locate 硬编码"血条在棋子上方", 已适配敌方。"""
        img = Image.open(image_path).convert("RGB")
        tmp = Path(tempfile.mkdtemp(prefix="tft_v3_"))
        rows = self._pre(img, tmp, mode=mode)
        idc = self.id_meta["classes"]
        batch = np.stack([self._id_input(tmp / r["image"], r["target_point"]) for r in rows])
        prob = softmax(self.id_s.run(None, {"input": batch})[0])
        idx, confs = prob.argmax(1), prob.max(1)

        units, occupied = {}, []
        for r, ci, pi in zip(rows, idx.tolist(), confs.tolist()):
            cname = idc[ci]
            if cname == "empty":
                continue
            occupied.append(r["cell"])
            units[r["cell"]] = {"cell": r["cell"], "unit_id": cname,
                                "name": self.api2cn.get(cname, cname) if pi >= conf else "unknown",
                                "id_conf": round(pi, 3), "star": None, "star_conf": None,
                                "items": [], "items_status": "resolved"}

        get_slots, locate, save_crops = self._star_stage
        hud_star = tmp / "hud_stars"
        man_star = locate(img, get_slots(mode), occupied, mode=mode); save_crops(img, man_star, hud_star)
        # 装备识别只对我方棋盘做: 敌方没有专门校准/验证过的装备栏识别(己方视角下固定位置的
        # 备战散装备栏在切到敌方视角时仍会留在屏幕同一位置, 容易被误当成敌方棋子的装备栏
        # 裁进来), 宁可敌方装备一律留空, 也不要输出不可靠的结果。
        if mode == "ally":
            hud_eq = tmp / "hud_eq"
            man_eq = self._eqv5.locate(img, self._eqv5.get_slots(mode), occupied, mode=mode)
            self._eqv5.save_crops(img, man_eq, hud_eq)
        else:
            hud_eq = None
            man_eq = {"slots": {}}

        for key, e in units.items():
            sr = man_star["slots"].get(key, {}).get("regions", {}).get("stars", {})
            if sr.get("file"):
                sp = softmax(self.st_s.run(None, {"input": self._star_input(hud_star / sr["file"])[None]})[0])[0]
                e["star"], e["star_conf"] = int(sp.argmax()) + 1, round(float(sp.max()), 3)
            if mode != "ally":
                e["items_status"] = "unresolved(enemy_item_recognition_not_supported)"
                continue
            eslot = man_eq["slots"].get(key, {})
            er = eslot.get("regions", {}).get("equipment", {})
            if er.get("file"):
                for iid, cf in self._eq_detect(hud_eq / er["file"], eqconf):
                    e["items"].append({"item_id": iid, "name": self.item2cn.get(iid, iid), "conf": cf})
            else:
                e["items_status"] = "unresolved(" + (er.get("status") or eslot.get("status", "no_healthbar")) + ")"
        return list(units.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image", type=Path)
    ap.add_argument("--conf", type=float, default=0.5)
    ap.add_argument("--eqconf", type=float, default=0.35)
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()
    board = BoardRecognizer().recognize(args.image, args.conf, args.eqconf)
    out = {"image": str(args.image), "n_units": len(board), "board": board}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    if args.json:
        args.json.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n--- 识别摘要 ---")
    for e in board:
        star = f"{e['star']}★" if e["star"] else "?★"
        it = " + ".join(i["name"] for i in e["items"]) or (
            "无装备" if e.get("items_status") == "resolved" else e.get("items_status", "无装备"))
        print(f"  {e['cell']:<12} {e['name']:<8} {star}  [{it}]")


if __name__ == "__main__":
    main()
