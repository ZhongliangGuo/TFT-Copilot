# TFT Copilot 后端接口文档

> 依据 `backend/app.py`、`backend/knowledge.py`、`backend/llm.py`、`vision/vision_api.py` 源码整理。

## 1. 服务总览

项目由**两个相互独立的 FastAPI 服务**组成，均开启全量 CORS（`allow_origins=["*"]`），可分开部署：

| 服务 | 模块 | 默认端口 | 职责 |
|---|---|---|---|
| 主后端 | `backend/app.py` | `:8000` | 托管前端静态页、会话管理、SSE 流式对话、知识查询、确定性羁绊核验 |
| 视觉服务 | `vision/vision_api.py` | `:8010` | 1920x1080 截图 → 结构化棋盘状态（onnxruntime，无 torch） |

启动方式：

```bash
python run.py                                        # 单进程同时拉起两个服务
uvicorn backend.app:app --host 0.0.0.0 --port 8000   # 仅主后端
cd vision && uvicorn vision_api:app --port 8010      # 仅视觉服务
```

调用链：`前端 → ① POST :8010 /api/recognize（得 state）→ ② POST :8000 /api/chat（state + action/message）→ SSE 流式建议 + 目标阵容`。

---

## 2. 主后端 API（`:8000`）

### 2.1 `GET /` — 前端页面

- 返回 `frontend/index.html`（`HTMLResponse`）。
- 自动给 `app.js` / `style.css` 追加 `?v=<文件mtime>` 查询参数，改前端后刷新即生效，免清缓存。
- 受环境变量 `TFT_WEB_GUI` 控制：`TFT_WEB_GUI=0` 时返回 404 提示页（"网页界面已关闭"），但 `/api/*` 照常可用。该开关由合并服务模式经环境变量传入，对应 `service_config.json` 的 `web_gui` 字段。

### 2.2 `GET /static/*` — 静态资源

`frontend/` 目录整体挂载到 `/static`（`StaticFiles`）。

### 2.3 `POST /api/session` — 新建会话

