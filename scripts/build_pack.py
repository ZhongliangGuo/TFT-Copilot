#!/usr/bin/env python3
"""从 CDragon 原始数据提取当前赛季数据包 (data/packs/setXX/)。

- 提取棋子/羁绊/装备/海克斯为结构化 JSON (按需注入层用)
- 生成 knowledge/resident_pack.md 常驻知识包 (每次请求都带)
- 覆盖前与旧数据包 diff, 打印版本变更报告

用法:
  python scripts/build_pack.py            # 构建 + diff 报告
  python scripts/build_pack.py --audit    # 只打印分类审计, 不写文件
"""
import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SET_NUMBER = 18
SET_MUTATOR = f"TFTSet{SET_NUMBER}"
LOCALE = "zh_cn"

RAW = ROOT / "data" / "raw" / f"cdragon_tft_{LOCALE}.json"
PACK_DIR = ROOT / "data" / "packs" / f"set{SET_NUMBER}"
KNOWLEDGE_DIR = ROOT / "knowledge"

# ---------------- 文本清洗 ----------------

TAG_RE = re.compile(r"<[^>]+>")
ICON_RE = re.compile(r"%i:[a-zA-Z0-9_]+%")
VAR_RE = re.compile(r"@([a-zA-Z0-9_.:*]+)@")


def fmt_num(v):
    if isinstance(v, float):
        if v == int(v):
            return str(int(v))
        return f"{v:.3g}"
    return str(v)


def clean_desc(desc: str, variables: dict | None = None) -> str:
    if not desc:
        return ""
    s = desc.replace("\\r\\n", " ").replace("\r\n", " ").replace("\\n", " ").replace("\n", " ")
    s = TAG_RE.sub("", s)
    s = ICON_RE.sub("", s)
    s = re.sub(r"\{\{[^}]*\}\}", "", s)
    if variables:
        lower = {k.lower(): v for k, v in variables.items() if isinstance(v, (int, float))}

        def sub(m):
            key = m.group(1)
            mul = 1
            if key.endswith("*100"):
                key, mul = key[:-4], 100
            v = lower.get(key.lower())
            if v is None:
                return m.group(0)
            return fmt_num(v * mul)

        s = VAR_RE.sub(sub, s)
    return re.sub(r"\s+", " ", s).strip()


# ---------------- 提取 ----------------

def load_raw():
    d = json.loads(RAW.read_text(encoding="utf-8"))
    set_data = next(sd for sd in d["setData"] if sd.get("mutator") == SET_MUTATOR)
    return d, set_data


def load_planner_codes():
    """character_id -> team_planner_code (阵容码用)"""
    f = ROOT / "data" / "raw" / "tftchampions-teamplanner.json"
    if not f.exists():
        return {}
    d = json.loads(f.read_text(encoding="utf-8"))
    return {e["character_id"]: e["team_planner_code"] for e in d.get(SET_MUTATOR, [])}


def extract_champions(set_data, audit=False):
    planner = load_planner_codes()
    missing_codes = []
    keep, dropped, seen = [], [], set()
    for c in set_data["champions"]:
        if c.get("name") in seen:
            continue
        cost = c.get("cost")
        traits = c.get("traits") or []
        # 可上场棋子: 1-5 费且有羁绊; 无羁绊的是训练假人/召唤物, 6+ 费是锻造器/宝箱等特殊商店物
        if not traits or cost not in (1, 2, 3, 4, 5):
            dropped.append(f"{c.get('name')}(cost={cost},traits={traits})")
            continue
        ab = c.get("ability") or {}
        st = c.get("stats") or {}
        keep.append({
            "name": c["name"],
            "apiName": c.get("apiName", ""),
            "cost": cost,
            "traits": traits,
            "ability_name": ab.get("name"),
            "ability_desc": clean_desc(ab.get("desc", ""), None),
            "stats": {
                "hp": st.get("hp"), "ad": st.get("damage"), "as": round(st.get("attackSpeed", 0), 2),
                "armor": st.get("armor"), "mr": st.get("magicResist"),
                "mana": f"{fmt_num(st.get('initialMana', 0))}/{fmt_num(st.get('mana', 0))}",
                "range": st.get("range"),
            },
            "icon": c.get("squareIcon") or c.get("tileIcon") or "",
            "planner_code": planner.get(c.get("apiName", "")),
        })
        if planner and keep[-1]["planner_code"] is None:
            missing_codes.append(c["name"])
        seen.add(c["name"])
    if missing_codes:
        print(f"提示: {len(missing_codes)} 个棋子无阵容码 (拉克丝变体等共享基础码属正常): {missing_codes[:6]}")
    mark_form_groups(keep, audit)
    if audit:
        print(f"[棋子] 保留 {len(keep)}, 剔除 {len(dropped)}:")
        for d_ in dropped:
            print("   -", d_)
    keep.sort(key=lambda x: (x["cost"], x["name"]))
    return keep


