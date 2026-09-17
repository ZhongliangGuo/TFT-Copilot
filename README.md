# TFT Copilot

[中文说明](README.zh-CN.md)

An Agent coach for Teamfight Tactics: **comp planning / augment picks / roll-or-save decisions / positioning**, live during a match.

How it works: your current board state is fed to an agent, which combines it with a built-in season data pack + meta-tier knowledge to give concise advice and a persistent "target comp". You can enter the board state two ways:

1. **Manually** — click through the left panel (units / items / augments / status...).
2. **Screenshot recognition** — upload or `Ctrl+V` paste a 1920x1080 in-game screenshot; a local vision service turns it into structured board state and fills it in for you.

## Screenshots

| Recognizing your board | Recognizing the opponent's board | Recognizing an augment pick |
|---|---|---|
| ![my board recognized](docs/screenshots/recognize-my-board.jpg) | ![enemy board recognized](docs/screenshots/recognize-enemy-board.jpg) | ![augments recognized](docs/screenshots/recognize-augments.jpg) |

The three screenshots above are from [`examples/`](examples/) — see "Try it with the example screenshots" below. Note the opponent board has no item icons: that's deliberate, not a bug (see the Design philosophy section and `known_issues.md`).

![agent recommendation](docs/screenshots/chat-recommend.jpg)

The agent's recommendation, with its tool calls (🔍 查询/核验阵容) visible above the target comp board.

---

## What's in this repository

This is the open-source release of the project: the full backend/vision/frontend **framework**, the season data pack, and the trained vision **model weights** (ONNX, distributed separately — see below).

**Not included**: the labeled training images/dataset used to train those models, and the model training code — training requires a private dataset this release doesn't include, so shipping the training scripts without it wouldn't be useful. What you get is the inference pipeline (which only needs `onnxruntime`, no torch), fully able to run recognition on your own screenshots.

**License**: [PolyForm Noncommercial 1.0.0](LICENSE.md) — free for personal/hobby/research use, contact the author for commercial use.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  frontend/  (static HTML/JS/CSS, served by the main backend)  │
│    left panel: enter board state   right panel: chat coach    │
└───────────┬───────────────────────────────┬─────────────────┘
            │ ① screenshot POST /api/recognize │ ② POST /api/chat (with state)
            ▼                                 ▼
┌───────────────────────────┐   ┌───────────────────────────────┐
│ vision service   :8010     │   │ main backend   :8000            │
│ vision/  (onnxruntime)     │   │ backend/ (FastAPI + agent)       │
│ local CV/OCR, screenshot   │   │ season data pack + knowledge     │
│ -> board state, no torch   │   │ SSE-streamed advice + target comp│
└───────────────────────────┘   └───────────────────────────────┘
```

Both are separate FastAPI services with CORS enabled, so they can be deployed independently if you want (e.g. the vision service on a different machine). `run.py` at the repo root starts both for you in one process.

---

## Design philosophy

The core bet behind this project: an agent is great at judgment, language, and adapting advice to your specific situation — and bad at exact arithmetic and recalling precise facts. So instead of just asking it to "be right," the backend gives it a small set of deterministic tools to call for anything that has one correct answer, and double-checks its final output the same way before it ever reaches you.

- **`verify_comp`** (`backend/knowledge.py`'s `analyze_comp`): counts trait breakpoints exactly — active/overflow/short-by-N for every trait in a comp, including traits carried by emblems. The agent is instructed to call this before recommending any final comp; the backend also re-runs the same check server-side on whatever comp the agent ends up proposing (`attach_trait_check` in `backend/app.py`) and attaches the ground-truth result regardless of whether the agent remembered to check itself — a wrong trait-count claim never reaches you unverified.
- **`lookup_details`**: forces the agent to pull this season's actual numbers (stats, ability text, item composition) from the data pack instead of recalling them from pretraining, which goes stale the moment the game patches.

The general shape: let the agent reason and communicate, but never let it be the source of truth for anything countable or lookup-able. If you extend this project with mechanics that need exact verification, this tool-calling + server-side re-check pattern (see `TOOL_HANDLERS` in `backend/app.py`) is where to hook in.

![deterministic trait verification](docs/screenshots/chat-trait-verify.jpg)

`verify_comp`'s actual output for a real recommendation: every trait's exact breakpoint, plus the positioning board.

---

## Environment setup

Requires **Python 3.10+**. No compilation, no packaging step, no license file needed to run this — just install the dependencies into whatever Python environment you normally use (system Python, a `venv`, conda, etc. — this project doesn't script that for you) and run it:

```bash
# 1) install dependencies into your environment of choice, e.g.:
python -m venv .venv && source .venv/bin/activate   # optional; or activate a conda env, or skip and use system Python
pip install -r requirements.txt

