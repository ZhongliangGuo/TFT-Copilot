#!/usr/bin/env python3
"""为所有实体名生成搜索索引: 拼音(全拼+首字母) + 社区简称/黑话(data/aliases_cn.json) 及其拼音。

别名表的 key 按前缀匹配官方名, 所以 "拉克丝" 的别名会应用到 "拉克丝 (地狱火)" 等变体。
"""
import json
from pathlib import Path

from pypinyin import lazy_pinyin, Style

ROOT = Path(__file__).resolve().parent.parent
SET_NUMBER = 18
PACK = ROOT / "data" / "packs" / f"set{SET_NUMBER}"
ALIAS_FILE = ROOT / "data" / "aliases_cn.json"


def pinyin_of(text: str) -> set[str]:
    full = "".join(lazy_pinyin(text)).lower()
    initials = "".join(lazy_pinyin(text, style=Style.FIRST_LETTER)).lower()
    return {full, initials} - {text.lower()}


def main():
    names = set()
    for f in ["champions.json", "augments.json"]:
        for e in json.loads((PACK / f).read_text(encoding="utf-8")):
            names.add(e["name"])
    for arr in json.loads((PACK / "items.json").read_text(encoding="utf-8")).values():
        for e in arr:
            names.add(e["name"])
    for e in json.loads((PACK / "traits.json").read_text(encoding="utf-8")):
        names.add(e["name"])

    aliases_raw = json.loads(ALIAS_FILE.read_text(encoding="utf-8")) if ALIAS_FILE.exists() else {}
    aliases = {k: v for k, v in aliases_raw.items() if not k.startswith("_")}

    index = {}
    matched_alias_keys = set()
    for n in sorted(names):
        keys = pinyin_of(n)
        for canon, alist in aliases.items():
            if n.startswith(canon):
                matched_alias_keys.add(canon)
                for a in alist:
                    keys.add(a.lower())
                    keys |= pinyin_of(a)
        index[n] = sorted(keys)

    unmatched = set(aliases) - matched_alias_keys
    if unmatched:
        print(f"警告: 别名表中 {len(unmatched)} 个 key 未匹配到任何实体(可能改名/移除): {', '.join(sorted(unmatched))}")

    out = PACK / "search_index.json"
    out.write_text(json.dumps(index, ensure_ascii=False, indent=0), encoding="utf-8")
    n_alias = sum(1 for k in index if any(not a.isascii() for a in index[k]))
    print(f"搜索索引已写入 {out} ({len(index)} 条, 其中 {n_alias} 条含中文别名)")


if __name__ == "__main__":
    main()
