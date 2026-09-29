# TFT Copilot 前端界面分析

> 依据 `frontend/index.html`、`frontend/app.js`、`frontend/i18n.js`、`frontend/style.css` 源码整理。

## 1. 技术形态

- **零框架纯静态前端**：原生 HTML/CSS/JS（`"use strict"`，无构建步骤），由主后端 `:8000` 以 `/static` 托管，入口 `GET /`。
- 仅 4 个文件：`index.html`（结构）、`style.css`（样式）、`app.js`（全部逻辑，约 740 行）、`i18n.js`（轻量中英文切换）。
- 图标全部来自 CommunityDragon CDN（`raw.communitydragon.org`），加载失败时 `onerror` 隐藏 `<img>` 降级为纯文字。
- 响应式：`#app` 为 flex 双栏；`@media (max-width: 900px)` 时转为纵向单栏（适配手机），棋盘格宽度改为 `12vw`。

## 2. 整体布局

```
#app (flex, 100vh)
├─ #input-pane (flex:1.15, 左栏, 可滚动) —— 局面输入
│   ├─ .pane-head      标题 / 数据版本 / 截图模式下拉 / 📷截图识别 / ⚙视觉服务配置 / 新开一局 / 语言切换
│   ├─ #sec-status     状态：阶段、等级、金币、血量、连胜/败
│   ├─ #sec-units      我的棋子：搜索+浏览面板、棋子列表、4x7 棋盘
│   ├─ #sec-items      持有装备/纹章：六类 Tab、搜索、统一归属列表
│   ├─ #sec-augments   海克斯：已选 + 待选三选一
│   ├─ #sec-opponent   对手信息（checkbox 展开）：对手棋子、对手棋盘、备注
│   └─ #sec-note       自由备注
└─ #chat-pane (flex:1, 右栏, flex 纵向) —— 教练对话
    ├─ .pane-head      标题 / 深度思考开关 / API 按钮 / 会话 ID
    ├─ #api-panel      供应商 / 模型 / API Key 配置（默认隐藏）
    ├─ #chat-log       消息流（SSE 实时渲染）
    ├─ #target-board   常驻目标阵容板（阵容码、羁绊核验、mini 棋盘）
    ├─ #quick-actions  6 个快捷指令按钮
    └─ #chat-input-row 追问输入框（Ctrl+Enter 发送）+ 发送按钮
```

## 3. 左栏：局面输入（`#input-pane`）

### 3.1 顶栏 `.pane-head`

| 控件 | 行为 |
|---|---|
| `#data-version` | 显示数据包构建时间（`DATA.version.built_at`） |
| `#rec-mode` | 截图类型三选一：`ally` 我方棋盘 / `augment` 选海克斯 / `enemy` 敌方棋盘 |
| `#btn-recognize` | 触发文件选择（PNG/JPEG）；识别期间禁用并显示"识别中…" |
| `#btn-vcfg` ⚙ | 两个 `prompt` 依次设置视觉服务地址与 API Key，存 `localStorage: vision_api / vision_key`（默认 `http://localhost:8010`） |
| `#btn-new-game` | `confirm` 后清空局面状态、聊天记录、目标板，新建会话 |
| `#btn-lang` | 中/EN 切换，写 `localStorage: tft_lang` 后整页刷新 |

另注册全局 `paste` 监听：剪贴板含图片时**直接 Ctrl+V 粘贴即识别**（按当前 `#rec-mode`）。

### 3.2 状态 `#sec-status`

五个输入框：`stage`（如 3-2）、`level`（1-10）、`gold`、`hp`（0-100）、`streak`（W3/L2）。`oninput` 即写入状态并落 `localStorage`。

### 3.3 我的棋子 `#sec-units`

- **搜索框 `#unit-search`**：通用搜索组件 `bindSearch`（见 §6.1）。无输入聚焦时展开**浏览面板**（费用 0-5 筛选 + 羁绊筛选，均为可保留的二级菜单）；有输入时按中文名/拼音/首字母匹配（`DATA.search` 别名表），最多 12 条候选。
- **多形态棋子**（如拉克丝，名字后带 ◈）：第一次点击把输入框改写为 `"名字 ("` 展开形态列表；选具体形态加入，再点基础名则按"未定形态"加入（`formAwarePick`）。
- **棋子列表 `#unit-list`**：每行显示图标、费用配色名、星级（点击 ★ 循环 1/2/3 星）、已装备装备（点击移除）、⚔ 配装按钮（点击后到装备搜索里选装备给它）、位置按钮（进入"摆放模式"再点棋盘格）、× 删除。
- **棋盘 `#my-grid`**：4 行 × 7 列，奇数行右移 22px 模拟六边形错位；上为前排（`row 0`）。摆放模式下点格子落子；点已有棋子的格子则移回备战（`pos=null`）。