- 请求体：无。
- 响应：`{"session_id": "MMDD-HHMMSS-xxxx"}`
- 会话结构：`{"id", "messages": [system + BOARD_PROTOCOL], "injected": set, "last_state": None}`。
  - system prompt = 常驻知识层（通用策略 + 赛季速查 + Meta）+ 棋盘协议（要求模型在目标阵容有变化时于回答末尾附 ```json 块）。
  - `persist_sessions: true`（`config.yaml`）时落盘到 `data/sessions/<sid>.json`；为 `false` 时仅存内存，重启丢失。

### 2.4 `GET /api/data` — 前端静态数据

启动时由 `Knowledge` 从 `data/packs/set<N>/` 加载。响应结构：

```jsonc
{
  "version": { "...": "version.json 内容，含 built_at" },
  "champions": [
    { "name": "...", "cost": 1, "traits": ["..."], "icon": "https://raw.communitydragon.org/...",
      "forms": [...], "all_traits": [...],   // 仅多形态棋子(如拉克丝)
      "form_of": "..." }                      // 仅形态变体条目
  ],
  "items": {   // 按分类
    "component":  [{ "name", "icon", "composition": [...], "desc": "前80字符" }],
    "craftable":  [...], "artifact": [...], "support": [...], "radiant": [...], "emblem": [...]
  },
  "augments": [{ "name", "icon", "desc": "前80字符", "tier": "银|金|彩", "traits": [...] }],
  "emblems":  ["纹章名", ...],
  "search":   { "官方名": ["拼音/首字母/别名", ...] },   // search_index.json，不存在则为 {}
  "actions":  { "recommend": "推荐当前最优方向...", "...": "各快捷指令的简述(≤20字)" },
  "config": {
    "allow_user_key": false,
    "default_provider": "deepseek",
    "providers": {
      "deepseek": { "models": [...], "default_model": "...", "server_key_set": true },
      "claude":   { "...": "..." }
    }
  }
}
```

说明：图标 URL 由 CDragon 路径小写化、`.tex/.dds → .png` 得到；`server_key_set` 表示服务端是否已配置该供应商的 key（对应 `api_key_env` 环境变量是否存在），**不会**泄露 key 本身。

### 2.5 `POST /api/chat` — 流式教练对话（SSE）

**请求体**（`ChatReq`）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `session_id` | `str?` | 会话 ID；为空或不存在时自动新建会话 |
| `state` | `dict?` | 当前棋盘状态，结构见 §2.6；与视觉服务 `/api/recognize` 返回的 `state` 同构 |
| `action` | `str?` | 快捷指令，取值见下表；与 `message` 可并存 |
| `message` | `str?` | 自由提问文本 |
| `deep` | `bool` | 深度思考模式（默认 `false`），见 §4.2 |
| `api_key` | `str?` | 用户自带 key；仅 `llm.allow_user_key: true` 时生效 |
| `provider` / `model` | `str?` | 指定供应商/模型；同上开关控制 |

**快捷指令 `action`**（`ACTIONS`，非法值被忽略）：

| action | 指令内容 |
|---|---|
| `recommend` | 推荐当前最优方向和本回合操作（买/卖/D/升人口/装备合成） |
| `augment` | 待选海克斯三选一，先给结论再一句理由 |
| `roll` | 本回合 D 牌还是存钱/拉人口？给具体金币预算 |
| `position` | 给出当前阵容的最优站位 |
| `counter` | 根据对手信息给针对性站位和克制思路 |
| `transition` | 要不要转型？转就给目标和步骤，不转就说怎么补强 |

**服务端处理流程**：

1. 加载/新建会话；
2. 若有 `state`：与上轮状态 diff（`diff_state`，产出"本回合变化"摘要）+ 全量渲染（`render_state`）+ 按需知识注入（`details_block`，状态中**首次出现**的实体附带详细数值，一局只注入一次）；
3. 拼上 action 指令 / message（都没有时用默认"基于当前局面给出建议"），作为一条 user 消息入会话；
4. 调 LLM `stream_chat`（带工具，最多 4 轮工具调用），边生成边转发 SSE；
5. 结束后把完整回答写入会话并持久化；从回答中解析最后的 ```json 块作为 `structured`，并做**服务端兜底核验**（`attach_trait_check`）：对 `target_comp` 重新跑确定性羁绊计算，棋子携带的"XX纹章"装备也计入羁绊，同时生成游戏内可粘贴的阵容码 `team_code`。

**SSE 响应**（`text/event-stream`，每行 `data: <json>\n\n`）：

| 事件 `type` | 载荷 | 时机 |
|---|---|---|
| `session` | `{session_id}` | 流开始时必发 |
| `delta` | `{text}` | 模型增量文本 |
| `tool` | `{text}` | 工具调用提示，如 `查询: 剑圣, 女警` / `核验阵容: ...`（≤120 字） |
| `done` | `{structured}` | 流结束；`structured` 结构见下，无 JSON 块时为 `null` |
| `error` | `{text}` | 异常时（如无可用 API key） |

**`structured` 结构**（模型按"棋盘协议"输出 + 服务端核验附加）：

```jsonc
{
  "target_comp": [{ "unit": "棋子名", "star": 2, "items": ["装备名"] }],
  "positioning": [{ "unit": "棋子名", "row": 0, "col": 0 }],  // row 0 = 最前排, 无站位变化时模型可省略
  "trait_check": ["羁绊: 数量 -> 激活情况", ...],   // 服务端兜底核验结果(逐行)
  "team_code": "02...TFTSet18"                      // 游戏内可粘贴导入的阵容码, 无法生成时缺省
}
```

### 2.6 棋盘状态 `state` 结构

所有字段可选，缺失即不渲染：

