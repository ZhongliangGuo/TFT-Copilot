#!/usr/bin/env python3
"""投放池核查: 交叉验证神器/光明装是否真的在掉落池中。

原理: CDragon 静态数据包含已注册但未投放的装备 (Riot 服务端按版本轮换掉落池,
且国服和外服池子可能不同步)。用两个来源交叉验证:
- tftable.cc (国服准确, 用户实战验证过) —— 主要依据, 覆盖神器
- tactics.tools (外服对局统计) —— 参考, 额外覆盖光明装

判定规则: 神器以 tftable 为准; 光明装只有 tactics.tools 有数据。
输出仅是报告, 不改文件 —— 池子变动需人工确认后更新 data/pool_overrides.json
再重跑 build_pack.py。误报若源于装备改名 (CDragon 保留旧 apiName), 在 SYNONYMS 补对照。
"""
import json
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SET_NUMBER = 18
PACK = ROOT / "data" / "packs" / f"set{SET_NUMBER}"

# CDragon 保留装备改名前的旧 apiName, 社区站用现名 —— 按分类做对照,
# 不能全局映射 (例: 神器池里真有一把疾射火炮, 而光明装的 RapidFirecannonRadiant 是红霸符的旧名)
SYNONYMS = {
    "radiant": {
        "frozenheart": "protectorsvow",      # 圣盾使的誓约 (旧: 冰心)
        "nightharvester": "steadfastheart",  # 坚定之心 (旧: 暗夜收割者)
        "guardianangel": "edgeofnight",      # 夜之锋刃 (旧: 守护天使)
        "trapclaw": "strikersflail",         # 强袭者的链枷 (旧: 陷阱之爪)
        "redemption": "spiritvisage",        # 振奋盔甲 (旧: 救赎)
        "runaanshurricane": "krakensfury",   # 海妖之怒 (旧: 卢安娜的飓风)
        "rapidfirecannon": "redbuff",        # 红霸符 (旧: 疾射火炮)
        "leviathan": "nashorstooth",         # 纳什之牙 (旧: 海兽祭司)
        "spectralgauntlet": "evenshroud",    # 薄暮法袍 (旧: 幽魂护手)
        "statikkshiv": "voidstaff",          # 虚空之杖 (旧: 斯塔缇克电刃)
    },
    "artifact": {
        # CDragon 碎舰者=Hullbreaker(TFT9_Item_OrnnHullbreaker), tftable 现名 Hullcrusher —— 同一神器改名
        "tft9itemornnhullbreaker": "hullcrusher",
    },
}


def norm(s: str, cat: str = "") -> str:
    s = s.lower()
    for pre in ("tft5_item_", "tft_item_", "da_item_", "da_"):
        if s.startswith(pre):
            s = s[len(pre):]
    s = s.split("artifact_")[-1].replace("artifact", "")
    s = s.replace("radiant", "").replace("_", "").rstrip("s")
    return SYNONYMS.get(cat, {}).get(s, s)


def match(a: str, pool: set[str]) -> bool:
    return any(a == b or a in b or b in a for b in pool)


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")


def main():
    items = json.loads((PACK / "items.json").read_text(encoding="utf-8"))
    ok = True

    # --- 主源: tftable.cc (国服) ---
    try:
        html = fetch("https://tftable.cc/item-tierlist")
        ids = set(re.findall(r"DA_[A-Za-z0-9_]+", html))
        cn_art = {norm(i, "artifact") for i in ids if "Artifact" in i}
        if len(cn_art) < 15:
            print(f"警告: tftable 只提取到 {len(cn_art)} 个神器, 页面结构可能变了, 跳过该源")
        else:
            print(f"tftable.cc (国服): 神器 {len(cn_art)}")
            for i in items["artifact"]:
                if not match(norm(i["apiName"], "artifact"), cn_art):
                    ok = False
                    print(f"疑似不在国服池 (候选加入 overrides): artifact/{i['name']} ({i['apiName']})")
            if abs(len(items["artifact"]) - len(cn_art)) > 1:
                ok = False
                print(f"数量偏差: 数据包神器 {len(items['artifact'])} vs tftable {len(cn_art)}, "
                      f"请人工复核 overrides (被排除的是否回归)")
    except Exception as e:
        print(f"tftable.cc 获取失败, 跳过: {e}")

    # --- 参考源: tactics.tools (外服, 覆盖光明装) ---
    try:
        html = fetch("https://tactics.tools/info/items")
        ids = set(re.findall(r"img/items_s\d+/([A-Za-z0-9_]+)\.png", html))
        tt_rad = {norm(i, "radiant") for i in ids if "radiant" in i.lower()}
        if len(tt_rad) < 15:
            print(f"警告: tactics.tools 只提取到 {len(tt_rad)} 个光明装, 页面结构可能变了, 跳过该源")
        else:
            print(f"tactics.tools (外服): 光明装 {len(tt_rad)}")
            for i in items["radiant"]:
                if not match(norm(i["apiName"], "radiant"), tt_rad):
                    ok = False
                    print(f"疑似不在池 (候选加入 overrides, 注意这是外服数据): radiant/{i['name']} ({i['apiName']})")
    except Exception as e:
        print(f"tactics.tools 获取失败, 跳过: {e}")

    if ok:
        print("核查通过: 数据包与实际投放池一致")


if __name__ == "__main__":
    main()
