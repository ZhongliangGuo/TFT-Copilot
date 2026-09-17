#!/usr/bin/env python3
"""抓取 tftable.cc (国服数据) 的当前版本 Meta, 生成 knowledge/meta_comps.md。

内容: 阵容梯度(核心卡/BIS装备/羁绊/完整阵容/神器纹章推荐/克制关系) +
羁绊强度榜 + 神器/纹章梯度 + 纹章合成配方(顺便补进 emblem_recipes.json)。

数据来源全部是页面内嵌的 __NEXT_DATA__ JSON, 不是解析 DOM, 相对抗改版;
若页面结构变化, 脚本会报错而不是产出错误数据。
"""
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SET_NUMBER = 18
PACK = ROOT / "data" / "packs" / f"set{SET_NUMBER}"
OUT_MD = ROOT / "knowledge" / "meta_comps.md"
OUT_RAW = PACK / "meta_raw.json"
OUT_RECIPES = ROOT / "data" / "emblem_recipes.json"
BASE = "https://tftable.cc"

# tftable 用装备现名, CDragon 保留旧 apiName —— 现名(归一化) -> 旧名(归一化)
NEW2OLD = {
    "protectorsvow": "frozenheart",
    "steadfastheart": "nightharvester",
    "edgeofnight": "guardianangel",
    "giantslayer": "madredsbloodrazor",
    "spiritvisage": "redemption",
    "krakensfury": "runaanshurricane",
    "redbuff": "rapidfirecannon",
    "nashorstooth": "leviathan",
    "evenshroud": "spectralgauntlet",
    "voidstaff": "statikkshiv",
    "strikersflail": "powergauntlet",
    "handofjustice": "unstableconcoction",
    "sunfirecape": "redbuff",              # 日炎斗篷 (CDragon 旧名 RedBuff, 与红霸符的旧名 RapidFireCannon 是两回事)
    "tacticianscrown": "forceofnature",    # 金铲铲冠冕 (铲+铲)
    "tacticianscape": "tacticiansring",    # 金锅铲冠冕 (铲+锅)
    "tacticiansshield": "tacticiansscepter",  # 金锅锅冠冕 (锅+锅)
    "emblemflorafatalisaugment": "emblemflorafatali",  # 绝命花妖纹章的海克斯版, 同名合并
    "hullcrusher": "tft9itemornnhullbreaker",  # tftable 现名 Hullcrusher = CDragon 碎舰者(旧名 Hullbreaker)
}


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")


def next_data(html: str) -> dict:
    m = re.search(r"__NEXT_DATA__[^>]*>(.*?)</script>", html, re.S)
    if not m:
        raise RuntimeError("页面没有 __NEXT_DATA__, tftable 可能改版了")
    return json.loads(m.group(1))


# ---------------- id -> 中文名 映射 ----------------

def norm(s: str) -> str:
    s = s.lower()
    for pre in ("tft5_item_", "tft_item_", "da_item_", "da_18_", "da_"):
        if s.startswith(pre):
            s = s[len(pre):]
    s = s.split("artifact_")[-1].replace("artifact", "")
    s = s.replace("_", "").rstrip("s")
    return s


class Mapper:
    def __init__(self):
        champs = json.loads((PACK / "champions.json").read_text(encoding="utf-8"))
        items = json.loads((PACK / "items.json").read_text(encoding="utf-8"))
        self.unit = {c["apiName"]: c["name"] for c in champs}
        self.item_by_api = {}
        self.item_by_norm = {}
        self.art_by_norm = {}
        for cat, arr in items.items():
            for i in arr:
                self.item_by_api[i["apiName"]] = i["name"]
                self.item_by_norm.setdefault(norm(i["apiName"]), i["name"])
                if cat == "artifact":
                    self.art_by_norm.setdefault(norm(i["apiName"]), i["name"])
        self.unmapped = set()

    def u(self, uid: str) -> str:
        name = self.unit.get(uid)
        if not name:
            self.unmapped.add("unit:" + uid)
        return name or uid

    def i(self, iid: str) -> str:
        if iid in self.item_by_api:
            return self.item_by_api[iid]
        n = norm(iid)
        # 神器命名空间优先且不做 NEW2OLD 改名映射 (改名表针对成装, 神器里真有同名旧装备如疾射火炮)
        if "artifact" in iid.lower() and n in self.art_by_norm:
            return self.art_by_norm[n]
        n = NEW2OLD.get(n, n)
        if n in self.item_by_norm:
            return self.item_by_norm[n]
        # 光明装: XxxRadiant
        if "radiant" in iid.lower():
            base = norm(iid.lower().replace("radiant", ""))
            base = NEW2OLD.get(base, base)
            if base in self.item_by_norm:
                return "光明版" + self.item_by_norm[base]
        self.unmapped.add("item:" + iid)
        return iid


