"""TFT Copilot 后端: FastAPI + SSE 流式对话。

启动: uvicorn backend.app:app --host 0.0.0.0 --port 8000
(手机访问用电脑局域网 IP:8000)
"""
import json
import os
import re
import time
import uuid
from pathlib import Path

import yaml
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.knowledge import Knowledge, render_state, diff_state
from backend.llm import make_client

ROOT = Path(__file__).resolve().parent.parent


def load_dotenv():
    """加载 .env 到环境变量 (已有的环境变量优先, 不覆盖)"""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_dotenv()
CFG = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
ALLOW_USER_KEY = bool(CFG["llm"].get("allow_user_key", False))
PERSIST_SESSIONS = bool(CFG.get("persist_sessions", True))
SESS_DIR = ROOT / "data" / "sessions"
if PERSIST_SESSIONS:
    SESS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="TFT Copilot")
# 允许任意前端跨源调用(全 API 化)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
KN = Knowledge(CFG.get("set", 18))
_clients: dict = {}


def default_provider() -> str:
    return os.environ.get("LLM_PROVIDER") or CFG["llm"]["provider"]


def provider_info() -> dict:
    """给前端: 各供应商的可选模型、默认模型、服务端是否有 key"""
    out = {}
    for name, pcfg in CFG["llm"]["providers"].items():
        out[name] = {
            "models": pcfg.get("models") or [pcfg["model"]],
            "default_model": os.environ.get(f"{name.upper()}_MODEL") or pcfg["model"],
            "server_key_set": bool(os.environ.get(pcfg.get("api_key_env", ""), "")),
        }
    return out


def llm(provider: str | None = None, model: str | None = None, user_key: str | None = None):
    """按 (供应商, 模型, key) 缓存客户端。用户自填 key 需开关允许。"""
    if not ALLOW_USER_KEY:
        provider, model, user_key = None, None, None
    cache_key = (provider or "_", model or "_", user_key or "_server")
    if cache_key not in _clients:
        _clients[cache_key] = make_client(CFG, provider=provider, api_key=user_key, model=model)
    return _clients[cache_key]


# ---------------- 会话 ----------------

SESSIONS: dict[str, dict] = {}


def new_session() -> dict:
    sid = time.strftime("%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    s = {"id": sid,
         "messages": [{"role": "system", "content": KN.system_prompt() + BOARD_PROTOCOL}],
         "injected": set(), "last_state": None}
    SESSIONS[sid] = s
    return s


def save_session(s: dict):
    if not PERSIST_SESSIONS:
        return  # 只存内存 (SESSIONS 字典), 不落盘
    dump = {k: (sorted(v) if isinstance(v, set) else v) for k, v in s.items()}
    (SESS_DIR / f"{s['id']}.json").write_text(json.dumps(dump, ensure_ascii=False, indent=1), encoding="utf-8")


def load_session(sid: str) -> dict | None:
    if sid in SESSIONS:
        return SESSIONS[sid]
    f = SESS_DIR / f"{sid}.json"
    if PERSIST_SESSIONS and f.exists():
        s = json.loads(f.read_text(encoding="utf-8"))
        s["injected"] = set(s.get("injected", []))
        SESSIONS[sid] = s
        return s
    return None


# ---------------- 指令模板 ----------------

# 常驻棋盘协议: 每轮回答, 目标阵容/站位相比你上一次给出的有变化时, 在最后附加```json块
# (前端渲染成常驻目标阵容板); 没变化就不输出, 避免重复浪费 token。
BOARD_PROTOCOL = (
    "\n\n【棋盘协议】你上一次给出的目标阵容会常驻显示在玩家界面上。本轮回答后, 若目标阵容、"
    "装备分配或站位相比你上次给出的json有任何变化(包括首次给出), 在回答最后附加```json代码块:"
    '{"target_comp":[{"unit":"棋子名","star":星级,"items":["装备名"]}],'
    '"positioning":[{"unit":"棋子名","row":0到3,"col":0到6}]}'
    " (row0最前排; positioning 没有站位变化时省略该字段)。若完全没变化, 不要输出json块。"
    "正文不要复述json内容。"
)

ACTIONS = {
    "recommend": "推荐当前最优方向和本回合操作(买/卖/D/升人口/装备合成)。",
    "augment": "待选海克斯三选一, 先给结论再一句理由。",
    "roll": "本回合D牌还是存钱/拉人口? 给具体金币预算。",
    "position": "给出当前阵容的最优站位。",
    "counter": "根据对手信息给针对性站位和克制思路。",
    "transition": "要不要转型? 转就给目标和步骤, 不转就说怎么补强。",
}


class ChatReq(BaseModel):
    session_id: str | None = None
    state: dict | None = None
    action: str | None = None
    message: str | None = None
    deep: bool = False
    api_key: str | None = None
    provider: str | None = None
    model: str | None = None


# ---------------- 接口 ----------------

@app.post("/api/session")
def api_new_session():
    s = new_session()
    save_session(s)
    return {"session_id": s["id"]}


