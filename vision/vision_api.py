"""Vision recognition API service (standalone process, no torch required).

The main backend (backend/app.py) is fastapi+LLM only; the vision service is
onnxruntime-only (see vision/requirements.txt) so both can run in the same
Python environment.

Start:
  uvicorn vision.vision_api:app --host 0.0.0.0 --port 8010
or:
  cd vision && uvicorn vision_api:app --port 8010

Endpoints:
  GET  /api/health
  POST /api/recognize   multipart: image=<screenshot file>, mode=ally|enemy|augment
       -> {"mode","state","raw"}   state matches backend /api/chat's state structure
"""
import io
import json
import os
import sys
import tempfile
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from recognize import Recognizer

app = FastAPI(title="TFT Copilot Vision API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
REC = Recognizer()   # loaded once per process (models stay warm)

# Optional auth: set VISION_API_KEY to require a matching X-API-Key header
# (useful if you expose this service beyond localhost).
API_KEY = os.environ.get("VISION_API_KEY", "")


def check_key(x_api_key: str | None):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="invalid api key")


def to_state(rec: dict) -> dict:
    """Recognition result -> the state structure render_state/diff_state expect."""
    if rec["mode"] == "augment":
        return {"stage": rec.get("stage"), "hp": rec.get("health"),
                "pending_augments": [a for a in (rec.get("augments") or []) if a]}
    if rec["mode"] == "enemy":
        opp_board = []
        for u in rec.get("board", []):
            cell = u["cell"]
            if not cell.startswith("board_"):
                continue                       # bench view for the opponent is unreliable, skip
            _, r, c = cell.split("_")
            e = {"unit": u["name"], "pos": [int(r), int(c)], "star": u.get("star") or 1}
            its = [i["name"] for i in u.get("items", [])]
            if its:
                e["items"] = its
            opp_board.append(e)
        opp = {"board": opp_board}
        if rec.get("health") is not None:
            opp["hp"] = rec["health"]
        return {"stage": rec.get("stage"), "opponent": opp}
    st = {"stage": rec.get("stage"), "level": rec.get("level"),
          "gold": rec.get("gold"), "hp": rec.get("health"), "streak": rec.get("streak")}
    board, bench = [], []
    for u in rec.get("board", []):
        items = [i["name"] for i in u.get("items", [])]
        star = u.get("star") or 1
        cell = u["cell"]
        if cell.startswith("board_"):
            _, r, c = cell.split("_")
            board.append({"unit": u["name"], "star": star, "items": items, "pos": [int(r), int(c)]})
        else:  # bench_0_c
            e = {"unit": u["name"], "star": star}
            if items:
                e["items"] = items
            bench.append(e)
    st["board"], st["bench"] = board, bench
    st["shop"] = [(s["name"] if isinstance(s, dict) else s) for s in rec.get("shop", []) if s]
    # unequipped bench items -> items (by category); consumables (reforge/remover) are excluded
    cat = {"component": [], "craftable": [], "artifact": [], "support": [], "radiant": []}
    for b in rec.get("bench_items", []):
        if b.get("consumable") or not b.get("item_id"):
            continue
        iid, nm = b["item_id"], b["name"]
        if iid.endswith("Radiant") or nm.startswith("光明"):
            cat["radiant"].append(nm)
        elif iid.startswith("Component_"):
            cat["component"].append(nm)
        elif iid.startswith("Artifact_"):
            cat["artifact"].append(nm)
        else:
            cat["craftable"].append(nm)
    st["items"] = {k: v for k, v in cat.items() if v}
    return st


@app.get("/api/health")
def health():
    return {"ok": True}


def _config_path():
    return Path(os.environ.get("TFT_CONFIG_PATH") or (HERE.parent / "service_config.json"))


DEFAULT_CFG = {"host": "127.0.0.1", "backend_port": 8000, "vision_port": 8010, "web_gui": True}


@app.get("/api/config")
def get_config():
    cfg = dict(DEFAULT_CFG)
    p = _config_path()
    if p.exists():
        try:
            cfg.update(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            pass
    return cfg


@app.post("/api/config")
def set_config(patch: dict = Body(...)):
    """Save ports / web_gui toggle to service_config.json. Takes effect after restart."""
    cfg = get_config()
    for k in ("host", "backend_port", "vision_port", "web_gui"):
        if k in patch:
            cfg[k] = patch[k]
    try:
        cfg["backend_port"] = int(cfg["backend_port"])
        cfg["vision_port"] = int(cfg["vision_port"])
        cfg["web_gui"] = bool(cfg["web_gui"])
    except (ValueError, TypeError):
        return {"ok": False, "reason": "ports must be numbers"}
    _config_path().write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True, "config": cfg, "note": "saved, takes effect after restart"}


SETTINGS_HTML = """<!doctype html><meta charset=utf-8><title>TFT Copilot · Settings</title>
<style>body{font:14px/1.6 system-ui;max-width:560px;margin:32px auto;padding:0 16px;color:#e6edfb;background:#0f1420}
.card{background:#171e2e;border:1px solid #2c3a5c;border-radius:10px;padding:16px 18px;margin:14px 0}
h3{margin:0 0 8px} .ok{color:#5ac87a} .bad{color:#c85a4a} button,input[type=file]{font:14px system-ui}
button{background:#c8a24a;border:0;color:#000;border-radius:6px;padding:7px 14px;cursor:pointer}
input[type=number]{width:80px;background:#0d1220;border:1px solid #2c3a5c;color:#e6edfb;border-radius:6px;padding:5px 8px}
label{display:inline-flex;align-items:center;gap:6px;margin-right:16px}</style>
<h2>TFT Copilot · Settings</h2>
<div class=card><h3>Ports / UI</h3>
 <div style=margin-bottom:8px><label>Backend port <input type=number id=bp></label>
  <label>Vision port <input type=number id=vp></label></div>
 <div style=margin-bottom:10px><label><input type=checkbox id=wg> Serve the web GUI</label></div>
 <button onclick=save()>Save</button> <span id=cfgmsg></span>
 <div style=color:#8494b3;font-size:12px;margin-top:6px>Restart the service after changing this.</div></div>
<script>
async function load(){
 try{const c=await (await fetch('/api/config')).json();bp.value=c.backend_port;vp.value=c.vision_port;wg.checked=!!c.web_gui}catch(e){}}
async function save(){cfgmsg.textContent='saving...';
 const r=await (await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({backend_port:+bp.value,vision_port:+vp.value,web_gui:wg.checked})})).json();
 cfgmsg.innerHTML=r.ok?'<span class=ok>'+r.note+'</span>':'<span class=bad>'+r.reason+'</span>'}
load()
</script>"""


@app.get("/settings", response_class=HTMLResponse)
def settings_page():
    return SETTINGS_HTML


@app.post("/api/recognize")
async def recognize(image: UploadFile = File(...), mode: str = Form("ally"),
                    x_api_key: str | None = Header(default=None)):
    check_key(x_api_key)
    data = await image.read()
    img = Image.open(io.BytesIO(data)).convert("RGB")
    if img.size != (1920, 1080):
        return {"error": f"Expected a 1920x1080 screenshot, got {img.size}"}
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        img.save(f.name)
        rec = REC.recognize(f.name, mode)
    Path(f.name).unlink(missing_ok=True)
    return {"mode": mode, "state": to_state(rec), "raw": rec}