# 2) get the model weights (optional — skip if you only want manual entry)
#    download the onnx-models-*.zip from this repo's GitHub Releases,
#    extract it into vision/onnx/ (see vision/onnx/README.md)

# 3) configure your model provider key (this is what powers the agent)
cp .env.example .env
# edit .env and set DEEPSEEK_API_KEY (or ANTHROPIC_API_KEY for Claude)

# 4) run it (starts both the backend and the vision service)
python run.py
```

Open `http://localhost:8000` in your browser (or `http://<your-LAN-ip>:8000` from your phone on the same network).

---

## Try it with the example screenshots

[`examples/`](examples/) has three ready-made 1920x1080 screenshots (`my_board.png`, `enemy_board.png`, `augments.png`) you can upload through the 📷 button to try screenshot recognition without having a match running.

**The game must be set to windowed mode at exactly 1920x1080**, and the screenshot must be exactly that resolution with no extra border — anything else won't line up with the fixed HUD coordinates the recognizer expects. To capture your own: use [Snipaste](https://www.snipaste.com/) and let it snap the game window directly (it grabs the exact client-area pixels with no scaling), rather than a generic OS screenshot tool. Screenshots at any other size will not work.

See [`known_issues.md`](known_issues.md) for the current known recognition mistakes.

---

## English support

The app's own UI chrome (buttons, labels, tooltips) has an English/中文 toggle (the `EN`/`中` button, top-left) and defaults to your browser's language.

**What's still Chinese-only right now**: the season data pack (unit/item/augment names) and the agent coach's replies, since the underlying game-data source is currently the CN-server locale (`config.yaml`: `region: cn`, `locale: zh_cn`). `scripts/fetch_data.py` already supports other CDragon locales — building an English data pack + prompting the agent to reply in English is a reasonable follow-up contribution, just not done in this release.

---

## Frontend (`frontend/`)

Static page, served by the main backend's `GET /` (adds a cache-busting version query string to `app.js`/`style.css` based on file mtime).

- **Left panel — board state** (all optional): status bar (stage/level/gold/HP/streak), your units (star level, items, drag onto the board), held items (inventory + equipped in one panel, reassignable), augments (selected + pending pick-of-3), opponent info.
- **Search**: pinyin/initials/community nicknames all work; aliases in `data/aliases_cn.json`. An empty search box shows a browse panel (units filterable by cost/trait, items by category).
- **Screenshot recognition**: pick a mode at the top (my board / augment pick) → 📷 upload, or `Ctrl+V` paste a clipboard screenshot directly on the page → calls the vision API → fills in the board (⚙ configures the vision service address/key).
- **Right panel — chat coach**: quick-action buttons or free-text follow-ups; one session per game, the coach remembers the whole match. The "target comp" board stays pinned and only updates when the model's answer changes it (to save tokens); its "Team code" button copies a code you can paste in-game.
- **API panel** (shown when `config.yaml`'s `llm.allow_user_key: true`): lets a user pick their own provider/model and paste their own key (stored in `localStorage`, takes priority over the server's key).

---

## Backend API

### Main API — `backend/app.py` (default `:8000`)

#### `GET /`
Serves the frontend page.

#### `GET /api/data`
Everything the frontend needs on load:
```jsonc
{
  "version":   "...",
  "champions": [{ "name","cost","traits","icon", "forms?","all_traits?","form_of?" }],
  "items":     { "component|craftable|artifact|support|radiant": [{ "name","icon","composition","desc" }] },
  "augments":  [{ "name","icon","desc","tier","traits" }],
  "emblems":   ["...emblem names..."],
  "search":    { /* pinyin/alias search index */ },
  "actions":   { "recommend":"...", "augment":"...", ... },
  "config":    { "allow_user_key","default_provider","providers" }
}
```