@app.post("/api/chat")
def api_chat(req: ChatReq):
    s = load_session(req.session_id) if req.session_id else None
    if s is None:
        s = new_session()

    # 组装本轮 user 消息: 状态变化 + 全量状态 + 按需知识 + 指令/问题
    parts = []
    if req.state:
        d = diff_state(s["last_state"], req.state)
        if d:
            parts.append(d)
        parts.append("【当前状态】\n" + render_state(req.state))
        detail = KN.details_block(req.state, s["injected"])
        if detail:
            parts.append(detail)
        s["last_state"] = req.state
    if req.action and req.action in ACTIONS:
        parts.append(ACTIONS[req.action])
    if req.message:
        parts.append(req.message)
    if not parts:
        parts.append("基于当前局面给出建议。")

    s["messages"].append({"role": "user", "content": "\n\n".join(parts)})

    def gen():
        sid_evt = json.dumps({"type": "session", "session_id": s["id"]}, ensure_ascii=False)
        yield f"data: {sid_evt}\n\n"
        try:
            for kind, payload in llm(req.provider, req.model, req.api_key).stream_chat(s["messages"], TOOL_HANDLERS, deep=req.deep):
                if kind == "delta":
                    yield "data: " + json.dumps({"type": "delta", "text": payload}, ensure_ascii=False) + "\n\n"
                elif kind == "tool":
                    yield "data: " + json.dumps({"type": "tool", "text": payload}, ensure_ascii=False) + "\n\n"
                elif kind == "done":
                    s["messages"].append({"role": "assistant", "content": payload})
                    save_session(s)
                    structured = parse_structured(payload)
                    attach_trait_check(structured)
                    yield "data: " + json.dumps({"type": "done", "structured": structured}, ensure_ascii=False) + "\n\n"
        except Exception as e:
            yield "data: " + json.dumps({"type": "error", "text": str(e)}, ensure_ascii=False) + "\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


TOOL_HANDLERS = {
    "lookup_details": lambda args: KN.lookup(args.get("names") or []),
    "verify_comp": lambda args: KN.analyze_comp(args.get("units") or [], args.get("emblems") or []),
}


def attach_trait_check(structured: dict | None):
    """服务端兜底: 对模型给出的目标阵容做确定性羁绊核验, 结果随 structured 返回给前端显示。
    棋子携带的纹章类装备计入羁绊。"""
    if not structured or not structured.get("target_comp"):
        return
    units, emblems = [], []
    for u in structured["target_comp"]:
        if u.get("unit"):
            units.append(u["unit"])
        for it in u.get("items") or []:
            if isinstance(it, str) and it.endswith("纹章"):
                emblems.append(it)
    if units:
        structured["trait_check"] = KN.analyze_comp(units, emblems).splitlines()
        code = KN.team_code(units)
        if code:
            structured["team_code"] = code


def parse_structured(text: str) -> dict | None:
    m = re.findall(r"```json\s*(\{.*?\})\s*```", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m[-1])
    except json.JSONDecodeError:
        return None


# ---------------- GUI 静态数据 ----------------

def icon_url(path: str) -> str:
    if not path:
        return ""
    p = path.lower().replace(".tex", ".png").replace(".dds", ".png")
    return f"https://raw.communitydragon.org/latest/game/{p}"


@app.get("/api/data")
def api_data():
    champs = [{"name": c["name"], "cost": c["cost"], "traits": c["traits"], "icon": icon_url(c["icon"]),
               **({"forms": c["forms"], "all_traits": c["all_traits"]} if c.get("forms") else {}),
               **({"form_of": c["form_of"]} if c.get("form_of") else {})}
              for c in KN.champions]
    items = {cat: [{"name": i["name"], "icon": icon_url(i["icon"]),
                    "composition": i.get("composition", []), "desc": i["desc"][:80]}
                   for i in arr]
             for cat, arr in KN.items.items()}
    augs = [{"name": a["name"], "icon": icon_url(a["icon"]), "desc": a["desc"][:80],
             "tier": a.get("tier"), "traits": a.get("traits") or []} for a in KN.augments]
    emblems = [i["name"] for i in KN.items.get("emblem", [])]
    search_file = ROOT / "data" / "packs" / f"set{CFG.get('set', 18)}" / "search_index.json"
    search = json.loads(search_file.read_text(encoding="utf-8")) if search_file.exists() else {}
    return {"version": KN.version, "champions": champs, "items": items,
            "augments": augs, "emblems": emblems, "search": search,
            "actions": {k: v.split("\n")[0][:20] for k, v in ACTIONS.items()},
            "config": {"allow_user_key": ALLOW_USER_KEY,
                       "default_provider": default_provider(),
                       "providers": provider_info()}}


# ---------------- 前端 ----------------

app.mount("/static", StaticFiles(directory=ROOT / "frontend"), name="static")


@app.get("/")
def index():
    # web_gui 开关(合并服务经 TFT_WEB_GUI 传入): 关掉后不托管网页GUI, 但 /api/* 照常
    if os.environ.get("TFT_WEB_GUI", "1") == "0":
        return HTMLResponse(
            "<meta charset=utf-8><body style='font:16px system-ui;padding:40px'>"
            "网页界面已关闭, 请使用桌面客户端。<br>"
            "如需重新开启: 把 service_config.json 的 web_gui 设为 true 再重启。</body>",
            status_code=404)
    # 给 app.js/style.css 附上按文件修改时间的版本号, 改了前端刷新就能拿到最新(免手动清缓存)
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    for f in ("app.js", "style.css"):
        mt = int((ROOT / "frontend" / f).stat().st_mtime)
        html = html.replace(f'/static/{f}"', f'/static/{f}?v={mt}"')
    return HTMLResponse(html)
