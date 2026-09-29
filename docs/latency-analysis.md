# 链路延迟追踪与分析

> 测量日期 2026-09-21，工具：`scripts/profile_latency.py`（运行时装潢计时，不改生产代码）。
> 环境：Windows，onnxruntime **CPU 版**（无 CUDA），LLM = DeepSeek（`deepseek-v4-flash`），示例图 = `examples/` 三张 1920x1080 截图。

## 结论速览

| 链路 | 端到端耗时 | 最大头 |
|---|---|---|
| 截图识别·我方棋盘 (ally) | **~33 s** | 血量 OCR 14 s（42%）+ 棋子分类 6.4 s（20%） |
| 截图识别·敌方棋盘 (enemy) | **~28 s** | 血量 OCR 14 s（49%）+ 棋子分类 6.4 s |
| 截图识别·选海克斯 (augment) | **~17 s** | 血量 OCR 14 s（**81%**） |
| 教练对话·会话第 1 轮（缓存冷） | **25~45 s** | DeepSeek 处理 18k 字符常驻 system prompt + 工具调用多轮 |
| 教练对话·同会话第 2 轮（缓存热） | **~7 s** | LLM 生成本身 |

---

## 1. 视觉链路（`/api/recognize`，本地 CPU 推理）

### 1.1 分阶段实测（热轮，模型已驻留）

| 阶段 | ally | enemy | augment | 说明 |
|---|---|---|---|---|
| `ocr:health`（右侧玩家列表 256×653，**det 检测式 OCR**） | ~13.8 s | ~13.8 s | ~13.8 s | **最大瓶颈** |
| `onnx:identity`（37 格批量分类） | ~6.4 s | ~6.4 s | — | 81MB 模型，CPU |
| `cv:preprocess`（切 37 格 + 写临时文件） | ~3.2 s | ~3.2 s | — | |
| `onnx:equipment`（每棋子 1 次 ×6） | ~3.3 s | — | — | 逐棋子串行 |
| `onnx:stars`（每棋子 1 次 ×12） | ~0.4 s | ~0.4 s | — | |
| `ocr:shop_1..5` + level/xp/gold/streak/stage | ~2.3 s | ~0.5 s | ~0.5 s | 小区域 rec-only，每个 0.2~0.5 s |
| `ocr:augment_1..3` | — | — | ~1.4 s | 含放大重试 |
| `onnx:bench_feat`（备战席每占用槽 1 次） | ~0.2 s | — | — | |
| **合计** | **~32.6 s** | **~28.2 s** | **~17.0 s** | |

冷轮（onnx 首次推理含图优化）比热轮再慢 10~25%（ally 42 s vs 33 s）。

### 1.2 进程启动（懒加载，每进程一次）

| 加载项 | 耗时 |
|---|---|
| HudOCR（RapidOCR 初始化） | 0.6~1.1 s |
| BoardRecognizer（identity+stars+equipment 三个 onnx） | 1.4~3.5 s |
| BenchMatcher（onnx + **137 张参考图逐张跑 embedding**） | **4.4~9.6 s** |

### 1.3 分析

- **`_health()` 用整面检测式 OCR**（`use_det=True`）扫 256×653 的右侧竖条来找"字号最大的纯数字"，单次 ~14 s，占三种模式总耗时的 42%~81%。同文件里其他字段都是 rec-only 小区域（0.2~0.5 s），差距 30 倍以上。
- 所有 OCR 字段和 CV 阶段**完全串行**（`hud.parse` 逐字段、board/bench 依次）。
- `preprocess` 把 37 个格子裁图**写临时文件**再读回，磁盘往返约 3 s。
- `equipment`/`stars`/`bench_feat` 逐棋子/逐槽单张推理，未组 batch。
- BenchMatcher 启动时对 137 张参考图逐张推理，可离线预计算缓存。

### 1.4 优化建议（按收益排序）→ **已于 perf/latency 分支实施，见 §1.5**