### 3.4 装备 `#sec-items`

- 六个分类 Tab：散件 `component` / 成装 `craftable` / 神器 `artifact` / 辅助装 `support` / 光明装 `radiant` / 纹章 `emblem`（决定搜索结果的默认归类与浏览面板内容）。
- 搜索选中时：若存在"待配装棋子"（`assignTarget`）则装到该棋子，否则进库存对应分类。
- **统一归属列表 `#item-pool`**：库存 + 已装备装备合并展示，每件右侧下拉框可在"未装备 / 各棋子"之间转移归属（`moveHeld`），× 删除。

### 3.5 海克斯 `#sec-augments`

- 搜索/浏览面板（银/金/彩 tier 筛选 + "羁绊转职类"开关）。点击结果默认加入**已选**；按住 **Shift 点击**或点条目旁的 `?` / `三选一?` 加入**待选三选一**（上限 3 个）。
- `已选 #aug-owned` 与 `待选 #aug-pending` 均为 chip 列表，× 删除。

### 3.6 对手信息 `#sec-opponent`

- checkbox 展开；识别到敌方棋盘时会自动勾选展开。
- 对手棋子只记录 `unit/pos/star/items`（UI 支持手动补装备，但视觉服务不识别对手装备）；棋盘与我方同构（上=对手前排，镜像语义）。
- `#opp-note` 对手备注 + `#sec-note` 自由备注，均随 state 发给模型。

## 4. 右栏：教练对话（`#chat-pane`）

### 4.1 顶栏与 API 配置

- **深度思考 `#deep-mode`**：勾选后 `/api/chat` 携带 `deep: true`（后端切深度模型/高 effort）。
- **API 按钮 `#btn-api`**：仅 `config.allow_user_key` 为 true 时显示；面板内选供应商、模型（标注默认项）、填 key。偏好存 `localStorage: tft_api_prefs`（含按供应商分的 `keys` 字典，**只存在用户自己的浏览器**），另有兼容旧键 `tft_api_key`。状态行提示当前在用"你的 key / 服务端 key / 无可用 key"；当前供应商既无服务端 key 也无用户 key 时面板自动展开；`allow_user_key=false` 且服务端无 key 时在消息流提示。
- `#sess-label` 显示会话 ID（`MMDD-HHMMSS-xxxx`）。

### 4.2 消息流 `#chat-log`

- 消息类型（CSS class）：`user`（蓝边）、`assistant`（金边）、`tool`（🔍 斜体小字，展示工具调用如"查询: 剑圣"/"核验阵容: …"）、`error`（红边）。
- 发送：快捷按钮发 `{action}`，输入框发 `{message}`（Ctrl/Cmd+Enter）；两者都会附带 `collectState()` 的全量局面、`deep`、API 偏好。
- **SSE 消费**（`fetch` + `ReadableStream` 手动解析 `data:` 帧）：
  - `session` → 更新并持久化会话 ID；
  - `delta` → 累积文本，经 `mdLite` 轻量渲染（去 ```json 块、代码块去围栏、HTML 转义、`**粗体**` 与 `#标题` → `<b>`、`white-space: pre-wrap` 保留换行）；
  - `tool` → 追加 tool 消息；`error` → 追加错误消息；`done` → 渲染目标阵容板。
- 滚动跟随策略：距底 60px 内才算"跟随中"，用户上翻历史时新内容不强制拉回；自己发消息总是跳到底。

### 4.3 常驻目标阵容板 `#target-board`

- 数据源为 `done` 事件的 `structured`：`target_comp`（棋子/星级/装备行）+ `trait_check`（服务端兜底核验逐行；含"溢出/无法识别/需要调整/未激活(差"的行标红，其余绿色）+ `team_code`（📋 一键复制，游戏内粘贴导入；剪贴板 API 失败时降级为 `prompt` 手动复制）。
- `positioning` 渲染为 mini 棋盘（34px 格）。**沿用逻辑**：新一轮回答省略 `positioning` 或 `target_comp` 时沿用上次（`lastTarget`），与后端"没变化不输出 json"的协议对应。
- × 关闭仅隐藏，下次 `done` 仍会重渲染。

