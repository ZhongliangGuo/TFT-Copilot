"""装备 id -> 中文名 的统一映射。棋子身上装备(YOLO)和备战席散装备(embedding)共用,
避免一边中文一边英文。装备类别 id(equipment_classes)命名和 items.json 的 apiName 不一致,
用归一化(去前缀/Radiant, 只留字母数字)跨命名匹配到 items.json 的中文名。"""
import json
import re
from pathlib import Path


# 装备改名: 视觉模型类名/现名 -> CDragon 保留的旧 apiName(归一化后), 使二者能对上
_ITEM_SYNONYM = {
    "hullbreaker": "hullcrusher",     # 碎舰者: CDragon Hullbreaker = 模型/tftable Hullcrusher
    "spiritvisage": "redemption",     # 振奋盔甲: 模型类名 SpiritVisage = CDragon 旧名 Redemption
}

# 装备类名 -> 中文名 的显式对照: 归一化后仍对不上 items.json(改名) 或会撞名(如 VoidStaff 归一后
# 与神器 斯塔缇克电刃 撞成 statikkshiv)的, 一律在此写死, 避免英文名漏进前端。
# (对全部 137 类逐一核对得出, 2026-09)
_CLASS_CN = {
    "Artifact_NavoriFlickerblade": "烁刃",
    "EdgeOfNight": "夜之锋刃",
    "Evenshroud": "薄暮法袍",
    "KrakensFury": "海妖之怒",
    "ProtectorsVow": "圣盾使的誓约",   # 旧名 FrozenHeart(冰心/冰甲)
    "StrikersFlail": "强袭者的链枷",
    "TacticiansCape": "金锅铲冠冕",
    "TacticiansCrown": "金铲铲冠冕",
    "TacticiansShield": "金锅锅冠冕",
    "VoidStaff": "虚空之杖",           # 与神器"斯塔缇克电刃"归一撞名, 必须写死
}


def _norm(s):
    s = re.sub(r"(TFT\d*_Item_|TFT_Item_|DA_|Artifact_|Component_|Ornn)", "", s or "")
    s = re.sub(r"Radiant$", "", s)
    s = re.sub(r"[^a-z0-9]", "", s.lower())
    return _ITEM_SYNONYM.get(s, s)


def build_item_cn(root: Path) -> dict:
    """{装备id: 中文名}。id 来自 equipment_classes(137类), 中文优先取 items.json。"""
    eq = json.loads((root / "data" / "vision_dataset" / "s18-equipment-v5.1"
                     / "equipment_classes.json").read_text(encoding="utf-8"))
    items = json.loads((root / "data" / "packs" / "set18" / "items.json").read_text(encoding="utf-8"))
    # 只用基础装备名建映射: 光明装的 apiName 去掉 Radiant 后会和基础装撞归一名
    # (如 TFT5_Item_RedemptionRadiant 与 TFT_Item_Redemption 都 -> redemption),
    # 光明版前缀由下面按 iid.endswith('Radiant') 单独加, api_cn 只放基础名避免撞车。
    api_cn = {}
    for cat in items.values():
        for it in cat:
            if it["name"].startswith("光明版"):
                continue
            api_cn[_norm(it["apiName"])] = it["name"]
    out = {}
    for e in eq:
        iid = e["id"]
        if iid in _CLASS_CN:            # 改名/撞名的显式对照, 优先
            out[iid] = _CLASS_CN[iid]
            continue
        cn = e.get("name", iid)
        if cn == iid or re.search(r"[A-Za-z_]", cn):      # equipment_classes 里还是英文的, 查 items.json
            n = _norm(iid)
            if n in api_cn:
                cn = ("光明版" + api_cn[n]) if iid.endswith("Radiant") else api_cn[n]
        out[iid] = cn
    return out
