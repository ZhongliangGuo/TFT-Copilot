# 视觉链路性能优化 — 测试方案

> 适用分支：`perf/latency`。配套文档：`docs/latency-analysis.md`（剖析数据与实施记录）。
> 优化提交：`98caca4 perf: cut vision recognize latency ~10x (33s->3.7s)`，基线 commit：`6e14bd2`。
> 范围：**只覆盖视觉链路**（`98caca4` 只改 `vision/`）；对话链路见 `latency-analysis.md` §2，不在本方案验收范围内。
>
> 第 1 节锚点、§2/§3 的命令均已在本机实跑核对（2026-09-23）；本版修正说明见 §8。

## 0. 测试目标与通过标准

| 维度 | 验证内容 | 通过标准 |
|---|---|---|
| 性能（热轮） | 三种模式识别耗时 | ally ≤ 5s、enemy ≤ 4s、augment ≤ 2s |
| 性能（冷启动） | 预热结束后首个请求耗时 | ≤ 6s（优化前 ~27s） |
| 正确性 | 识别结果与基线一致性 | 血量/棋子/星级/装备/海克斯逐项一致 |
| 稳定性 | 同模式连续 5 次耗时波动 | max/min ≤ 1.3 且 max ≤ 热轮标准，无报错 |

**前置条件（否则上表不成立）**：第 1 节环境检查必须通过。本机实测过"同一份代码热轮 18.8s / 12.9s / 8.3s"（参考值 3.7 / 2.7 / 1.3 的约 5 倍），原因是当时系统内存占用 89%、空闲仅 1.7GB —— 这类偏差属于环境，不是代码回退。

参考数值（同机 1920x1080 示例图实测，见 latency-analysis.md §1.5）：

| 模式 | 基线 | 优化后（热轮） | 提速 |
|---|---|---|---|
| ally 我方棋盘 | ~33 s | ~3.7 s | ~9× |
| enemy 敌方棋盘 | ~28 s | ~2.7 s | ~10× |
| augment 选海克斯 | ~17 s | ~1.3 s | ~13× |
| 服务首请求（冷启动） | ~27 s | ~4 s | ~7× |
| 进程启动（模型加载） | ~10 s | ~2.7 s | ~4× |

## 1. 环境准备（公平性前提，最关键）

性能数字对机器负载极其敏感——本机曾出现外部应用打满 CPU / 空闲内存仅 1.7GB 时，同一份代码热轮从 3.7s 退化到 24s。**测试前后必须保持同等环境**。

```powershell
# CPU 占用率（LoadPercentage）应 <20%，空闲物理内存应 >4GB
(Get-CimInstance Win32_Processor | Select-Object -ExpandProperty LoadPercentage)
[math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1MB, 2)
```

- 关闭微信、多余浏览器标签、远程桌面工具等高内存/高 CPU 应用。
- 笔记本接电源，避免省电降频。
- 所有计时均取**第 2 轮（热轮）**数据；第 1 轮含 onnx 首次推理图优化，单独看冷启动项。
- 产物统一写到 `scratch\`（已在 `.gitignore` 中忽略），别散落在仓库根目录。

测试素材（仓库自带，1920x1080，锚点已实跑核对）：

| 文件 | 模式 | 期望结果（校验锚点） |
|---|---|---|
| `examples/my_board.png` | ally | stage=2-6, health=91, gold=24, level=4, 棋子 7（含备战席 3）, bench_items 3 |
| `examples/enemy_board.png` | enemy | stage=3-5, health=101, 棋子 10 |
| `examples/augments.png` | augment | stage=2-1, health=100, 海克斯=单身板甲/英勇福袋/新纪元 |

> 锚点口径：`board` 数组长度即"棋子数"（ally 的 7 个里有 3 个是 `bench_0_*` 格）；`bench_items` 是原始条目数（含消耗品，`to_state` 映射时才剔除消耗品）。

## 2. 方案 A：同机 A/B 对比（最有说服力）

利用 git 在"基线 / 优化后"之间切换，同机同图各跑一遍 profile。

**兼容性前提（已修）**：`scripts/profile_latency.py` 对基线与优化后两版 `vision/` 都可用——
优化后星级定位是 `board._star_locate`，基线是 `board._star_stage` 三元组，脚本自行分支；
基线的 `board._pre`（37 格裁图落盘）会被单独计时（标签 `cv:preprocess`），优化后该属性不存在、该行自然消失。
历史坑：修之前脚本硬取 `board._star_locate`，在基线上会立刻 `AttributeError` 退出（表现为 `baseline.log` 为 0 字节）。

### 路线 1：就地切换（快，务必恢复）

```powershell
cd E:\TFT\TFT-Copilot
mkdir scratch -Force