def mark_form_groups(champions, audit=False):
    """多形态棋子(如 拉克丝): 同基础名带括号后缀的条目分组。
    基础条目获得 forms(形态列表) 和 all_traits(所有可能羁绊, 供 GUI 筛选);
    形态条目获得 form_of 指回基础名(GUI 默认隐藏, 数据保留供按需注入)。"""
    by_base: dict[str, list] = {}
    for c in champions:
        by_base.setdefault(c["name"].split(" (")[0], []).append(c)
    for base, group in by_base.items():
        if len(group) < 2:
            continue
        base_entry = next((c for c in group if c["name"] == base), None)
        variants = [c for c in group if c["name"] != base]
        for v in variants:
            v["form_of"] = base
        if base_entry:
            base_entry["forms"] = [v["name"] for v in variants]
            base_entry["all_traits"] = sorted({t for c in group for t in c["traits"]})
        if audit:
            print(f"[多形态] {base}: {len(variants)} 个形态")


def extract_traits(set_data, champions):
    used = set()
    for c in champions:
        used.update(c["traits"])
    out = []
    for t in set_data["traits"]:
        effects = []
        for e in t.get("effects", []):
            named_vars = {k: v for k, v in (e.get("variables") or {}).items() if not k.startswith("{")}
            effects.append({"minUnits": e.get("minUnits"), "style": e.get("style"), "variables": named_vars})
        out.append({
            "name": t["name"],
            "apiName": t.get("apiName", ""),
            "desc": clean_desc(t.get("desc", "")),
            "thresholds": [e["minUnits"] for e in effects],
            "effects": effects,
            "in_use": t["name"] in used,
            "icon": t.get("icon", ""),
        })
    out.sort(key=lambda x: (not x["in_use"], x["name"]))
    return out


COMPONENT_APINAMES = {
    "TFT_Item_BFSword", "TFT_Item_RecurveBow", "TFT_Item_NeedlesslyLargeRod",
    "TFT_Item_TearOfTheGoddess", "TFT_Item_ChainVest", "TFT_Item_NegatronCloak",
    "TFT_Item_GiantsBelt", "TFT_Item_SparringGloves", "TFT_Item_Spatula", "TFT_Item_FryingPan",
}


def classify_item(it):
    """分类一个已通过赛季白名单的装备条目。
    apiName 模式依据 (对 S18 白名单全量核对过):
    - 真神器都是 *Artifact_* (DA_Artifact_/TFT_Item_Artifact_); "神器化"(DA_Artifactinate18,
      自然之灵机制物)和"神器装备"(锻造砧)不含 'Artifact_', 自然排除
    - 纹章以名字结尾判断即可, 白名单已保证是本赛季启用的 (含幻影/绝命花妖这类纯纹章羁绊)
    """
    api = it.get("apiName", "")
    name = it.get("name") or ""
    comp = it.get("composition") or []
    if it.get("isAugment"):
        return None  # 海克斯单独处理
    # 占位名/坏条目
    if name.lower().startswith("tft_item") or "锻造器" in name or name.endswith("。"):
        return None
    if api in COMPONENT_APINAMES:
        return "component"
    if name.endswith("纹章"):
        return "emblem"
    if "Artifact_" in api:
        return "artifact"
    # 传统 Ornn 神器: 部分神器只有旧 apiName (TFT4/TFT9_Item_Ornn*), 没有 DA_Artifact_ 兄弟
    # (如碎舰者/兰顿之兆), 不含 'Artifact_' 会被漏分类。Ornn 命名空间历来就是神器,
    # 排除锻造砧/随机神器这两个 Assist 物件即可。是否真在投放池交给 pool_overrides+check_pool。
    if "_Ornn" in api and "Assist" not in api:
        return "artifact"
    if "Support" in api:
        return "support"
    if api.startswith("TFT5_Item_") and api.endswith("Radiant"):
        return "radiant"
    if api.startswith("TFT_Item_") and len(comp) == 2:
        return "craftable"
    return None


