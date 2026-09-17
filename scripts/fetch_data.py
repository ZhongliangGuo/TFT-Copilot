#!/usr/bin/env python3
"""下载 CDragon TFT 原始数据到 data/raw/。

用法: python scripts/fetch_data.py [--locale zh_cn]
"""
import argparse
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CDRAGON_URL = "https://raw.communitydragon.org/latest/cdragon/tft/{locale}.json"
TEAMPLANNER_URL = ("https://raw.communitydragon.org/latest/plugins/"
                   "rcp-be-lol-game-data/global/default/v1/tftchampions-teamplanner.json")


def fetch(locale: str) -> Path:
    url = CDRAGON_URL.format(locale=locale)
    out = ROOT / "data" / "raw" / f"cdragon_tft_{locale}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    print(f"下载 {url} ...")
    req = urllib.request.Request(url, headers={"User-Agent": "tft-assistant/1.0"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = resp.read()
    # 校验是合法 JSON 再落盘
    json.loads(data)
    out.write_bytes(data)
    print(f"已保存 {out} ({len(data) / 1e6:.1f} MB)")
    # 阵容码用的 team_planner_code 数据
    tp_out = out.parent / "tftchampions-teamplanner.json"
    req2 = urllib.request.Request(TEAMPLANNER_URL, headers={"User-Agent": "tft-assistant/1.0"})
    with urllib.request.urlopen(req2, timeout=60) as resp:
        tp_data = resp.read()
    json.loads(tp_data)
    tp_out.write_bytes(tp_data)
    print(f"已保存 {tp_out} ({len(tp_data) / 1e3:.0f} KB)")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--locale", default="zh_cn")
    args = ap.parse_args()
    try:
        fetch(args.locale)
    except Exception as e:
        print(f"下载失败: {e}", file=sys.stderr)
        sys.exit(1)