#### `POST /api/session`
Creates a new session. Returns `{ "session_id": "MMDD-HHMMSS-xxxx" }` (optional — `/api/chat` auto-creates one if you don't pass a `session_id`).

#### `POST /api/chat` — the core chat endpoint, SSE-streamed
Request body:
```jsonc
{
  "session_id": "…",        // omit to start a new session
  "state":      { … },      // current board state (see below); omit to reuse the last one
  "action":     "recommend",// optional quick action: recommend|augment|roll|position|counter|transition
  "message":    "should I sell my Jinx for a Jhin...",  // optional free-text follow-up
  "deep":       false,      // true = "deep think" (a stronger/slower model)
  "provider":   "deepseek", // optional, only used when allow_user_key is on
  "model":      "…",        // optional
  "api_key":    "sk-…"      // optional, your own key
}
```
Response is `text/event-stream`, one `data: <json>` per line:

| `type` | payload | meaning |
|---|---|---|
| `session` | `session_id` | which session this belongs to |
| `delta`   | `text` | streamed answer text, appended incrementally |
| `tool`    | `text` | a hint that the model is checking its own knowledge (lookup_details/verify_comp) |
| `done`    | `structured` | end of turn; `structured` is the target comp/positioning, or `null` |
| `error`   | `text` | error message |

`done.structured` (only present when the model gives/updates a target comp):
```jsonc
{
  "target_comp":  [{ "unit":"...","star":3,"items":["..."] }],
  "positioning":  [{ "unit":"...","row":0,"col":3 }],   // row 0 = frontmost; omitted if unchanged
  "trait_check":  ["Fighter 4 ✓", "Sorcerer 3 ✗ needs 1 more", …],  // server-side deterministic trait check
  "team_code":    "…"                                    // in-game importable team code
}
```

### Board state structure (`/api/chat`'s `state`, also the vision API's output)

| Field | Type | Meaning |
|---|---|---|
| `stage` | `"4-2"` | current stage |
| `level` | `int` | player level |
| `gold` | `int` | gold |
| `hp` | `int` | health |
| `streak` | `"W3"`/`"L2"` | win/loss streak |
| `augments` | `[name]` | augments already picked |
| `pending_augments` | `[name]` | the current 3 augment choices |
| `emblems` | `[name]` | held emblems |
| `items` | `{component,craftable,artifact,support,radiant: [name]}` | unequipped held items, by category |
| `board` | `[{unit,star,items:[name],pos:[row,col]}]` | units on the board, `row 0` = front row |
| `bench` | `[{unit,star,items?}]` | bench units |
| `shop` | `[name]` | current 5-slot shop |
| `opponent` | `{board:[{unit,pos}], note}` | opponent info (for late-game targeting) |
| `note` | `str` | free-text notes |

All fields optional; missing ones just don't render.

### Vision API — `vision/vision_api.py` (default `:8010`)

See `vision/API.md` for the full endpoint reference (`/api/recognize`, etc.) and `vision/README.md` for how the recognition pipeline itself works.

---

## Data & knowledge

- `data/packs/set18/`: season data pack (units/traits/items/augments/portals JSON + a pinyin search index), built by `scripts/build_pack.py` from CDragon.
- `knowledge/general_strategy.md`: hand-written, cross-season strategy layer.
- `knowledge/resident_pack.md`: generated, goes into the system prompt (benefits from prompt caching).
- `knowledge/meta_comps.md`: meta-tier layer, generated by `scripts/fetch_meta.py`.
- On-demand knowledge: the backend injects relevant entity details based on the current board; the model can also self-query via the `lookup_details`/`verify_comp` tools.

**After a game patch**: re-run the data pipeline (`scripts/fetch_data.py` → `scripts/build_pack.py` → `scripts/build_search_index.py`, and `scripts/fetch_meta.py` for the meta layer) to pick up balance changes. [`skills/tft-update/SKILL.md`](skills/tft-update/SKILL.md) documents this whole flow step by step, including how to sanity-check the results (e.g. catching items that are registered but not actually in the current drop pool). If you use [Claude Code](https://claude.com/claude-code), drop that folder into your `.claude/skills/` directory and it becomes a runnable `/tft-update` command; otherwise just follow it manually or run the scripts directly.

---

## Configuration (`config.yaml`)

- `set` / `region` / `locale`: season / server / language for the data source.
- `persist_sessions`: `false` keeps sessions in memory only (not written to `data/sessions/`); saves disk, loses chat history on restart (the board state in the browser is unaffected either way).
- `llm.provider` / `allow_user_key` / `providers`: default provider, whether users may supply their own key, and the selectable models per provider (DeepSeek via an OpenAI-compatible API, Claude via the official Anthropic SDK).

---

## Repository layout

```
backend/        main backend (FastAPI + agent + knowledge)
frontend/       static frontend (HTML/JS/CSS)
vision/         vision recognition service (local CV/OCR + onnx models) + hud/ submodule
scripts/        data pipeline (fetch_data, build_pack, build_search_index, fetch_meta, check_pool)
data/packs/     season data pack     data/vision_dataset/  vision runtime protocol/region files
knowledge/      strategy / resident / meta knowledge layers
skills/tft-update/   season data-update flow (usable standalone, or as a Claude Code skill)
examples/       three 1920x1080 test screenshots (see "Try it" above)
known_issues.md known recognition mistakes
config.yaml     global config        .env.example  copy to .env and fill in your key
requirements.txt   install into your own environment    run.py   starts both services
LICENSE.md      PolyForm Noncommercial 1.0.0
```