# ① 基线（把 vision/ 切回优化前）
git stash                                       # 必须: 见下方警告
git checkout 6e14bd2 -- vision/
git status --short vision/                      # 应列出 5 个 M：bench_items / hud_ocr / infer / vision_api / API.md
                                                # (98caca4 只动了前 4 个; 第 5 个 API.md 是本方案 ⑤ 改的)
.venv\Scripts\python.exe scripts\profile_latency.py vision 2>&1 | Tee-Object scratch\baseline.log
# 基线的正确性留档（§3 需要）
.venv\Scripts\python.exe vision\recognize.py examples\my_board.png     ally    --json scratch\before_ally.json
.venv\Scripts\python.exe vision\recognize.py examples\enemy_board.png  enemy   --json scratch\before_enemy.json
.venv\Scripts\python.exe vision\recognize.py examples\augments.png     augment --json scratch\before_aug.json

# ② 优化后（恢复 HEAD）
git checkout HEAD -- vision/
git status --short vision/                      # 必须为空
.venv\Scripts\python.exe scripts\profile_latency.py vision 2>&1 | Tee-Object scratch\optimized.log
.venv\Scripts\python.exe vision\recognize.py examples\my_board.png     ally    --json scratch\after_ally.json
.venv\Scripts\python.exe vision\recognize.py examples\enemy_board.png  enemy   --json scratch\after_enemy.json
.venv\Scripts\python.exe vision\recognize.py examples\augments.png     augment --json scratch\after_aug.json

git stash pop                                   # 如有 stash
```

> **`git stash` 不是可选项**：`git checkout HEAD -- vision/` 会用 HEAD 版本覆盖 `vision/` 下**所有未提交改动**（不是合并）。本次实测踩过——`vision_api.py`、`vision/API.md` 的未提交修改在"切基线再恢复"后被静默丢弃。所以：先把改动提交或 stash，跑完立即 `git status --short vision/` 确认干净。
>
> 恢复命令（`git checkout HEAD -- vision/`）一旦中断没执行，工作区就会停在基线版本——切换期间别提交、别开别的服务。

### 路线 2：另开副本（不动当前工作区，更稳）

```powershell
cd E:\TFT\TFT-Copilot
git worktree add ..\tft-baseline 6e14bd2
copy vision\onnx\*.onnx ..\tft-baseline\vision\onnx\      # 权重不入库（.gitignore），必须复制
copy scripts\profile_latency.py ..\tft-baseline\scripts\  # 该 commit 尚无此脚本
cd ..\tft-baseline
E:\TFT\TFT-Copilot\.venv\Scripts\python.exe scripts\profile_latency.py vision 2>&1 |
  Tee-Object E:\TFT\TFT-Copilot\scratch\baseline.log