| 字段 | 类型 | 说明 |
|---|---|---|
| `stage` | `"3-2"` | 阶段 |
| `level` | `int` | 等级 |
| `gold` | `int` | 金币 |
| `hp` | `int` | 血量 |
| `streak` | `"W3"/"L2"` | 连胜/连败 |
| `augments` | `[name]` | 已选海克斯 |
| `pending_augments` | `[name]` | 当前待选三个海克斯 |
| `emblems` | `[name]` | 持有的转职纹章（与 `items` 分开） |
| `items` | `{component, craftable, artifact, support, radiant: [name]}` | 未装备的持有装备，按分类 |
| `board` | `[{unit, star, items: [name], pos: [row, col]}]` | 场上棋子；`row 0` = 前排，`row` 0-3 / `col` 0-6 |
| `bench` | `[{unit, star, items?}]` | 备战席棋子 |
| `shop` | `[name]` | 当前 5 格商店 |
| `opponent` | `{board: [{unit, pos, star?, items?}], note?, hp?}` | 对手信息（决赛圈针对用） |
| `note` | `str` | 自由备注 |

---

## 3. 视觉服务 API（`:8010`）

详见 `vision/API.md`，此处为接口速查。模型进程启动时加载一次并保持热驻留。可选鉴权：设置环境变量 `VISION_API_KEY` 后，所有识别请求必须携带匹配的 `X-API-Key` 头，否则 `401 {"detail": "invalid api key"}`。

### 3.1 `GET /api/health`

响应：`{"ok": true}`

### 3.2 `POST /api/recognize` — 截图识别

- 请求：`multipart/form-data`：`image=<截图文件>`、`mode=ally|enemy|augment`（默认 `ally`）。
- 尺寸校验：只接受 **1920x1080**，否则返回 `{"error": "Expected a 1920x1080 screenshot, got (w, h)"}`。
- 响应：`{"mode", "state", "raw"}`
  - `state`：与主后端 `/api/chat` 的 `state` 同构，可直接转发：
    - `ally`（我方棋盘）：`stage/level/gold/hp/streak` + `board`（棋子/星级/装备/站位）+ `bench` + `shop`（5 格，棋子或奇遇）+ `items`（备战席未装备散件，按分类；重铸器/拆卸器等消耗品被排除；光明装按 `Radiant` 后缀或"光明"前缀归入 `radiant`）。
    - `enemy`（敌方棋盘）：`stage` + `opponent: {board: [{unit, pos, star, items?}], hp?}`；对手备战席不可靠，仅取 `board_` 格；**不支持识别对手装备**（见 `known_issues.md`）。
    - `augment`（海克斯选择界面）：`stage` + `hp` + `pending_augments`（3 个候选）。
  - `raw`：含置信度的完整识别结果，供 UI 展示/人工校正。建议重要决策前让用户核对低置信字段（奇遇、纹章、光明装）。

### 3.3 服务配置接口

- `GET /api/config` → 当前 `{host, backend_port, vision_port, web_gui}`（默认 `{127.0.0.1, 8000, 8010, true}`，读取 `service_config.json`，路径可用 `TFT_CONFIG_PATH` 覆盖）。
- `POST /api/config` body 为部分 JSON：仅接受 `host/backend_port/vision_port/web_gui` 四键；端口必须能转 int，否则 `{"ok": false, "reason": "ports must be numbers"}`。成功返回 `{"ok": true, "config", "note": "saved, takes effect after restart"}`（**重启后生效**）。
- `GET /settings` → 内置的设置页 HTML（改端口 / 开关 web GUI）。

---

## 4. LLM 层与工具

### 4.1 供应商与客户端（`backend/llm.py`）

- 支持两类供应商：**OpenAI 兼容**（DeepSeek，`openai` SDK + `base_url`）与 **Anthropic 官方 SDK**（Claude，配置项 `kind: anthropic`）。
- 客户端按 `(provider, model, api_key)` 三元组缓存（`_clients`）；`allow_user_key: false` 时请求里的 provider/model/api_key 一律被忽略，强制走服务端默认。
- 优先级：请求参数 > 环境变量 `LLM_PROVIDER` / `<PROVIDER>_MODEL` > `config.yaml`。
- key 解析：用户 key 优先，否则读 `api_key_env` 指定的环境变量；两者皆无则抛错（经 SSE `error` 事件返回前端）。
- 请求指定的 `model` 必须在该供应商 `models` 可选列表内，否则报错。

