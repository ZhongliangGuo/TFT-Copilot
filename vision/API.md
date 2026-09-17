# Vision API integration notes

Everything is API-based. Two independent services (each hot-loads its own models, no interference between them); a frontend calls both.

## Architecture

```
Frontend (web / your own client)
  │  ① screenshot (1920x1080)
  ├─► Vision API   POST /api/recognize (image, mode)   [:8010]
  │       └─► returns { state, raw }   state = the board structure the main API expects
  │  ② (optional) let the user review/tweak using raw
  └─► Main API     POST /api/chat (state, action/message) [:8000]
          └─► SSE-streamed advice + structured target comp
```

**Why two services**: keeps the agent backend lightweight and lets the vision service be deployed separately (e.g. on a machine with a GPU) without touching the main backend.

## Start

```sh
# main backend
uvicorn backend.app:app --host 0.0.0.0 --port 8000

# vision service
cd vision && uvicorn vision_api:app --host 0.0.0.0 --port 8010
```
Both have CORS enabled, so any web frontend can call them cross-origin.
`python run.py` at the project root starts both for you (see the top-level README).

## Vision API

- `GET /api/health` -> `{ok:true}`
- `POST /api/recognize` multipart: `image=<screenshot>`, `mode=ally|enemy|augment`
  - `ally` (your board): stage/level/gold/health + board (units/stars/items/positions) + shop (5 slots: units or portals) + unequipped bench items
  - `enemy` (opponent board): board (units/positions/stars visible from the front-facing camera; item recognition is not supported for the opponent's board — see `known_issues.md`)
  - `augment` (augment picker): stage/health + the 3 augment choices
  - Returns `{mode, state, raw}`. `state` feeds directly into the main API's `/api/chat`; `raw` is the full recognition result with confidences (for UI display / user correction).

## Main API (unchanged, already documented in the top-level README)

- `POST /api/session` -> new session
- `POST /api/chat` `{session_id, state, action|message, ...}` -> SSE advice. `state` is exactly what the vision API produces.
- `GET /api/data` -> static frontend data (units/items/augments/icons, etc.)

## Frontend integration flow

1. Capture the current game screen at 1920x1080.
2. Decide the mode (your board / opponent board / augment picker) -> pass as `mode`.
3. `POST` the vision API's `/api/recognize` -> get `state` (+ `raw` for the user to review).
4. `POST` the main API's `/api/chat` with `state` and `action` (e.g. `recommend`/`augment`) -> streamed advice.

> Recognition is meant to assist manual entry, not replace it — for important decisions, let the user glance at `raw`'s low-confidence fields (portals, emblems, radiant items) before sending.

## Related files
- `vision/vision_api.py` — the vision API service (incl. the `to_state` mapping)
- `vision/recognize.py` — unified recognizer (board + HUD + bench), also usable from the command line
- `vision/infer.py` / `hud/hud_ocr.py` / `hud/bench_items.py` — the three recognition modules