# 用完清理：git worktree remove ..\tft-baseline
```

### 检查点

1. 两日志中"第 2 轮（热）"的 `== recognize(ally|enemy|augment)` 总耗时，对比提升倍数。
2. 分段明细差异（**以下标签均为脚本真实产出**）：
   - `ocr:health`：干净环境参考 基线 ~13.8s×3 → 优化后 ~0.3s/张（负载高的机器上改善会被压缩，见下表）；
   - `[计数] RapidOCR rec 次数`（整轮 3 图累计）：口径是"rec 实际执行次数"——基线的 `self.ocr(strip)` 一次调用里 det+rec 全跑，计为**检出的文本框数**（~11~15/图）；优化后改为 det-only 拿候选框 + 按优先级惰性 rec，计为 1~2/图；`text_rec([N])` 计 N。本机实测**基线 50 → 优化后 22**（链路级总数，含其他字段的贡献），血量那条路径的下降主要体现在 `ocr:health` 耗时上；
   - `cv:preprocess (基线:37格裁图落盘)`：基线 ~3.2s，**只在基线日志出现**，优化后该行消失即为预期（内存化）；
   - `onnx:identity` / `onnx:stars`：由每格/每棋子多次调用变为各 1 次批量调用（`x2` = ally+enemy 各一次；augment 不跑棋盘）；
   - `load:BenchMatcher`：基线 4.4~9.6s → 优化后 <0.2s（干净环境 <0.1s，embedding 缓存生效）；
   - 各阶段之和 ≈ 总耗时的差额 = 未单独归类的开销（图像解码、基线裁图读回等）。

> 若优化后 `load:BenchMatcher` 仍 >2s：先确认 `data/vision_dataset/s18-equipment-v4/reference/ref_emb_cache.npz` 是否被删/失效（参考图名单变化会触发一次性重建），否则这次对比会被"重建缓存"污染。

> **本机实跑样例（2026-09-23，负载偏高：系统内存占用 89%、空闲 1.7GB）** —— 当时日志形态如下，绝对值被环境污染，**不是参考数据**，只用来确认"该出现/该消失的标签"：
>
> | 日志行 | 基线（`6e14bd2`） | 优化后（`98caca4`） |
> |---|---|---|
> | `cv:preprocess (基线:37格裁图落盘)` | 3.6s(冷)/5.0s(热) x2 | **整行消失** ✓ |
> | `[计数] RapidOCR rec 次数`（3 图） | 50 | 22 |
> | `ocr:health`（3 图） | 19.2s / 30.9s | 12.2s / 13.2s |
> | `onnx:stars` | x12（每棋子一次） | x2（每图一次批量） |
> | `load:BenchMatcher` | 1.1s / 7.0s（两次跑） | 0.15s |
> | `== recognize(ally/enemy/augment)`（热） | 24.5 / 21.6 / 12.4s | 18.5 / 13.2 / 7.7s |
>
> 也正因为环境噪声（同机两次跑同一版本可差 3 倍），第 1 节的负载检查不能省。

## 3. 方案 B：正确性回归（必做，证明"快且没坏"）

速度快了但识别错了没有意义。**用 `vision/recognize.py`（三模式统一入口）**：`before_*` 在方案 A 第①步状态下产出、`after_*` 在第②步状态下产出，然后逐项 diff：

```powershell
git diff --no-index --stat scratch\before_ally.json scratch\after_ally.json   # 期望：无输出
git diff --no-index --stat scratch\before_enemy.json scratch\after_enemy.json
git diff --no-index --stat scratch\before_aug.json  scratch\after_aug.json
```

**通过标准**：逐字节一致（无输出）。重点核对第 1 节锚点值（health 91/101/100、棋子数 7/10、海克斯三项等）。

若只在 `*_conf` 类浮点上出现末位差异（组批推理与单张推理的数值扰动），用语义比较再确认一次：

```powershell
.venv\Scripts\python.exe -X utf8 -c "import json,sys;f=lambda o:{k:f(v) for k,v in o.items() if not k.endswith('_conf')} if isinstance(o,dict) else [f(x) for x in o] if isinstance(o,list) else o;a,b=[f(json.load(open(p,encoding='utf-8'))) for p in sys.argv[1:3]];print('SAME' if a==b else 'DIFF');sys.exit(0 if a==b else 1)" scratch\before_ally.json scratch\after_ally.json
```

**不要用 `vision/infer.py` 做这一步**：它只做棋盘，`--json` 只有 `image / n_units / board`，**没有** health/gold/stage/bench_items/augments 字段；而且它没有 mode 参数（内部固定 ally），拿敌方图跑会输出 `n_units: 0, board: []` —— before/after 会"一致"，但一致的是空结果，不构成回归证据。`infer.py` 只适合单独调棋盘分类器。

## 4. 方案 C：端到端服务测试（体现"服务预热"效果）

唯一能体现**冷启动改善**的测法。服务启动后后台线程会自动预热（加载子模型 + 用 `examples/` 三张样例跑完整识别），使首个真实请求不再支付 ~27s 的懒加载 + onnx 图优化开销。

```powershell
# 启动视觉服务（仓库根目录）
.venv\Scripts\python.exe -m uvicorn vision.vision_api:app --host 127.0.0.1 --port 8010