def _art_root(api):
    """神器根名(去 TFT/Ornn/Artifact_ 等命名空间), 用来判断"同一件神器的不同 apiName"。"""
    s = re.sub(r"(TFT\d*_Item_|TFT_Item_|DA_|Artifact_|Component_|Ornn)", "", api or "")
    return re.sub(r"[^a-z0-9]", "", s.lower())


def extract_items(d, set_data, audit=False):
    lookup = {it["apiName"]: it for it in d["items"]}
    # 赛季白名单: setData.items 是本赛季实际启用的装备/物件清单,
    # 全局表里同名的旧赛季版本一律不进数据包
    whitelist = set(set_data.get("items") or [])
    # 已有 Artifact_ 规范条目的神器根名: 传统 Ornn 版是同一件神器的重复(可能中文名还不同,
    # 如 视界专注/狙击手的专注 都是 HorizonFocus), 按根名去重, 只留规范的 Artifact_ 版
    art_roots = {_art_root(a) for a in whitelist
                 if "Artifact_" in a and not lookup.get(a, {}).get("isAugment")}
    buckets = {"component": [], "craftable": [], "emblem": [], "artifact": [], "support": [], "radiant": []}
    seen_names = set()
    # 同名神器有 DA_Artifact_ 和旧 Ornn 两种 apiName 时, 让 Artifact_ 版先被处理成为规范条目
    # (meta 映射按 DA_Artifact_ 的归一名匹配, 换成旧 Ornn 名会导致 fetch_meta 映射不到)。
    for it in sorted(d["items"], key=lambda x: 0 if "Artifact_" in x.get("apiName", "") else 1):
        if it.get("apiName") not in whitelist:
            continue
        cat = classify_item(it)
        if not cat:
            continue
        # 传统 Ornn 神器若已有 Artifact_ 规范条目(同一件), 跳过, 避免重复收录
        if cat == "artifact" and "Artifact_" not in it["apiName"] and _art_root(it["apiName"]) in art_roots:
            continue
        name = it.get("name") or ""
        if not name or name.startswith("TFT_") or name in seen_names:
            continue
        seen_names.add(name)
        comp_names = [lookup.get(x, {}).get("name", x) for x in (it.get("composition") or [])]
        buckets[cat].append({
            "name": name,
            "apiName": it["apiName"],
            "desc": clean_desc(it.get("desc", ""), it.get("effects")),
            "composition": comp_names,
            "traits": it.get("associatedTraits") or [],
            "unique": bool(it.get("unique")),
            "icon": it.get("icon", ""),
        })
    apply_pool_overrides(buckets)
    enrich_emblem_recipes(buckets)
    for v in buckets.values():
        v.sort(key=lambda x: x["name"])
    if audit:
        for k, v in buckets.items():
            print(f"[装备/{k}] {len(v)}: {', '.join(x['name'] for x in v[:12])}{' ...' if len(v) > 12 else ''}")
    return buckets


def enrich_emblem_recipes(buckets):
    """CDragon 的 S18 纹章条目缺合成配方; 若 fetch_meta.py 抓过 tftable 的
    data/emblem_recipes.json (中文名 key), 用它补上空的 composition。"""
    f = ROOT / "data" / "emblem_recipes.json"
    if not f.exists():
        return
    recipes = json.loads(f.read_text(encoding="utf-8"))
    filled = 0
    for arr in buckets.values():
        for i in arr:
            if not i["composition"] and i["name"] in recipes:
                i["composition"] = recipes[i["name"]]
                filled += 1
    if filled:
        print(f"纹章配方回填: {filled} 条 (来自 data/emblem_recipes.json)")


