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
        from hud_stage import get_slots, locate
        import hud_equipment_v5 as eqv5
        self._get_slots = get_slots      # 阶段1 槽位几何 (与 preprocess 同一 regions.json)
        self._star_locate = locate       # 阶段2 血条定位(星级)
        self._eqv5 = eqv5                # 阶段2 血条定位(装备)

    # ---- 预处理 (与训练一致, 纯 numpy; 全程内存, 不落盘) ----
    def _id_input(self, img, tp):
        img = img.convert("RGB")
        w, h = img.size
        rgb = np.asarray(img, np.float32).transpose(2, 0, 1) / 255.0
        x = np.concatenate([rgb, gaussian_anchor(w, h, *tp)[None]], 0)
        x = resize_chw(x, self.id_meta["size"])
        mean = np.array(self.id_meta["mean"], np.float32)[:, None, None]
        std = np.array(self.id_meta["std"], np.float32)[:, None, None]
        x[:3] = (x[:3] - mean) / std
        return x.astype(np.float32)

    def _star_input(self, img):
        img = img.convert("RGB")
        x = np.asarray(img, np.float32).transpose(2, 0, 1) / 255.0
        x = resize_chw(x, self.st_meta["size"])
        mean = np.array(self.st_meta["mean"], np.float32)[:, None, None]
        std = np.array(self.st_meta["std"], np.float32)[:, None, None]
        return ((x - mean) / std).astype(np.float32)

    def _eq_letterbox(self, rgb):
        """复现 ultralytics letterbox: 等比缩放到 imgsz 见方, 114 填充, /255。
        入参为 RGB ndarray (原实现读 cv2 BGR 再翻转, 内存化后省去)。"""
        h0, w0 = rgb.shape[:2]
        s = self.eq_meta["imgsz"]
        r = min(s / h0, s / w0)
        nw, nh = round(w0 * r), round(h0 * r)
        rs = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_LINEAR)
        canvas = np.full((s, s, 3), 114, np.uint8)
        dw, dh = (s - nw) // 2, (s - nh) // 2
        canvas[dh:dh + nh, dw:dw + nw] = rs
        blob = canvas.transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        return np.ascontiguousarray(blob)

    def _eq_post(self, out, eqconf):
        """onnx 输出 [300,6]=xyxy+conf+cls(NMS 已融进图)。取置信最高 top3, 按 x 从左到右排。"""
        cand = [(float(c), self.eq_names[int(k)], float((x1 + x2) / 2))
                for x1, y1, x2, y2, c, k in out if float(c) >= eqconf]
        cand = sorted(cand, key=lambda c: -c[0])[:3]
        return [(nm, round(cf, 3)) for cf, nm, _ in sorted(cand, key=lambda c: c[2])]

    @staticmethod
    def _ready_crop(img, slots, key, task):
        """从阶段2 manifest 取 ready 区域的内存裁图 (替代 save_crops 落盘再读回)。"""
        slot = slots.get(key, {})
        region = slot.get("regions", {}).get(task, {})
        if region.get("status") != "ready":
            return None, region, slot
        x, y, w, h = region["bbox"]
        return img.crop((x, y, x + w, y + h)), region, slot

    def recognize(self, image_path, conf=0.5, eqconf=0.35, mode="ally"):
        """mode='ally' 己方棋盘(下半屏), 'enemy' 敌方棋盘(上半屏, 位置镜像但血条仍在棋子上方)。
        敌方几何用 regions.json 的 enemyCells; locate 硬编码"血条在棋子上方", 已适配敌方。"""
        img = Image.open(image_path).convert("RGB")
        slots = self._get_slots(mode)
        # 阶段1: 按固定坐标在内存切 37 格 (几何与协议 preprocess 一致), 组批一次推理
        rows = []
        for slot in slots:
            cx, cy = slot["center"]
            x, y = round(cx - 90), round(cy - 165)
            rows.append({"cell": slot["key"], "crop": img.crop((x, y, x + 180, y + 205)),
                         "target_point": [cx - x, cy - y]})
        idc = self.id_meta["classes"]
        batch = np.stack([self._id_input(r["crop"], r["target_point"]) for r in rows])
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

        man_star = self._star_locate(img, slots, occupied, mode=mode)
        # 装备识别只对我方棋盘做: 敌方没有专门校准/验证过的装备栏识别(己方视角下固定位置的
        # 备战散装备栏在切到敌方视角时仍会留在屏幕同一位置, 容易被误当成敌方棋子的装备栏
        # 裁进来), 宁可敌方装备一律留空, 也不要输出不可靠的结果。
        man_eq = (self._eqv5.locate(img, self._eqv5.get_slots(mode), occupied, mode=mode)
                  if mode == "ally" else {"slots": {}})

        # 星级: 所有定位成功的格子组批, 一次推理
        star_keys, star_imgs = [], []
        for key in units:
            crop, _, _ = self._ready_crop(img, man_star["slots"], key, "stars")
            if crop is not None:
                star_keys.append(key)
                star_imgs.append(crop)
        if star_imgs:
            sp = softmax(self.st_s.run(
                None, {"input": np.stack([self._star_input(im) for im in star_imgs])})[0])
            for key, p in zip(star_keys, sp):
                units[key]["star"], units[key]["star_conf"] = int(p.argmax()) + 1, round(float(p.max()), 3)

        if mode != "ally":
            for e in units.values():
                e["items_status"] = "unresolved(enemy_item_recognition_not_supported)"
            return list(units.values())

        # 装备: equipment.onnx 固定 batch=1, 逐棋子推理 (但仍全程内存, 不落盘)
        eq_todo, unresolved = [], {}
        for key in units:
            crop, er, eslot = self._ready_crop(img, man_eq["slots"], key, "equipment")
            if crop is not None:
                eq_todo.append((key, self._eq_letterbox(np.asarray(crop))))
            else:
                unresolved[key] = (er, eslot)
        for key, blob in eq_todo:
            out = self.eq_s.run(None, {self.eq_s.get_inputs()[0].name: blob})[0][0]
            for iid, cf in self._eq_post(out, eqconf):
                units[key]["items"].append({"item_id": iid, "name": self.item2cn.get(iid, iid), "conf": cf})
        for key, (er, eslot) in unresolved.items():
            units[key]["items_status"] = "unresolved(" + (er.get("status") or eslot.get("status", "no_healthbar")) + ")"
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