# 等预热结束：轮询 health 的 warm 字段，不要死等固定秒数
do { $h = curl.exe -s http://127.0.0.1:8010/api/health | ConvertFrom-Json; if (-not $h.warm) { Start-Sleep -Seconds 2 } } while (-not $h.warm)
"warm=$($h.warm) warmup_ms=$($h.warmup_ms)"

# 预热结束后发"第一个"真实请求并计时（关键指标）
curl.exe -s -o scratch\resp_ally.json -w "ally first: %{time_total}s`n" `
  -F "image=@examples/my_board.png" -F "mode=ally" http://127.0.0.1:8010/api/recognize

# 连续再测稳定性 + 另外两个模式
curl.exe -s -o scratch\ally2.json   -w "ally #2: %{time_total}s`n" -F "image=@examples/my_board.png"    -F "mode=ally"    http://127.0.0.1:8010/api/recognize
curl.exe -s -o scratch\enemy.json   -w "enemy:  %{time_total}s`n"  -F "image=@examples/enemy_board.png" -F "mode=enemy"   http://127.0.0.1:8010/api/recognize
curl.exe -s -o scratch\augment.json -w "augment: %{time_total}s`n" -F "image=@examples/augments.png"    -F "mode=augment" http://127.0.0.1:8010/api/recognize
```

**通过标准**：

- `warm=true` 之后发出的首请求 ≤ 6s（与热轮同量级；优化前 ~27s）。
- 后续请求在参考区间内无显著递增。
- `scratch\resp_ally.json` 中 `raw.health=91`、`raw.board` 7 个棋子、`raw.bench_items` 3 项。

> 注意：
> 1. **必须等 `warm=true` 再计时**。早先 `/api/health` 只返回 `{"ok":true}`，无法判断预热是否结束——预热期间发请求会与预热线程抢 CPU（实测启动 8s 后发请求得 36.8s，紧接着第二次 24.0s，都不代表稳态）。现在 health 返回 `{ok, warm, warmup_ms}`，请以 `warm` 为准。本机实测预热本身花了 `warmup_ms=44826`（44.8s，负载偏高时），所以"固定等 40s"并不保险——只能轮询。
> 2. 不要用 `-o NUL`：cmd/PowerShell 下是空设备，Git Bash 下会真的生成一个名为 `NUL` 的文件；统一写 `scratch\*.json`。
> 3. PowerShell 5.1 的 `Invoke-WebRequest` 不支持 `-Form`，multipart 上传请用 `curl.exe`（Windows 自带）。
> 4. 若设过环境变量 `VISION_API_KEY`，请求需带 `-H "X-API-Key: <key>"`，否则 401。

## 5. 方案 D：全栈联测（可选，贴近真实使用）

```powershell
.venv\Scripts\python.exe run.py    # 同时起后端 8000 + 视觉 8010
```

浏览器打开 `http://localhost:8000`，走一遍真实流程：

1. 粘贴/上传三张示例截图，分别触发三种识别模式；
2. 观察前端响应速度（预热完成后识别结果 ~3~4s 内返回）；
3. 核对前端展示的血量/棋子/海克斯与第 1 节锚点一致；
4. 发起教练对话，确认识别结果正确注入会话。

## 6. 结果记录模板

| 指标 | 基线 | 优化后 | 提升 | 测试环境备注 |
|---|---|---|---|---|
| ally（热轮，5 次 max/min） | | | | CPU 占用 __% / 空闲内存 __GB |
| enemy（热轮） | | | | |
| augment（热轮） | | | | |
| 服务首请求（warm=true 后） | | | | warmup_ms=____ |
| 进程启动（模型加载） | | | | |
| `[计数] RapidOCR rec 次数`（每图） | | | | |
| 正确性回归（3 图 diff） | — | 通过 / 不通过 | — | |

## 7. 常见问题

| 现象 | 排查 |
|---|---|
| 耗时突然变 20s+ | 先查 CPU/内存（见第 1 节），大概率是机器被其他应用占满，不是代码回退 |
| 基线跑 profile 报 `AttributeError: _star_locate` | 用的是旧脚本（不兼容基线）。换成当前 `scripts/profile_latency.py` |
| 做完 A/B 后 `vision/` 的改动不见了 | `git checkout HEAD -- vision/` 覆盖了未提交改动。跑之前先 `git stash`（或改用路线 2 的 worktree 副本），跑完 `git stash pop` |
| 优化后日志里找不到 `cv:preprocess` | **正常**：该阶段已内存化，这一行只在基线日志出现 |
| 日志里看不到 rec 次数 | 用本版脚本，每个 `summary` 底部会有 `[计数] RapidOCR rec 次数` |
| BenchMatcher 加载又变慢 | 删除过 `ref_emb_cache.npz` 会触发一次重建（一次性，之后恢复 <0.2s） |
| 服务首请求仍慢 | 确认 `warm=true` 后再发；`examples/` 缺失时预热退化为合成黑图，只覆盖 onnx 路径 |
| 结果与基线不一致 | 保留两份 json 与日志，按 latency-analysis.md §1.5 的改动清单逐项定位 |

## 8. 本版修订记录（相对初稿）

| # | 问题 | 修订 |
|---|---|---|
| ① | 方案 A 在基线上必崩（脚本硬取 `board._star_locate`，基线是 `_star_stage`），表现为 `baseline.log` 0 字节 | 修 `scripts/profile_latency.py`：双版本兼容 + 恢复命令写进文档；新增"另开副本"路线 |
| ② | 方案 B 用 `vision/infer.py` 测不到锚点（无 HUD/海克斯字段、无 mode，敌方图恒为 0 棋子） | 改用 `vision/recognize.py <图> <模式> --json`；判据改为逐字节一致，并给出忽略 `*_conf` 的语义比较命令 |
| ③ | 第 1 节"CPU 空闲率应 <20%"与命令矛盾（`LoadPercentage` 是占用率） | 改为"CPU 占用率 <20%"，内存判据显式写成 `>4GB` |
| ④ | 检查点的 `cv:preprocess`、`rec 次数` 脚本不产出；`load:BenchMatcher` 阈值两处不一致（<0.1s / <0.2s） | 脚本补 preprocess 计时与 rec 计数两个埋点；阈值统一为 <0.2s（干净环境 <0.1s）；检查点只保留脚本真实产出的标签 |
| ⑤ | `/api/health` 无预热状态，"等 40s"不可验证，冷启动判据不可复现 | `vision_api.py` 的 health 增加 `warm` / `warmup_ms`；文档改为轮询 `warm=true` 后再计时（`vision/API.md` 已同步） |
| ⑥ | 产物写在仓库根目录，`-o NUL` 在 Git Bash 下会生成垃圾文件 | 统一输出到 `scratch\`（已加入 `.gitignore`）；稳定性判据量化为 max/min ≤ 1.3 |
| ⑦ | 切版本做 A/B 会静默丢弃 `vision/` 下未提交的改动（`git checkout HEAD -- vision/` 是覆盖而非合并） | §2 明确"`git stash` 是必做项"，补 §7 排查行；另提供不切工作区的 worktree 路线 |

> ⑦ 是这次写方案时亲历的：先改了 `vision/vision_api.py`，随后为验证方案 A 切基线再恢复，改动被覆盖丢失，不得不重做——按本方案执行时请先 stash/提交。