1. **血量识别去检测化**（已实施，实际方案比建议更保守：保留 det 拿候选框、按优先级惰性 rec，见 §1.5-③）。
2. 换 **onnxruntime-gpu**（有 N 卡时）：identity/equipment/bench 全部受益，预计 identity 6.4 s → <1 s。**（未做，环境改动）**
3. `preprocess` 裁图走内存（PIL crop → numpy），省掉 37 次写盘/读盘。**（已实施）**
4. `stars`/`equipment`/`bench_feat` 组 batch 一次推理。**（已实施；equipment.onnx 固定 batch=1 除外）**
5. BenchMatcher 参考 embedding 预计算落盘（`ref_emb_cache.npz`），启动省 4~9 s。**（已实施）**
6. OCR 各字段之间无依赖，可多线程并发。**（未做：优化后 OCR 占比已很小，CPU 受限收益有限）**

### 1.5 优化实施与复测（2026-09-23，perf/latency 分支）

实施的改动（`vision/` 共 4 个文件）：

| # | 改动 | 文件 | 收益 |
|---|---|---|---|
| ① | 棋盘预处理全程内存化（裁图不落盘），identity 37 格 / stars 全部组批一次推理 | `vision/infer.py` | preprocess ~3.2 s → ~0；onnx 调用 40+ 次 → 3 次 |
| ② | 简单 HUD 字段（stage/level/xp/gold/streak/shop×5）组批一次 `text_rec` | `vision/hud/hud_ocr.py` | 小字段 10 次流水线调用 → 1 次 |
| ③ | **血量惰性 rec**：det-only 拿候选框（新增 `x0≥100` 过滤名字区 + 剑标配对过滤），ally/augment 按字高降序、enemy 先定位自己再按 x 升序，rec 到第一个纯数字即止 | `vision/hud/hud_ocr.py` | rec 次数 11~15 → 1~2，血量 ~10.4 s/张 → ~0.3 s/张 |
| ④ | 备战席参考 embedding 缓存 `ref_emb_cache.npz`（名字校验自动失效重算）+ 槽位批量推理 | `vision/hud/bench_items.py` | 进程启动 4~9.6 s → <0.1 s |
| ⑤ | 服务预热：后台线程预载模型后，用 `examples/` 三张样例各跑一遍完整识别（onnx 首次推理的图优化也在后台支付；样例缺失时退化合成黑图） | `vision/vision_api.py` | 首请求不再出现 ~27 s 冷启动 |

复测（同机同图，热轮；校验识别结果与基线完全一致）：

| 模式 | 基线 | 优化后 | 提速 |
|---|---|---|---|
| ally | ~33 s | **~3.7 s** | ~9× |
| enemy | ~28 s | **~2.7 s** | ~10× |
| augment | ~17 s | **~1.3 s** | ~13× |

踩坑记录：
- RapidOCR 的 rec 对小图也有 ~0.2~0.9 s/张固定开销，`text_rec` 列表组批**摊不薄**（11 框 9.4 s）——减次数比组批重要。
- det 换 `det_limit_type="max"` 可快 5~8 倍（0.6 s → 0.08 s），但**会漏检金底高亮行的自己血量框**（暗字亮底召回差），弃用。
- 纯 CV（连通域/Otsu/行高亮）替代 det 的尝试在三张样例上边界情况过多（选海克斯时整体变暗、剑标黏连、金底黑字），不可靠，弃用；保留 det + 惰性 rec 是精度零风险的方案。
- 复测时若耗时异常偏高，先确认机器无其他高负载进程（本机曾被外部应用打满 CPU，同代码热轮 3.7 s ↔ 24 s）。

---

## 2. 对话链路（`/api/chat`，SSE）

### 2.1 提示词规模（静态实测）

