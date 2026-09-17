#!/usr/bin/env python3
"""备战席散装备识别 (我方棋盘)。固定 2列×10 槽网格 + 预训练 CNN embedding 最近邻匹配
到 137 张装备参考图标(复用装备数据集的 reference/hud-icons, 23×23 无框)。

为什么用 embedding 而非分类模型/SSIM/NCC:
- 每类只有1张参考图, 分类模型没意义。
- 备战席道具是 ~50px 带边框, 参考是 23×23 无框; SSIM/NCC 对这种渲染差不稳(实测蓝泪会错成纹章)。
- 预训练 resnet18 特征 + 余弦, 免训练, 对边框/缩放/色调差鲁棒(实测正常件 0.8-0.91)。

只做**正常装备**: 可叠层消耗品(重铸器/拆卸器)不在参考库, 低于阈值自动拒识(返回 unknown), 不处理角标数字。

用法:
  from bench_items import BenchMatcher
  bm = BenchMatcher()
  bm.recognize("screenshot.png")   # -> [{slot,col,row, item_id, name, conf}, ...] 只列占用且过阈值的
"""
import glob
import sys
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))          # onnx_backend
from onnx_backend import load_session, default_providers, resize_chw
REF_DIR = ROOT / "data" / "vision_dataset" / "s18-equipment-v4" / "reference" / "hud-icons"

# 20 槽网格 (1920x1080, 用户截图标定): 2列 × 10行
GRID = {"origin": [8, 258], "slot": 50, "pitch": 55, "cols": 2, "rows": 10, "inner_pad": 6}
OCC_MIN = 28      # 槽内平均亮度 >= 此值 = 占用
CONF_MIN = 0.65   # 余弦相似度阈值。备战席道具都在库里(137+消耗品), 阈值可略低; 消耗品另有参考不靠阈值拒
MEAN, STD = [0.485, 0.456, 0.406], [0.229, 0.224, 0.225]


class BenchMatcher:
    def __init__(self, providers=None):
        self.model = load_session("bench_feat.onnx", providers or default_providers())  # resnet18 去fc -> 512维
        self.mean = np.array(MEAN, np.float32)[:, None, None]
        self.std = np.array(STD, np.float32)[:, None, None]
        # 137 装备参考(无框HUD图标) + 消耗品参考(从真实截图裁, 重铸器/拆卸器等, 用于识别并跳过)
        refs = sorted(glob.glob(str(REF_DIR / "*.png")))
        cons = sorted(glob.glob(str(HERE / "ref_consumables" / "*.png")))
        self.ref_names = [Path(p).stem for p in refs] + [Path(p).stem for p in cons]
        self.is_consumable = [False] * len(refs) + [True] * len(cons)
        embs = [self._emb(np.array(Image.open(p).convert("RGB").resize((32, 32)))) for p in refs + cons]
        e = np.stack(embs)
        self.ref_emb = e / np.linalg.norm(e, axis=1, keepdims=True)
        self.id2cn = self._name_map()

    def _name_map(self):
        from item_names import build_item_cn
        return build_item_cn(ROOT)

    def _emb(self, rgb):
        x = np.asarray(Image.fromarray(rgb), np.float32).transpose(2, 0, 1) / 255.0
        x = resize_chw(x, [64, 64])
        x = (x - self.mean) / self.std
        return self.model.run(None, {"input": x[None].astype(np.float32)})[0][0]

    def _slot_box(self, col, row):
        ox, oy = GRID["origin"]; s = GRID["slot"]; p = GRID["pitch"]
        return ox + col * p, oy + row * p, s, s

    def recognize(self, image_path, conf_min=CONF_MIN):
        img = Image.open(image_path).convert("RGB")
        if img.size != (1920, 1080):
            raise ValueError(f"需要 1920x1080, 实际 {img.size}")
        out = []
        pad = GRID["inner_pad"]

        def occ(col, row):
            x, y, w, h = self._slot_box(col, row)
            return np.array(img.crop((x, y, x + w, y + h))).mean()

        # 第二列只在第一列 10 格全满时才存在; 否则不扫(避免蹭到旁边羁绊六边形, 误配成转职纹章)
        col0_full = all(occ(0, r) >= OCC_MIN for r in range(GRID["rows"]))
        cols = [0, 1] if col0_full else [0]
        for col in cols:
            for row in range(GRID["rows"]):
                x, y, w, h = self._slot_box(col, row)
                if occ(col, row) < OCC_MIN:
                    continue  # 空槽
                inner = np.array(img.crop((x + pad, y + pad, x + w - pad, y + h - pad)))
                e = self._emb(inner); e = e / np.linalg.norm(e)
                s = self.ref_emb @ e
                j = int(s.argmax()); conf = round(float(s[j]), 3)
                iid = self.ref_names[j]; consumable = self.is_consumable[j]
                ok = conf >= conf_min
                out.append({"col": col, "row": row, "conf": conf,
                            "consumable": bool(consumable and ok),   # 重铸/拆卸等: 识别到但主功能可跳过
                            "item_id": (None if consumable else iid) if ok else None,
                            "name": (iid if consumable else self.id2cn.get(iid, iid)) if ok else "unknown"})
        return out


if __name__ == "__main__":
    import sys
    bm = BenchMatcher()
    for it in bm.recognize(sys.argv[1]):
        tag = " [消耗品/跳过]" if it.get("consumable") else ""
        print(f"  ({it['col']},{it['row']}) {it['name']:28} conf={it['conf']}{tag}")
