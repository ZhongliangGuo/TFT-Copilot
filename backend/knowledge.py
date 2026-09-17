"""知识分层管理:
- 常驻层: 通用策略 + 赛季速查表 (进 system prompt, 字节稳定以命中 DeepSeek 上下文缓存)
- 按需层: 根据 GUI 选择注入相关实体的详细数据 (进 user message, 每实体一局只注入一次)
- 工具层: lookup_details 供模型主动查询
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class Knowledge:
    def __init__(self, set_number: int = 18):
        self.set_number = set_number
        pack = ROOT / "data" / "packs" / f"set{set_number}"
        self.champions = json.loads((pack / "champions.json").read_text(encoding="utf-8"))
        self.traits = json.loads((pack / "traits.json").read_text(encoding="utf-8"))
        # 羁绊召唤的可放置棋子(如永恒之森植物), 非商店购买, CDragon无独立记录, 手工维护
        summon_file = pack / "summoned_units.json"
        self.summons = (json.loads(summon_file.read_text(encoding="utf-8")).get("units", [])
                        if summon_file.exists() else [])
        self.items = json.loads((pack / "items.json").read_text(encoding="utf-8"))
        self.augments = json.loads((pack / "augments.json").read_text(encoding="utf-8"))
        self.version = json.loads((pack / "version.json").read_text(encoding="utf-8"))

        self.general = (ROOT / "knowledge" / "general_strategy.md").read_text(encoding="utf-8")
        self.resident = (ROOT / "knowledge" / "resident_pack.md").read_text(encoding="utf-8")
        meta_file = ROOT / "knowledge" / "meta_comps.md"
        self.meta = meta_file.read_text(encoding="utf-8") if meta_file.exists() else ""

        # 全实体索引: name -> (kind, entry)
        self.index: dict[str, tuple[str, dict]] = {}
        for c in self.champions:
            self.index[c["name"]] = ("champion", c)
        for s in self.summons:
            self.index.setdefault(s["name"], ("summon", s))
        for t in self.traits:
            self.index.setdefault(t["name"], ("trait", t))
        for cat, arr in self.items.items():
            for it in arr:
                self.index.setdefault(it["name"], (f"item/{cat}", it))
        for a in self.augments:
            self.index.setdefault(a["name"], ("augment", a))

        # 简称/黑话 -> 官方名 (别名表 key 按前缀匹配, 变体棋子继承)
        self.alias: dict[str, list[str]] = {}
        alias_file = ROOT / "data" / "aliases_cn.json"
        if alias_file.exists():
            raw = json.loads(alias_file.read_text(encoding="utf-8"))
            for canon, alist in raw.items():
                if canon.startswith("_"):
                    continue
                targets = [n for n in self.index if n.startswith(canon)]
                for a in alist:
                    for t in targets:
                        self.alias.setdefault(a, []).append(t)

    # ---------- 常驻层 ----------
    def system_prompt(self) -> str:
        parts = [
            "你是云顶之弈(TFT)实战教练, 辅助玩家在对局中做决策。玩家在国服, 当前是 S18 赛季。",
            "你只依据下面提供的当季数据推理; 训练记忆中的旧赛季数据一律不可信。"
            "需要某个棋子技能/装备/海克斯的详细数值而下文未提供时, 调用 lookup_details 工具查询。"
            "给出终局阵容前必须调用 verify_comp 工具核验羁绊档位(见下文铁律)。",
            "", self.general, "", self.resident,
        ]
        if self.meta:
            parts += ["", self.meta]
        return "\n".join(parts)

    # ---------- 按需层 ----------
    def detail_text(self, name: str) -> str | None:
        hit = self.index.get(name)
        if not hit:
            return None
        kind, e = hit
        if kind == "champion":
            st = e["stats"]
            def n(v):
                return int(v) if isinstance(v, float) and v == int(v) else v
            forms = f" | 可选形态: {', '.join(e['forms'])} (各形态羁绊/技能加成不同, 需要细节可查询具体形态)" if e.get("forms") else ""
            return (f"[棋子]{e['name']} {e['cost']}费 {'/'.join(e['traits'])} | "
                    f"生命{n(st['hp'])} 攻击{n(st['ad'])} 攻速{st['as']} 护甲{n(st['armor'])} 魔抗{n(st['mr'])} "
                    f"蓝量{st['mana']} 射程{st['range']} | 技能[{e['ability_name']}]: {e['ability_desc']}{forms}")
        if kind == "summon":
            return (f"[召唤棋子]{e['name']} —— 由【{e['source_trait']}】羁绊生成的可放置棋子, "
                    f"不在商店购买。{e.get('note', '')}")
        if kind == "trait":
            return f"[羁绊]{e['name']} 阈值{e['thresholds']}: {e['desc']}"
        if kind.startswith("item"):
            comp = f" 合成:{'+'.join(e['composition'])}" if e.get("composition") else ""
            uniq = " (唯一)" if e.get("unique") else ""
            return f"[{kind}]{e['name']}{uniq}{comp}: {e['desc']}"
        if kind == "augment":
            tier = f"·{e['tier']}" if e.get("tier") else ""
            return f"[海克斯{tier}]{e['name']}: {e['desc']}"
        return None

    def entities_in_state(self, state: dict) -> list[str]:
        """从 GUI 状态里收集所有涉及的实体名"""
        names: list[str] = []
        names += state.get("augments", []) + state.get("pending_augments", [])
        names += state.get("emblems", [])
        for arr in (state.get("items") or {}).values():
            names += arr
        for u in (state.get("board") or []) + (state.get("bench") or []):
            names.append(u.get("unit", ""))
            names += u.get("items", [])
        names += state.get("shop", [])
        for u in ((state.get("opponent") or {}).get("board") or []):
            names.append(u.get("unit", ""))
        return [n for n in names if n]

    def details_block(self, state: dict, already: set[str]) -> str:
        """为状态中首次出现的实体生成详细数据块"""
        lines = []
        for n in self.entities_in_state(state):
            if n in already:
                continue
            t = self.detail_text(n)
            if t:
                lines.append(t)
                already.add(n)
        if not lines:
            return ""
        return "【相关详细数据】\n" + "\n".join(lines)

    # ---------- 羁绊核验 (确定性计算, 不让 LLM 心算) ----------
    def analyze_comp(self, unit_names: list[str], emblems: list[str] | None = None) -> str:
        """计算一套阵容每个羁绊的数量与激活档位, 标出溢出/未激活。
        规则: 同名棋子只计一次; 拉克丝形态为其可变羁绊提供+2计数; 纹章+1。"""
        emblems = emblems or []
        trait_by_name = {t["name"]: t for t in self.traits}
        trait_by_api = {t["apiName"]: t["name"] for t in self.traits if t.get("apiName")}
        counts: dict[str, int] = {}
        notes, unknown = [], []

        seen = set()
        for n in unit_names:
            hit = self.index.get(n)
            if hit and hit[0] == "summon":
                # 召唤棋子(永恒之森植物等)占位但不计入羁绊档位
                notes.append(f"{n} 为 {hit[1]['source_trait']} 召唤棋子, 不计入羁绊")
                continue
            if not hit or hit[0] != "champion":
                # 简称容错
                cands = self.alias.get(n, [])
                cand = next((c for c in cands if self.index.get(c, ("",))[0] == "champion"), None)
                if not cand:
                    unknown.append(n)
                    continue
                n, hit = cand, self.index[cand]
            if n in seen:
                continue
            seen.add(n)
            e = hit[1]
            for t in e["traits"]:
                inc = 1
                if e.get("form_of") and t != "自然之力！大元素使":
                    inc = 2
                    notes.append(f"{n} 为 {t} 提供+2计数")
                counts[t] = counts.get(t, 0) + inc

        for emb in emblems:
            hit = self.index.get(emb)
            trait = None
            if hit and hit[0].startswith("item"):
                for api in hit[1].get("traits") or []:
                    trait = trait_by_api.get(api)
                    if trait:
                        break
            if not trait and emb.endswith("纹章") and emb[:-2] in trait_by_name:
                trait = emb[:-2]
            if trait:
                counts[trait] = counts.get(trait, 0) + 1
            else:
                unknown.append(emb)

        lines, waste = [], []
        for tname, c in sorted(counts.items(), key=lambda x: -x[1]):
            t = trait_by_name.get(tname)
            if not t or not t.get("thresholds"):
                continue
            ths = [x for x in t["thresholds"] if x]
            active = max((x for x in ths if x <= c), default=None)
            nxt = min((x for x in ths if x > c), default=None)
            if active is None:
                if c == 1:
                    status = "1个, 未激活(单卡顺带, 通常可接受)"
                else:
                    status = f"未激活(差{ths[0] - c}个到{ths[0]}档)"
                    waste.append(tname)
            elif c == active:
                status = f"激活{active}档, 正好"
            else:
                status = f"激活{active}档, 溢出{c - active}个" + (f" (差{nxt - c}个到{nxt}档)" if nxt else "")
                waste.append(tname)
            lines.append(f"{tname}: {c} -> {status}")

        out = [f"阵容核验 ({len(seen)}个棋子):"] + lines
        if notes:
            out.append("说明: " + "; ".join(notes))
        if unknown:
            out.append("无法识别(请用官方名重试): " + ", ".join(unknown))
        out.append("结论: " + ("存在浪费/未激活羁绊: " + ", ".join(waste) + " —— 需要调整" if waste
                              else "所有羁绊都正好落在档位上"))
        return "\n".join(out)

    # ---------- 阵容码 (游戏内可粘贴导入) ----------
    def team_code(self, unit_names: list[str]) -> str | None:
        """生成游戏内阵容码: '02' + 10槽位x3位hex(team_planner_code) + 'TFTSet18'。
        变体棋子(拉克丝形态)回落到基础条目的码。识别不出的棋子跳过。"""
        codes = []
        for n in unit_names[:10]:
            hit = self.index.get(n)
            if not hit or hit[0] != "champion":
                cand = next((c for c in self.alias.get(n, [])
                             if self.index.get(c, ("",))[0] == "champion"), None)
                if not cand:
                    continue
                hit = self.index[cand]
            e = hit[1]
            pc = e.get("planner_code")
            if pc is None and e.get("form_of"):
                base = self.index.get(e["form_of"])
                pc = base[1].get("planner_code") if base else None
            if pc is not None:
                codes.append(pc)
        if not codes:
            return None
        slots = codes + [0] * (10 - len(codes))
        return "02" + "".join(f"{c:03X}" for c in slots) + f"TFTSet{self.set_number}"

    # ---------- 工具层 ----------
    def lookup(self, names: list[str]) -> str:
        out = []
        for n in names:
            t = self.detail_text(n)
            if t:
                out.append(t)
                continue
            # 简称/黑话
            if n in self.alias:
                out.append("\n".join(self.detail_text(m) for m in self.alias[n][:5]))
                continue
            # 模糊: 子串匹配
            matches = [k for k in self.index if n in k or k in n]
            if matches:
                out.append("\n".join(self.detail_text(m) for m in matches[:5]))
            else:
                out.append(f"未找到: {n}")
        return "\n".join(out)


def render_state(state: dict) -> str:
    """把 GUI 状态渲染成给模型看的中文描述"""
    L = []
    bar = []
    for key, label in [("stage", "阶段"), ("level", "等级"), ("gold", "金币"), ("hp", "血量"), ("streak", "连胜/败")]:
        v = state.get(key)
        if v not in (None, "", []):
            bar.append(f"{label}:{v}")
    if bar:
        L.append("状态: " + " ".join(bar))
    if state.get("augments"):
        L.append("已选海克斯: " + ", ".join(state["augments"]))
    if state.get("pending_augments"):
        L.append("待选海克斯(三选一): " + " / ".join(state["pending_augments"]))
    if state.get("emblems"):
        L.append("持有转职纹章: " + ", ".join(state["emblems"]))
    items = state.get("items") or {}
    cat_label = {"component": "散件", "craftable": "成装", "artifact": "神器", "support": "辅助装", "radiant": "光明装"}
    for cat, arr in items.items():
        if arr:
            L.append(f"{cat_label.get(cat, cat)}(未装备): " + ", ".join(arr))
    if state.get("board"):
        rows = []
        for u in state["board"]:
            s = f"{u['unit']}{u.get('star', 1)}星"
            if u.get("items"):
                s += f"[{'/'.join(u['items'])}]"
            if u.get("pos"):
                s += f"@({u['pos'][0]},{u['pos'][1]})"
            rows.append(s)
        L.append("场上: " + ", ".join(rows))
    if state.get("bench"):
        L.append("备战席: " + ", ".join(f"{u['unit']}{u.get('star', 1)}星" for u in state["bench"]))
    if state.get("shop"):
        L.append("当前商店: " + ", ".join(state["shop"]))
    opp = state.get("opponent") or {}
    if opp.get("hp") not in (None, ""):
        L.append("对手血量: " + str(opp["hp"]))
    if opp.get("board"):
        rows = []
        for u in opp["board"]:
            s = f"{u['unit']}{u['star']}星" if u.get("star") else u["unit"]
            if u.get("items"):
                s += f"[{'/'.join(u['items'])}]"
            if u.get("pos"):
                s += f"@({u['pos'][0]},{u['pos'][1]})"
            rows.append(s)
        L.append("对手场上: " + ", ".join(rows))
    if opp.get("note"):
        L.append("对手备注: " + opp["note"])
    if state.get("note"):
        L.append("备注: " + state["note"])
    return "\n".join(L) if L else "(未提供状态)"


def diff_state(prev: dict | None, cur: dict) -> str:
    """上回合到本回合的变化摘要(帮模型抓重点, 全量状态仍然会给)"""
    if not prev:
        return ""
    changes = []
    for key, label in [("stage", "阶段"), ("level", "等级"), ("gold", "金币"), ("hp", "血量")]:
        a, b = prev.get(key), cur.get(key)
        if a not in (None, "") and b not in (None, "") and a != b:
            changes.append(f"{label} {a}->{b}")
    prev_units = {u["unit"] for u in (prev.get("board") or []) + (prev.get("bench") or [])}
    cur_units = {u["unit"] for u in (cur.get("board") or []) + (cur.get("bench") or [])}
    if cur_units - prev_units:
        changes.append("新增棋子: " + ", ".join(cur_units - prev_units))
    if prev_units - cur_units:
        changes.append("移除棋子: " + ", ".join(prev_units - cur_units))
    return ("本回合变化: " + "; ".join(changes)) if changes else ""