### 4.2 深度思考 `deep`

- OpenAI 兼容：切换到 `model_deep`（如 DeepSeek reasoner）；**reasoner 类深度模型不挂工具**（`model_deep != model` 时 `use_tools=False`）。
- Anthropic：`thinking: {"type": "adaptive"}` + `output_config: {"effort": "high"}`，工具照常可用。

### 4.3 模型可调用的确定性工具（`TOOL_DEFS` → `TOOL_HANDLERS`）

| 工具 | 参数 | 实现 | 用途 |
|---|---|---|---|
| `lookup_details` | `names: [str]` | `Knowledge.lookup` | 查询棋子/羁绊/装备/海克斯详细数值；支持中文名、简称黑话（`data/aliases_cn.json`）、子串模糊匹配，一次可查多个 |
| `verify_comp` | `units: [str]`, `emblems: [str]?` | `Knowledge.analyze_comp` | 阵容羁绊核验：精确计算每羁绊数量/激活档位，标出溢出与未激活 |

`analyze_comp` 规则：同名棋子只计一次；拉克丝形态为其可变羁绊 +2；纹章 +1；召唤棋子（如永恒之森植物）不计羁绊；结论为"存在浪费/未激活 —— 需要调整"或"所有羁绊都正好落在档位上"。系统提示中规定**给出终局阵容前必须先 verify_comp**。

### 4.4 知识分层（`backend/knowledge.py`）

| 层 | 内容 | 去向 |
|---|---|---|
| 常驻层 | `knowledge/general_strategy.md`（手写跨赛季策略）+ `resident_pack.md`（赛季速查）+ `meta_comps.md`（Meta 梯度，可选） | system prompt，字节稳定以命中上下文缓存 |
| 按需层 | 状态中首次出现实体的详细数据（`details_block`） | user 消息，一局每实体只注入一次（`injected` 集合跟踪） |
| 工具层 | `lookup_details` / `verify_comp` | 模型主动调用 |

数据来自 `data/packs/set<N>/`（champions/traits/items/augments/summoned_units/version JSON），简称别名来自 `data/aliases_cn.json`。

---

## 5. 配置项汇总

**`config.yaml`**：`set` / `region` / `locale`（数据包赛季、服务器、语言）、`persist_sessions`、`llm.provider` / `allow_user_key` / `providers.<name>.{kind, base_url, api_key_env, model, model_deep, models}`。

**环境变量 / `.env`**（启动时加载 `.env`，已存在的环境变量优先、不覆盖）：

| 变量 | 作用 |
|---|---|
| `LLM_PROVIDER` | 覆盖默认供应商 |
| `<PROVIDER>_MODEL`（如 `DEEPSEEK_MODEL`） | 覆盖该供应商默认模型 |
| `api_key_env` 指定的变量 | 服务端 LLM API key |
| `TFT_WEB_GUI=0` | 关闭 `/` 网页托管（API 仍可用） |
| `VISION_API_KEY` | 视觉服务鉴权（仅视觉服务） |
| `TFT_CONFIG_PATH` | `service_config.json` 路径覆盖（仅视觉服务） |

---

## 6. 错误处理一览

| 场景 | 表现 |
|---|---|
| 无可用 LLM key | SSE `{"type":"error","text":"没有可用的 API key..."}` |
| 未知供应商 / 模型不在可选列表 | SSE `error` 事件（`RuntimeError` 文案） |
| `session_id` 不存在 | 静默新建会话，首个事件回传新 id |
| 非法 `action` | 忽略，不注入指令 |
| 模型未输出 JSON 块 | `done` 事件的 `structured: null`；JSON 解析失败同 |
| 截图尺寸非 1920x1080 | `{"error": "Expected a 1920x1080 screenshot, got (w, h)"}` |
| 视觉服务 key 不匹配 | HTTP 401 `{"detail": "invalid api key"}` |
