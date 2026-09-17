#!/usr/bin/env python3
"""HUD 文本/数字识别 (本地 RapidOCR, 离线). 从 1920x1080 截图按固定区域读:
阶段/等级/经验/金币/血量(通用+我方) + 海克斯名(选海克斯) + 商店牌(我方)。

设计:
- 固定区域坐标见 hud/regions.json (用户手圈)。
- 紧贴的框一律用 **rec-only**(关检测), 数字放大5x, 正则抽数字。RapidOCR 的检测会把
  云顶花体小数字切碎, 关掉它直接识别整块最稳。
- 文本(海克斯/棋子名) OCR 后 **fuzzy-match 到已知词表**(augments.json / champions.json)自动纠错。
- 血量: 右侧竖条做检测, 取"纯数字里字号(框高)最大"的 = 自己(放大头像)。
- bench_items(备战散装备)是图标不是文字, 本模块不处理, 另做图标识别。

用法:
  from hud_ocr import HudOCR
  hud = HudOCR()
  hud.parse("screenshot.png", mode="ally")     # 我方棋盘
  hud.parse("screenshot.png", mode="augment")  # 选海克斯
"""
import json
import re
import sys
import difflib
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent


class HudOCR:
    def __init__(self):
        from rapidocr_onnxruntime import RapidOCR
        self.ocr = RapidOCR()
        self.regions = json.loads((HERE / "regions.json").read_text(encoding="utf-8"))
        # 词表
        ch = json.loads((ROOT / "data" / "packs" / "set18" / "champions.json").read_text(encoding="utf-8"))
        self.champion_names = sorted({c["name"] for c in ch})
        aug = json.loads((ROOT / "data" / "packs" / "set18" / "augments.json").read_text(encoding="utf-8"))
        self.augment_names = sorted({a["name"] for a in aug})
        # 海克斯升级档: 同一"家族"有 名/名+/名++ 三档(如 扩展包/扩展包+/扩展包++)。
        # 建 基名 -> {加号数: 全名} 映射: 中文基名信息量大好匹配, '+/++' 单独按加号数定档,
        # 避免整串 fuzzy 时把 ++ 误判成 +。全角＋归一到半角+。
        self.aug_families: dict[str, dict[int, str]] = {}
        for n in self.augment_names:
            nn = n.replace("＋", "+").replace("﹢", "+")
            base = nn.rstrip("+")
            self.aug_families.setdefault(base, {})[len(nn) - len(base)] = n
        # 自然仙灵(赛季机制): shop 第5格可能是仙灵卡而非棋子
        sp = json.loads((ROOT / "data" / "packs" / "set18" / "sprites.json").read_text(encoding="utf-8"))
        self.sprite_names = sorted({s["name"] for s in sp})

    # ---- 底层 ----
    def _rec(self, bgr_or_rgb):
        """rec-only: 把整块当一行识别, 返回文本(可能空)。"""
        r, _ = self.ocr(bgr_or_rgb, use_det=False, use_cls=False, use_rec=True)
        return r[0][0] if r else ""

    @staticmethod
    def _up(crop, f=5):
        g = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
        g = cv2.resize(g, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
        return cv2.cvtColor(g, cv2.COLOR_GRAY2RGB)

    @staticmethod
    def _crop(img, box):
        x, y, w, h = box
        return np.array(img.crop((x, y, x + w, y + h)))

    def _fuzzy(self, text, vocab, cutoff=0.3):
        text = (text or "").strip()
        if not text:
            return None
        m = difflib.get_close_matches(text, vocab, n=1, cutoff=cutoff)
        return m[0] if m else text  # 匹配不到就返回原文(留痕)

    @staticmethod
    def _best(text, vocab):
        """返回 (最佳名, 相似度)。"""
        text = (text or "").strip()
        if not text:
            return (None, 0.0)
        best, br = None, 0.0
        for v in vocab:
            r = difflib.SequenceMatcher(None, text, v).ratio()
            if r > br:
                best, br = v, r
        return (best, round(br, 3))

    # ---- 各类型解析 ----
    def _digits(self, img, box):
        return re.sub(r"\D", "", self._rec(self._up(self._crop(img, box))))

    def _parse_field(self, img, name, spec):
        t = spec["type"]
        if t == "stage":
            raw = self._rec(self._up(self._crop(img, spec["box"])))
            m = re.search(r"(\d)\s*[-/]\s*(\d)", raw)
            return f"{m.group(1)}-{m.group(2)}" if m else None
        if t == "level":
            d = self._digits(img, spec["box"])
            return int(d) if d else None
        if t == "gold":
            d = self._digits(img, spec["box"])
            return int(d) if d else 0            # 读不到数字=打光了=0
        if t == "frac":                          # 经验 a/b
            raw = self._rec(self._up(self._crop(img, spec["box"])))
            m = re.search(r"(\d+)\s*/\s*(\d+)", raw)
            return [int(m.group(1)), int(m.group(2))] if m else None
        if t == "health":
            return self._health(img, spec["box"])
        if t == "enemy_health":
            return self._enemy_health(img, spec["box"])
        if t == "streak":
            return self._streak(img, spec["box"])
        if t == "champion":
            return self._fuzzy(self._rec(self._crop(img, spec["box"])), self.champion_names) or None
        if t == "champion_or_sprite":     # shop 第5格: 棋子 或 自然仙灵
            raw = self._rec(self._crop(img, spec["box"]))
            if not raw.strip():
                return None
            cn, cr = self._best(raw, self.champion_names)
            sn, sr = self._best(raw, self.sprite_names)
            return ({"name": sn, "kind": "sprite"} if sr >= cr
                    else {"name": cn, "kind": "champion"})
        if t == "augment":
            return self._match_augment(img, spec["box"])
        if t == "item_icons":
            return "TODO_icon_recognition"       # 备战散装备, 另做
        return None

    @staticmethod
    def _norm_plus(s):
        """把尾部的加号族统一成半角 '+' , 便于数出 +/++ 升级档:
        - 全角＋、﹢
        - 花体 '++' 里第二个 + 常被 OCR 误读成 '十'(字形就是个加号); 海克斯名正文里
          没有以 '十' 结尾的, 只归一化结尾这一串, 不碰正文中合法的 十/士/干。"""
        return re.sub(r"[+＋﹢十]+$", lambda m: "+" * len(m.group(0)), (s or "").strip())

    def _match_augment(self, img, box):
        """海克斯名识别 + 纠错。中文基名 fuzzy 到家族(稳), 升级档 '+/++' 由加号字形数量定。
        花体小加号 OCR 常漏读/误读, 故原图与放大各读一次, 同家族里取更大的加号数。"""
        crop = self._crop(img, box)
        cands = []                       # (基名匹配度, 家族基名, 加号数, 原文)
        for scale in (1, 3):
            txt = self._rec(crop if scale == 1 else self._up(crop, scale))
            nn = self._norm_plus(txt)
            base = nn.rstrip("+").strip()
            if not base:
                continue
            fam, r = self._best(base, list(self.aug_families))
            cands.append((r, fam, len(nn) - len(nn.rstrip("+")), txt))
        if not cands:
            return None
        cands.sort(key=lambda c: c[0], reverse=True)
        r, fam, plus, txt = cands[0]
        if r < 0.5 or fam is None:       # 家族都对不上, 退回整串 fuzzy 留痕
            return self._fuzzy(txt, self.augment_names)
        variants = self.aug_families[fam]
        plus = max([plus] + [p for _, f, p, _ in cands if f == fam])  # 加号常被漏读, 取最多的
        if plus in variants:
            return variants[plus]
        return variants[min(variants, key=lambda p: abs(p - plus))]   # 该档不存在取最近档

    def _streak(self, img, box):
        """连胜/败: 左半火焰颜色(橙=胜/蓝=败) + 右半数字 -> 'W3'/'L2'。"""
        x, y, w, h = box
        num = re.sub(r"\D", "", self._rec(self._up(np.array(img.crop((x + w // 2 - 4, y, x + w, y + h))))))
        if not num:
            return None
        flame = np.array(img.crop((x, y, x + w // 2, y + h))).reshape(-1, 3).astype(float)
        bright = flame[flame.max(1) > 90]        # 取亮的火焰像素
        if len(bright) == 0:
            return None
        r, g, b = bright.mean(0)
        return ("W" if r > b + 15 else "L") + num

    def _enemy_health(self, img, box):
        """被观察敌方的血量(看敌方棋盘时)。据观察: 使用助手的玩家自己字号最大;
        其余敌方字号相同, 但"被观察的那个"整体向左偏移。故: 排除最大(=玩家自己),
        剩下敌方里取最靠左(x 最小)的数字。
        ⚠ 首版启发, 阈值 0.9 和"最左=被观察者"需真敌方截图校准。"""
        crop = self._crop(img, box)
        res, _ = self.ocr(crop)  # det on
        dets = []
        for pts, txt, cf in (res or []):
            digits = re.sub(r"\D", "", txt)
            if digits and digits == txt.strip().replace(" ", ""):
                h = abs(pts[2][1] - pts[0][1])
                x = min(p[0] for p in pts)
                dets.append((h, x, int(digits)))
        if not dets:
            return None
        maxh = max(d[0] for d in dets)
        enemies = [d for d in dets if d[0] < maxh * 0.9]   # 明显小于最大字号 = 敌方
        if not enemies:
            return None
        return min(enemies, key=lambda d: d[1])[2]          # 最靠左 = 被观察的敌方

    def _health(self, img, box):
        """右侧竖条: 检测所有文本, 取纯数字里框最高的(自己=放大头像)。"""
        crop = self._crop(img, box)
        res, _ = self.ocr(crop)  # det on
        best = None
        for pts, txt, cf in (res or []):
            digits = re.sub(r"\D", "", txt)
            if digits and digits == txt.strip().replace(" ", ""):  # 纯数字
                h = abs(pts[2][1] - pts[0][1])
                if best is None or h > best[0]:
                    best = (h, int(digits))
        return best[1] if best else None

    # ---- 入口 ----
    def parse(self, image_path, mode="ally"):
        img = Image.open(image_path).convert("RGB")
        if img.size != (1920, 1080):
            raise ValueError(f"需要 1920x1080 截图, 实际 {img.size}")
        out = {"mode": mode}
        for name, spec in self.regions[mode].items():
            out[name] = self._parse_field(img, name, spec)
        return out


if __name__ == "__main__":
    import sys
    hud = HudOCR()
    mode = sys.argv[2] if len(sys.argv) > 2 else "ally"
    print(json.dumps(hud.parse(sys.argv[1], mode), ensure_ascii=False, indent=2))