| 部分 | 大小 |
|---|---|
| system prompt（常驻层：通用策略 + 赛季速查 + meta，进 DeepSeek 上下文缓存） | **18,177 字符 ≈ 9k~12k token** |
| 首轮按需注入 details_block（局面涉及实体的详细数值） | 2,354 字符 |
| render_state（当前局面） | 170 字符 |

### 2.2 实测时间线（DeepSeek，非 deep 模式）

会话第 1 轮（`action=recommend`，含首次注入、缓存冷；跑两次取区间）：

```
0 ms        POST /api/chat
  │         ├ 服务端组装消息: diff + render_state + details_block  <10 ms (本地)
  │         ▼
~22~43 s    第 1 次 LLM 调用结束: 模型决定调 verify_comp (核验阵容 x2)
  │         ├ 工具执行 (本地 analyze_comp)  <10 ms
  │         ▼
+2 s        第 2 次 LLM 调用首 token (上下文已在缓存, 快)
+1~3 s      正文生成 ~720 字 / 350 delta
≈ 25~45 s   done (含服务端兜底 trait_check + team_code, <10 ms)
```

会话第 2 轮（自由追问，常驻 prompt 命中 DeepSeek 上下文缓存）：

```
0 ms        POST /api/chat
5.6 s       首 token (TTFT)
7.0 s       done (~780 字)
```

对照组（本地开销）：`POST /api/session` 27~53 ms，`GET /api/data` 13~21 ms（200 KB），均可忽略。

### 2.3 分析

- **时间几乎 100% 花在 DeepSeek API 上**，本地服务端（组消息、工具执行、羁绊核验、阵容码）合计 <50 ms。
- **首轮 vs 次轮差距 3~6 倍**（25~45 s vs 7 s）：首轮要把 ~1 万 token 的常驻 prompt 全量处理；次轮起命中上下文缓存，TTFT 降到 5.6 s。DeepSeek 服务端负载波动也大（两次冷轮分别 44 s / 25 s）。
- **工具调用会倍增往返**：模型每发起一轮 tool_use，就得多一次"全上下文"调用。本轮实测模型在推荐时调了 1 轮 `verify_comp`（+2 s）；局面复杂时最多 4 轮，每轮都是数秒级。
- 生成速度约 500~700 字/s（热），正文 700 字本身只要 1~3 s。
- 前端渲染、SSE 解析无可见开销。
- `deep=true` 会切换到 `deepseek-v4-pro` + 思考，预期更慢（本次未测）；Claude 路径 thinking=high 同理。

### 2.4 优化建议（按收益排序，未实施）

1. **引导用户复用同一会话追问**（前端已做：`tft_session` 持久化），新开一局才换会话 —— 这是当前最大的实际加速来源（45 s → 7 s）。
2. **压缩常驻 prompt**：`resident_pack.md`/`meta_comps.md` 是字节大头，可按局面裁剪（如只放与当前羁绊相关的档位表），或接受略降质量换 TTFT。注意改动会破坏缓存稳定性，需字节稳定才有缓存收益。
3. **减少工具轮次**：system prompt 已要求"给终局阵容前必须 verify_comp"，可进一步明确"至多核验 1~2 次"，避免反复核验；或在服务端把模型首轮输出的阵容直接核验并附结果（`attach_trait_check` 已有兜底），放宽对模型自调的强制。
4. 非 deep 模式用更快的模型档位（如 flash 已在用）；把 `deep` 作为用户可预期的"慢但准"选项即可。
5. 首 token 前前端可加"正在分析局面…"的即时反馈（当前有 `…` 占位，体验已覆盖）。

---

## 3. 复测方法

```bash
# 视觉链路 (本地, 无需网络)
.venv/Scripts/python scripts/profile_latency.py vision

# 对话链路 (先起后端再跑)
.venv/Scripts/python -m uvicorn backend.app:app --port 8123
.venv/Scripts/python scripts/profile_latency.py chat --base http://127.0.0.1:8123
```