def apply_pool_overrides(buckets):
    """剔除已注册但本版本不投放的装备 (data/pool_overrides.json, 手动维护)。
    覆盖表中已经不存在于数据包的名字会告警 —— 说明版本更新改了池子, 该核查覆盖表了。"""
    ov_file = ROOT / "data" / "pool_overrides.json"
    if not ov_file.exists():
        return
    exclude = json.loads(ov_file.read_text(encoding="utf-8")).get("exclude", {})
    for cat, names in exclude.items():
        if cat not in buckets:
            print(f"警告: pool_overrides 中未知分类 {cat}")
            continue
        have = {i["name"] for i in buckets[cat]}
        for n in names:
            if n not in have:
                print(f"警告: pool_overrides 的 {cat}/{n} 不在数据包中 (可能已改名/官方移除), 请核查覆盖表")
        buckets[cat] = [i for i in buckets[cat] if i["name"] not in set(names)]


# 海克斯品质的哈希 tag (由图标路径线索关联分析得出, 全量验证 402/402 无歧义)
AUGMENT_TIER_TAGS = {"{d11fd6d5}": "银", "{ce1fd21c}": "金", "{cf1fd3af}": "彩"}


def _unresolved(it):
    """clean_desc 回填后仍残留的 @占位符@ 数量。越少说明该 apiName 的数值越全。"""
    return len(VAR_RE.findall(clean_desc(it.get("desc", ""), it.get("effects"))))


def extract_augments(d, set_data, audit=False):
    """同名海克斯在 CDragon 里常有多份定义: DA_* 与 TFT_Augment_*(含往季 TFTx_) 等。
    DA_* 往往带当前实时数值, 但 effects 的键被哈希成 {4b65cc7c}, 描述里的 @数值@
    无法回填 —— 于是"扩展包 / 扩展包+ / 扩展包++"三档看起来描述完全一样(都是占位符),
    Agent 无从区分。策略: 按名分组, 若首选(通常是带实时数值的 DA_)本身能完整回填就用它
    (不换, 避免误取往季旧数值); 仅当它残留占位符时, 才挑同名里占位符最少的变体
    (并列优先规范的 TFT_Augment_ 前缀)。"""
    lookup = {it["apiName"]: it for it in d["items"]}
    groups, order, missing = {}, [], []
    for api in set_data.get("augments", []):
        it = lookup.get(api)
        if not it or not it.get("name"):
            missing.append(api)
            continue
        name = it["name"]
        if name not in groups:
            groups[name] = []
            order.append(name)
        groups[name].append(api)

    out, still = [], 0
    for name in order:
        apis = groups[name]
        cur = apis[0]
        if _unresolved(lookup[cur]) == 0:
            pick = cur
        else:
            pick = min(apis, key=lambda a: (_unresolved(lookup[a]),
                                            0 if a.startswith("TFT_Augment") else 1))
        it = lookup[pick]
        desc = clean_desc(it.get("desc", ""), it.get("effects"))
        if VAR_RE.search(desc):
            still += 1
        tiers = [AUGMENT_TIER_TAGS[t] for t in (it.get("tags") or []) if t in AUGMENT_TIER_TAGS]
        out.append({
            "name": name,
            "apiName": pick,
            "tier": tiers[0] if tiers else None,
            "desc": desc,
            "traits": it.get("associatedTraits") or [],
            "icon": it.get("icon", ""),
        })
    out.sort(key=lambda x: x["name"])
    if audit:
        print(f"[海克斯] 提取 {len(out)}, 无数据 {len(missing)}, 仍有未回填占位符 {still} "
              f"(CDragon 静态数据未给该数值, 多为 @StartingGold@ 等运行时/全局量)")
        trait_augs = [a for a in out if a["traits"]]
        print(f"   其中羁绊转职类 {len(trait_augs)}: {', '.join(a['name'] for a in trait_augs[:10])} ...")
    return out


# ---------------- 常驻知识包 ----------------

STYLE_NAME = {1: "铜", 3: "银", 4: "金+", 5: "金", 6: "彩"}