# ---------------- 抓取 ----------------

def main():
    mp = Mapper()

    # 1) 首页: 阵容总表 + 神器/纹章梯度
    landing_all = next_data(fetch(BASE + "/"))["props"]["pageProps"]["landing"]
    landing = landing_all.get("zh-CN") or landing_all.get("en-US")

    # 2) meta 页: 羁绊强度 + 版本号 + 阵容中文名
    meta_pp = next_data(fetch(BASE + "/meta"))["props"]["pageProps"]
    patch = (meta_pp.get("compStrengthTrends", {}).get("dates") or ["?"])[-1]
    comp_names = {c["slug"]: c.get("display_name_cn") or c.get("display_name_en", c["slug"])
                  for c in meta_pp.get("compositionCards", [])}
    trait_rows = (meta_pp.get("metaStats", {}).get("top4Share", {}).get("traits") or []) + \
                 (meta_pp.get("metaStats", {}).get("winShare", {}).get("traits") or [])

    # 3) 每个阵容详情页
    slugs = []
    for g in landing.get("allComps", []):
        for c in g["comps"]:
            slugs.append((c["key"], c.get("avgPlacement"), c.get("top4Share")))
    print(f"版本 {patch}, 阵容 {len(slugs)} 个, 逐个抓取详情...")
    comps = []
    for slug, avg_p, top4 in slugs:
        try:
            pp = next_data(fetch(f"{BASE}/{slug}"))["props"]["pageProps"]
            comp = pp.get("composition")
            if not comp:
                print(f"  {slug}: 无 composition 数据, 跳过")
                continue
            comps.append(parse_comp(slug, comp, comp_names, mp, avg_p))
            print(f"  {comp_names.get(slug, slug)} ok")
        except Exception as e:
            print(f"  {slug}: 抓取失败 {e}")
        time.sleep(0.4)

    if len(comps) < 10:
        print(f"只成功解析 {len(comps)} 个阵容, 放弃写入 (旧 meta_comps.md 保留)", file=sys.stderr)
        sys.exit(1)

    # 4) 纹章配方 (CDragon 没有 S18 纹章合成信息)
    try:
        recipes = json.loads(fetch(BASE + "/data/__shared/item-recipes.json"))["itemRecipes"]
        cn_recipes = {mp.i(k): [mp.i(x) for x in v] for k, v in recipes.items()}
        OUT_RECIPES.write_text(json.dumps(cn_recipes, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"纹章配方已写入 {OUT_RECIPES} ({len(cn_recipes)} 条)")
    except Exception as e:
        cn_recipes = {}
        print(f"纹章配方抓取失败(不影响主流程): {e}")

    md = render_md(patch, comps, landing, trait_rows, cn_recipes, mp)
    OUT_MD.write_text(md, encoding="utf-8")
    OUT_RAW.write_text(json.dumps({"patch": patch, "comps": comps}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nMeta 知识层已写入 {OUT_MD} ({len(md)} 字符, 约 {len(md)//2} token)")
    if mp.unmapped:
        print(f"警告: {len(mp.unmapped)} 个 id 未映射到中文名 (原样保留): {sorted(mp.unmapped)[:10]}")


def parse_comp(slug, comp, comp_names, mp, avg_p):
    ov = comp.get("overview", {})
    core = []
    for cu in ov.get("coreUnits", []):
        builds = cu.get("builds") or []
        bis = [mp.i(it["id"]) for it in builds[0]["items"]] if builds else []
        core.append({"name": cu.get("nameCn") or mp.u(cu["id"]), "cost": cu.get("cost"), "bis": bis})
    core.sort(key=lambda x: -(x["cost"] or 0))

    variants = comp.get("variants") or []
    primary = next((v for v in variants if v.get("is_primary")), variants[0] if variants else {})
    team = [mp.u(u["id"]) for u in (primary.get("units") or [])]

    prio = sorted(comp.get("priorityMatrixRows") or [], key=lambda r: -(r.get("unitPriority") or 0))
    prio_names = [mp.u(r["unitId"]) for r in prio[:4]]

    arts = []
    for p in (comp.get("artifactPicks", {}).get("picks") or [])[:3]:
        carrier = mp.u(p["carrierUnitId"]) if p.get("carrierUnitId") else (p.get("carrierName") or "?")
        arts.append(f"{mp.i(p['artifactId'])}给{carrier}")

    embs = []
    for e in (comp.get("specialGroups", {}).get("emblems") or []):
        n = mp.i(e["itemId"])
        if n not in embs:
            embs.append(n)
    embs = embs[:3]

    countered = [comp_names.get(m["key"], m["key"]) for m in (comp.get("matchups", {}).get("counteredBy") or [])[:2]]

    return {
        "slug": slug,
        "name": comp_names.get(slug, comp.get("title", slug)),
        "avg_placement": round(avg_p, 2) if avg_p else None,
        "core": core[:4],
        "team": team,
        "priority": prio_names,
        "artifacts": arts,
        "emblems": embs,
        "countered_by": countered,
    }


def render_md(patch, comps, landing, trait_rows, cn_recipes, mp):
    L = [f"# S{SET_NUMBER} 当前版本 Meta (国服, 版本 {patch}, 来源 tftable.cc)", "",
         "以下是当前版本真实对局统计得出的阵容梯度。推荐阵容时优先在此范围内选择,",
         "根据玩家实际的装备/海克斯/来牌灵活调整; 玩家资产明显指向某阵容时不要硬套梯度。", "",
         "## 阵容梯度 (按平均名次, 越小越强)"]
    for c in sorted(comps, key=lambda x: x["avg_placement"] or 9):
        L.append(f"### {c['name']} (均名{c['avg_placement']})")
        for cu in c["core"]:
            bis = "+".join(cu["bis"]) if cu["bis"] else "(装备灵活)"
            L.append(f"- 核心 {cu['name']}({cu['cost']}费): {bis}")
        if c["team"]:
            L.append(f"- 完整阵容: {', '.join(c['team'])}")
        if c["priority"]:
            L.append(f"- 找牌优先: {' > '.join(c['priority'])}")
        if c["artifacts"]:
            L.append(f"- 神器优选: {'; '.join(c['artifacts'])}")
        if c["emblems"]:
            L.append(f"- 适配纹章: {', '.join(c['emblems'])}")
        if c["countered_by"]:
            L.append(f"- 被克制于: {', '.join(c['countered_by'])}")
        L.append("")

    # 羁绊强度
    seen = set()
    rows = []
    for t in trait_rows:
        key = (t.get("trait_name_cn"), t.get("units_required"))
        if key in seen or not t.get("trait_name_cn") or not t.get("units_required") \
                or "<" in str(t.get("trait_name_cn")):
            continue
        seen.add(key)
        rows.append(f"{t['trait_name_cn']}{t.get('units_required','')}")
    if rows:
        L += ["## 强势羁绊档位", ", ".join(rows), ""]

    # 神器/纹章梯度
    tiers = landing.get("itemTiers") or {}
    for cat, label in [("artifacts", "神器梯度"), ("emblems", "纹章梯度")]:
        tl = (tiers.get(cat) or {}).get("tiers") or []
        if tl:
            L.append(f"## {label}")
            for t in tl:
                names = []
                for x in t.get("items", []):
                    n = mp.i(x) if isinstance(x, str) else mp.i(x.get("id", ""))
                    if n not in names:
                        names.append(n)
                L.append(f"{t.get('id','?')}级: {', '.join(names)}")
            L.append("")

    # 纹章配方
    emblem_recipes = {k: v for k, v in cn_recipes.items() if k.endswith(("纹章", "冠冕"))}
    if emblem_recipes:
        L.append("## 转职纹章合成 (金铲铲/金锅锅+散件; 成装合成见基础数据)")
        for k, v in sorted(emblem_recipes.items()):
            L.append(f"{k}={'+'.join(v)}")
        L.append("")
    return "\n".join(L)


if __name__ == "__main__":
    main()