### 4.4 快捷指令 `#quick-actions`

6 个按钮一一对应后端 `ACTIONS`：推荐阵容 / 海克斯三选一 / D牌还是存钱 / 站位建议 / 针对对手 / 要不要转型。

## 5. 前端状态模型与持久化

内存状态 `S`（`EMPTY()` 初始化）：

```jsonc
{
  "status": { "stage": "", "level": "", "gold": "", "hp": "", "streak": "" },
  "units":  [{ "unit", "star", "items": [], "pos": [r, c] | null }],  // 场上+备战合一, pos=null 即备战
  "pool":   { "component": [], "craftable": [], "artifact": [], "support": [], "radiant": [], "emblem": [] },
  "augments": [], "pending": [],          // 已选 / 待选三选一海克斯
  "opp":    { "units": [], "note": "", "hp": null },
  "shop":   [], "note": ""
}
```

发给后端时经 `collectState()` 转换：纹章从 `pool.emblem` **拆回独立的 `emblems` 字段**（UI 合并管理、API 契约分开）；`units` 按有无 `pos` 拆成 `board`/`bench`；对手块仅在勾选且有内容时附带。旧版存档的 `emblems` 字段在加载时自动迁移进 `pool.emblem`。

**localStorage 键一览**：

| 键 | 内容 |
|---|---|
| `tft_state` | 局面状态 `S`（每次变更即写） |
| `tft_session` | 会话 ID（跨刷新续聊） |
| `tft_lang` | 界面语言 `zh`/`en` |
| `tft_api_prefs` | 供应商/模型选择 + 各供应商的用户 key |
| `tft_api_key` | 旧版单 key 存储（清除时一并删） |
| `vision_api` / `vision_key` | 视觉服务地址 / API Key |

注意：聊天记录不存前端，刷新后对话区清空但会话仍在服务端（`persist_sessions` 开启时可续）。

## 6. 关键机制

### 6.1 通用搜索组件 `bindSearch`

四个搜索框（棋子/装备/海克斯/对手棋子）共用：输入过滤（≤12 条）→ 点击 `onPick`；面板 `mousedown` 拦截防 blur 丢失焦点；`Esc` 关闭；失焦 150ms 延迟收起；无输入聚焦时展示 `browseFn` 二级浏览菜单（筛选状态跨开合保留）。匹配函数 `matches` 支持官方名子串 + `DATA.search` 别名（拼音/首字母），`name+" ("` 的小技巧让基础形态也能被形态展开查询命中。

### 6.2 截图识别流程

```
选择/粘贴图片 → POST {vision_api}/api/recognize (multipart: image, mode, X-API-Key?)
  → 成功: applyRecognized() 按 mode 合并进 S（augment→pending; enemy→opp+自动展开; ally→全量覆盖）
         + 消息流插入识别摘要 recapHtml()（提示"核对后再发问"）
  → 失败: alert（含视觉服务地址提示）
```

### 6.3 i18n（`i18n.js`）

- 无字典文件：静态文案以 `data-zh` / `data-en`（及 `data-*-title` / `data-*-ph`）写在 HTML 元素上，`applyStaticI18n()` 一次性套用；动态文案用 `T("中文", "English")`。
- 默认语言：`localStorage` > 浏览器语言（`zh*` → 中文）。
- 范围仅限 UI 骨架；游戏数据名与教练回复当前只有中文。

### 6.4 样式体系（`style.css`）

- CSS 变量定义暗色主题：`--bg #0f1420`、`--panel #171e2e`、`--accent #c8a24a`（金）、`--accent2 #4a9ac8`（蓝）、`--danger/--ok`。
- 棋子费用配色：1 费灰 / 2 费绿 / 3 费蓝 / 4 费紫 / 5 费金（`.cost1-5`），与游戏内一致。
- 字体优先 PingFang SC / Microsoft YaHei。

## 7. 健壮性与交互细节

- 所有状态变更立即 `saveState()`，误刷新不丢局面。
- `confirm` 防误触"新开一局"；识别失败、请求失败、SSE error 均有用户可见提示。
- 发送期间禁用发送按钮防重入。
- 对手棋盘"摆放模式"与我方共用 `placeTarget`，以 `list: my|opp` 区分。
- 待选海克斯上限 3 个；装备分类查不到时回落 `craftable`。