def build_resident_pack(champions, traits, items, version):
    lines = [
        f"# S{SET_NUMBER} 赛季数据速查 (数据版本: {version})",
        "",
        "## 棋子一览 (名字|费用|羁绊)",
    ]
    for c in champions:
        if c.get("form_of"):
            continue  # 形态变体不单列, 合并进基础条目
        if c.get("forms"):
            opts = "/".join(f.split(" (")[1].rstrip(")") for f in c["forms"])
            lines.append(f"{c['name']}|{c['cost']}费|{'/'.join(c['traits'])}|形态可选({opts}之一), "
                         f"买第一个后商店中其余同名棋子锁定为相同形态")
        else:
            lines.append(f"{c['name']}|{c['cost']}费|{'/'.join(c['traits'])}")
    lines += ["", "## 羁绊阈值与效果"]
    for t in traits:
        if not t["in_use"]:
            continue
        th = "/".join(str(x) for x in t["thresholds"])
        lines.append(f"### {t['name']} ({th})")
        lines.append(t["desc"])
    lines += ["", "## 成装合成表 (装备=散件+散件)"]
    for it in items["craftable"]:
        comp = "+".join(it["composition"])
        lines.append(f"{it['name']}={comp}")
    lines += ["", "## 散件"]
    for it in items["component"]:
        lines.append(f"{it['name']}: {it['desc'][:60]}")
    return "\n".join(lines) + "\n"


# ---------------- Diff 报告 ----------------

def diff_packs(old_champs, new_champs):
    report = []
    old = {c["name"]: c for c in old_champs}
    new = {c["name"]: c for c in new_champs}
    for name in sorted(new.keys() - old.keys()):
        report.append(f"+ 新增棋子: {name} ({new[name]['cost']}费, {'/'.join(new[name]['traits'])})")
    for name in sorted(old.keys() - new.keys()):
        report.append(f"- 移除棋子: {name}")
    for name in sorted(old.keys() & new.keys()):
        o, n = old[name], new[name]
        if o["cost"] != n["cost"]:
            report.append(f"~ {name}: 费用 {o['cost']} -> {n['cost']}")
        if o["traits"] != n["traits"]:
            report.append(f"~ {name}: 羁绊 {'/'.join(o['traits'])} -> {'/'.join(n['traits'])}")
    return report


# ---------------- 主流程 ----------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", action="store_true", help="只打印分类审计, 不写文件")
    args = ap.parse_args()

    d, set_data = load_raw()
    champions = extract_champions(set_data, audit=args.audit)
    traits = extract_traits(set_data, champions)
    items = extract_items(d, set_data, audit=args.audit)
    augments = extract_augments(d, set_data, audit=args.audit)

    if args.audit:
        print(f"\n合计: 棋子 {len(champions)}, 羁绊 {sum(1 for t in traits if t['in_use'])}(使用中)/{len(traits)}, "
              f"海克斯 {len(augments)}")
        return

    # diff 旧包
    old_file = PACK_DIR / "champions.json"
    if old_file.exists():
        old_champs = json.loads(old_file.read_text(encoding="utf-8"))
        report = diff_packs(old_champs, champions)
        print("=== 版本变更报告 ===" if report else "=== 无棋子变更 ===")
        for line in report:
            print(line)

    PACK_DIR.mkdir(parents=True, exist_ok=True)
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    version = json.dumps({"built_at": __import__("datetime").date.today().isoformat(),
                          "set": SET_NUMBER, "locale": LOCALE}, ensure_ascii=False)
    (PACK_DIR / "champions.json").write_text(json.dumps(champions, ensure_ascii=False, indent=1), encoding="utf-8")
    (PACK_DIR / "traits.json").write_text(json.dumps(traits, ensure_ascii=False, indent=1), encoding="utf-8")
    (PACK_DIR / "items.json").write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    (PACK_DIR / "augments.json").write_text(json.dumps(augments, ensure_ascii=False, indent=1), encoding="utf-8")
    (PACK_DIR / "version.json").write_text(version, encoding="utf-8")

    resident = build_resident_pack(champions, traits, items,
                                   json.loads(version)["built_at"])
    (KNOWLEDGE_DIR / "resident_pack.md").write_text(resident, encoding="utf-8")
    print(f"\n数据包已写入 {PACK_DIR}")
    print(f"常驻知识包 {KNOWLEDGE_DIR / 'resident_pack.md'} ({len(resident)} 字符, 约 {len(resident)//2} token)")


if __name__ == "__main__":
    main()
