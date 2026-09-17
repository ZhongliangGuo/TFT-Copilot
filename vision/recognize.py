#!/usr/bin/env python3
"""统一识别入口: 一张 1920x1080 截图 + 图像类型 -> 一份结构化 JSON, 接主功能。

按 mode 路由(不同类型从不同地方输入, 按不同规则解析):
  ally    我方棋盘: 阶段/等级/经验/金币/血量 + 棋盘(棋子/星级/装备) + 商店5牌 + 备战散装备
  augment 选海克斯: 阶段/血量 + 海克斯3选一
  enemy   敌方棋盘: 阶段(通用) + 被观察敌方血量 + 棋盘(棋子/星级, 不含装备), 位置镜像但血条仍在棋子上方
          装备识别只在 ally 模式跑, 见 infer.py 里的说明

全部本地: 棋子/星级/装备 = 训练的CV模型; 文本数字 = RapidOCR; 备战装备 = embedding匹配。

用法:
  python vision/recognize.py <截图> [ally|augment] [--json out.json]
或:
  from recognize import Recognizer
  Recognizer().recognize("shot.png", "ally")
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "hud"))


class Recognizer:
    def __init__(self):
        self._board = None   # lazy-load (onnx sessions are relatively heavy to init)
        self._bench = None
        self._hud = None

    @property
    def hud(self):
        if self._hud is None:
            from hud_ocr import HudOCR
            self._hud = HudOCR()
        return self._hud

    @property
    def board(self):
        if self._board is None:
            from infer import BoardRecognizer
            self._board = BoardRecognizer()
        return self._board

    @property
    def bench(self):
        if self._bench is None:
            from bench_items import BenchMatcher
            self._bench = BenchMatcher()
        return self._bench

    def recognize(self, image_path, mode="ally"):
        h = self.hud.parse(image_path, mode)
        if mode == "augment":
            return {"mode": "augment", "stage": h.get("stage"), "health": h.get("health"),
                    "augments": [h.get(f"augment_{i}") for i in (1, 2, 3)]}
        if mode == "enemy":
            # 敌方棋盘(上半屏, 位置镜像但血条仍在棋子上方): 阶段(通用) + 被观察敌方血量 + 棋盘(棋子/星级; 不识别装备)
            return {"mode": "enemy", "stage": h.get("stage"), "health": h.get("health"),
                    "board": self.board.recognize(image_path, mode="enemy")}
        # ally
        shop = [h.get(f"shop_{i}") for i in range(1, 6)]
        bench = [b for b in self.bench.recognize(image_path)]
        return {
            "mode": "ally",
            "stage": h.get("stage"), "level": h.get("level"), "xp": h.get("xp"),
            "gold": h.get("gold"), "health": h.get("health"), "streak": h.get("streak"),
            "board": self.board.recognize(image_path),
            "shop": shop,           # 5 格: 棋子名 str / {name,kind:sprite} / None(空)
            "bench_items": bench,   # 备战散装备(消耗品标 consumable)
        }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("mode", nargs="?", default="ally", choices=["ally", "augment", "enemy"])
    ap.add_argument("--json")
    args = ap.parse_args()
    out = Recognizer().recognize(args.image, args.mode)
    txt = json.dumps(out, ensure_ascii=False, indent=2)
    print(txt)
    if args.json:
        Path(args.json).write_text(txt, encoding="utf-8")


if __name__ == "__main__":
    main()
