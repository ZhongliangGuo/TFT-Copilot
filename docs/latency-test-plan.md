# 视觉链路性能优化 — 测试方案

> 适用分支：`perf/latency`。配套文档：`docs/latency-analysis.md`（剖析数据与实施记录）。
> 优化提交：`98caca4 perf: cut vision recognize latency ~10x (33s->3.7s)`，基线 commit：`6e14bd2`。
> 范围：**只覆盖视觉链路**（`98caca4` 只改 `vision/`）；对话链路见 `latency-analysis.md` §2，不在本方案验收范围内。
>
> 第 1 节锚点、§2/§3 的命令均已在本机实跑核对（2026-09-23）；本版修正说明见 §8（其中 §1 的工况口径于 2026-09-28 修订：验收工况就是"TFT 运行中"，不追求空载）。

## 0. 测试目标与通过标准

| 维度 | 验证内容 | 通过标准 |
|---|---|---|
| 性能（热轮） | 三种模式识别耗时 | ally ≤ 5s、enemy ≤ 4s、augment ≤ 2s |
| 性能（冷启动） | 预热结束后首个请求耗时 | ≤ 6s（优化前 ~27s） |
| 正确性 | 识别结果与基线一致性 | 血量/棋子/星级/装备/海克斯逐项一致 |
| 稳定性 | 同模式连续 5 次耗时波动 | max/min ≤ 1.3 且无报错（绝对耗时按本次工况自建基准，见第 1 节） |

**前置条件（否则上表不成立）**：**A/B 两次跑必须处在同一工况**（见第 1 节）。本项目的视觉识别服务输入就是游戏截图，**真实运行环境里主机必然同时跑着 TFT**，所以绝对阈值（5/4/2s）是在 latency-analysis.md §1.5 的测量工况下给出的，**只在记录的工况里才成立**；工况更重时以"相对提升倍数 ≥8×"为主判据，并把本次实测的绝对耗时当作该工况下的新基准。

**不需要为测试清理机器**：直接以当前状态开测，两次测量之间保持机器状态不变即可（该开着的应用就开着，别中途新增负载）。

本机实测过"同一份代码热轮 18.8s / 12.9s / 8.3s"（参考值 3.7 / 2.7 / 1.3 的约 5 倍），可见这些数字对机器状态极其敏感 —— 这类偏差属于环境，不是代码回退。

参考数值（同机 1920x1080 示例图实测，见 latency-analysis.md §1.5；**该表是"良好负载条件"下的值，不写明工况就不能跨环境比**）：

| 模式 | 基线 | 优化后（热轮） | 提速 |
|---|---|---|---|
| ally 我方棋盘 | ~33 s | ~3.7 s | ~9× |
| enemy 敌方棋盘 | ~28 s | ~2.7 s | ~10× |
| augment 选海克斯 | ~17 s | ~1.3 s | ~13× |
| 服务首请求（冷启动） | ~27 s | ~4 s | ~7× |
| 进程启动（模型加载） | ~10 s | ~2.7 s | ~4× |

> 期望的形态：本次 A/B 里"基线列"无论落在哪个档位，"优化后列"都应当是它的 1/8 ~ 1/10，且比基线低一个数量级。这比绝对秒数更稳、更说明问题。

## 1. 工况设定（保证 A/B 可比，最关键）

性能数字对机器状态极其敏感 —— 同一份代码，本机热轮 3.7s 和 24s 都出现过。但**这里不能靠"把机器清空"来解决**：本项目的视觉识别服务就是用来识别游戏截图的，**正常使用时主机必然同时跑着 TFT**，那才是要验收的工况；为追求漂亮数字去测空载，等于测了个用户拿不到的场景。

所以本节的规则是「**锁定工况、如实记录**」，不是「追求负载更轻」：

1. **直接以当前机器状态开测，不要为了测试去清理负载**。该开着的（微信、浏览器标签、IDE、后台服务）就开着，一切照常。
2. **测试期间也不要新增负载**：别在中途打开新应用、发起下载/编译、装软件。**两次测量之间机器状态保持一致，比"更干净"重要得多**。
3. **主测量工况 = TFT 运行中**（当前状态即可）。基线 / 优化后两次测量都必须在**这个**状态下跑完。
4. **不要拿空载数字当验收依据**。想额外测一版空载，在报告里单列并注明，不与主工况混算。
5. **每次测量前后各记一次实测负载**，填进 §6。不需要去凑任何阈值，只要两次落在同一档就行。

```powershell
# 测前测后各跑一次: 记录实测值, 并确认 TFT 确实在跑
(Get-CimInstance Win32_Processor | Select-Object -ExpandProperty LoadPercentage)
[math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1MB, 2)
Get-Process | Where-Object Name -match 'League|TFT|WeGame' | Select-Object Name, Id
```

> 参考：本机常态就是内存占用 ~90%、空闲 ~1.5GB（TFT 跑着，总内存 15.8GB），这属于**本项目的正常工况，不是"环境异常"** —— 只要两次测量落在这个状态里，数据就是可比的。

- **保持电源状态不变**：笔记本别在两次测量之间拔/插电源，避免省电降频导致同一工况下数字漂移。
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

利用 git 在"基线 / 优化后"之间切换，同机同图各跑一遍 profile。**两次都要在 TFT 运行中做**（见第 1 节），否则比较的是两个不同工况。

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
git status --short vision/                      # 应列出 6 个 M (在第 1 列, 已暂存):
                                                #   API.md / hud/bench_items.py / hud/hud_ocr.py / infer.py
                                                #   / recognize.py / vision_api.py
                                                # 98caca4 本身只动前 4 个; API.md 是本方案 ⑤ 改的,
                                                # recognize.py 是用法字符串补 enemy 带出来的
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
>
> **第 ① 步的 M 在第 1 列（已暂存）属正常，但此时别 commit**：`git checkout <commit> -- <path>` 会**同时写索引**，也就是"把 `vision/` 回退到基线"这件事已经暂存好了——此时顺手 `git commit` 就会把基线版本提交进去。第 ② 步的 `git checkout HEAD -- vision/` 会把索引一并复位，恢复后 `git status` 应当为空（本版已在提交后的干净工作区实测整条流程：切基线 6 个 M → 恢复 0 残留）。

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
   - `ocr:health`：良好工况参考 基线 ~13.8s×3 → 优化后 ~0.3s/张（工况更重时改善幅度会被压缩，见下表，但"基线/优化后"的相对差应始终明显）；
   - `[计数] RapidOCR rec 次数`（整轮 3 图累计）：口径是"rec 实际执行次数"——基线的 `self.ocr(strip)` 一次调用里 det+rec 全跑，计为**检出的文本框数**（~11~15/图）；优化后改为 det-only 拿候选框 + 按优先级惰性 rec，计为 1~2/图；`text_rec([N])` 计 N。本机实测**基线 50 → 优化后 22**（链路级总数，含其他字段的贡献），血量那条路径的下降主要体现在 `ocr:health` 耗时上；
   - `cv:preprocess (基线:37格裁图落盘)`：基线 ~3.2s，**只在基线日志出现**，优化后该行消失即为预期（内存化）；
   - `onnx:identity` / `onnx:stars`：由每格/每棋子多次调用变为各 1 次批量调用（`x2` = ally+enemy 各一次；augment 不跑棋盘）；
   - `load:BenchMatcher`：基线 4.4~9.6s → 优化后 <0.2s（轻载时 <0.1s，缓存生效后重载下也只会被放大到零点几秒）；
   - 各阶段之和 ≈ 总耗时的差额 = 未单独归类的开销（图像解码、基线裁图读回等）。

> 若优化后 `load:BenchMatcher` 仍 >2s：先确认 `data/vision_dataset/s18-equipment-v4/reference/ref_emb_cache.npz` 是否被删/失效（参考图名单变化会触发一次性重建），否则这次对比会被"重建缓存"污染。

> **本机实跑样例（2026-09-23，内存占用 89%、空闲 1.7GB —— 本项目的常态工况，不是异常）** —— 当时日志形态如下，绝对值不代表良好工况，**不能当参考数据**，只用来确认"该出现/该消失的标签"：
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
> 也正因为同机两次跑同一版本能差 3 倍，第 1 节的工况锁定不能省。

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

- `warm=true` 之后发出的首请求应与**热轮同量级** —— 这条的本质是验证"预热生效"，优化前是 ~27s 的懒加载量级；良好工况下 ≤ 6s，工况更重时按"落在本次热轮的档位内"判断，**只要不是懒加载量级就算通过**。
- 后续请求无显著递增（见 §0 稳定性判据）。
- 正确性：`scratch\resp_ally.json` 中 `raw.health=91`、`raw.board` 7 个棋子、`raw.bench_items` 3 项。

> 注意：
> 1. **必须等 `warm=true` 再计时**。早先 `/api/health` 只返回 `{"ok":true}`，无法判断预热是否结束——预热期间发请求会与预热线程抢 CPU（实测启动 8s 后发请求得 36.8s，紧接着第二次 24.0s，都不代表稳态）。现在 health 返回 `{ok, warm, warmup_ms}`，请以 `warm` 为准。本机实测预热花了 `warmup_ms=44826`（44.8s）——工况重时预热本身就会超过 40s，所以"固定等 40s"并不保险，只能轮询。
> 2. 不要用 `-o NUL`：cmd/PowerShell 下是空设备，Git Bash 下会真的生成一个名为 `NUL` 的文件；统一写 `scratch\*.json`。
> 3. PowerShell 5.1 的 `Invoke-WebRequest` 不支持 `-Form`，multipart 上传请用 `curl.exe`（Windows 自带）。
> 4. 若设过环境变量 `VISION_API_KEY`，请求需带 `-H "X-API-Key: <key>"`，否则 401。

## 5. 方案 D：全栈联测（可选，贴近真实使用）

```powershell
.venv\Scripts\python.exe run.py    # 同时起后端 8000 + 视觉 8010
```

浏览器打开 `http://localhost:8000`，走一遍真实流程：

1. 粘贴/上传三张示例截图，分别触发三种识别模式；
2. 观察前端响应速度（预热完成后识别结果应与本次热轮同档，良好工况 ~3~4s 返回）；
3. 核对前端展示的血量/棋子/海克斯与第 1 节锚点一致；
4. 发起教练对话，确认识别结果正确注入会话。

## 6. 结果记录模板

> **已按 2026-09-28 本机实测填写**（执行：WorkBuddy）。产物在 `scratch/`：`baseline.log` / `optimized.log` / `base_rerun{1,2}.log` / `opt_rerun.log` / `cpu_*.log` / `ab_env.log` / `svc_{base,opt}.log` / `before_*.json` / `after_*.json`。

**工况（第 1 节必填栏）**：**TFT 未运行** —— 执行时 `tasklist` 无 League/TFT/WeGame 进程。经确认后按"就以当前状态测、如实标注"执行，因此**未满足 §1 规则 3 的主工况**，下表绝对秒数**不能**与 §0 的 5/4/2s 阈值直接对照（阈值只在记录的工况下成立）。
实测负载：CPU 占用 **9.6~19.2%**、内存占用 **87~94%**、空闲内存 **0.92~2.04GB**（总内存 15.82GB、20 逻辑核）；分辨率：样例图 1920×1080（离线识别，无游戏窗口）；A/B 全程电源状态未变。

> ⚠️ **本机出现 3~6 倍的性能档位漂移**，导致 §2 "先跑完基线、再跑完优化后"的字面流程会给出**自相矛盾**的结论（见 §6.2/§6.3）。下表基线/优化后两列取**同档配对样本**（方案 C，15:48~15:51 同一窗口），这是本次唯一可靠的对比口径。

| 指标 | 基线 | 优化后 | 提升 | 工况备注 |
|---|---|---|---|---|
| ally（热轮，5 次 max/min） | **4.93 s**（热轮 4 次 4.70~5.08，max/min **1.08**；含另 3 个同档样本共 7 次为 4.70~5.56，max/min **1.18**） | **3.56 s**（5 次 3.50~3.99，max/min **1.14**） | **~1.4×** | TFT 未运行 / CPU 9.6~19.2% / 空闲 0.9~2.0GB |
| enemy（热轮） | 3.81 s（3.69 / 3.93） | 2.52 s（2.46 / 2.57） | ~1.5× | 同上 |
| augment（热轮） | 1.77 s（1.76 / 1.77） | 1.07 s（1.05 / 1.09） | ~1.65× | 同上 |
| 服务首请求（warm=true 后） | 9.12 s（基线 health 无 `warm` 字段，该次**含懒加载**） | **3.99 s** | ~2.3× | warmup_ms=**30636**；服务就绪 1~2 s |
| 进程启动（模型加载） | 9.16 s（快档；另两档 10.13 / 11.13 s） | 2.60~3.20 s | ~3.5× | 懒加载合计 `load:*` |
| `[计数] RapidOCR rec 次数`（每图） | 50 / 3 图 ≈ **16.7** | 22 / 3 图 ≈ **7.3** | ~2.3× | 计数含全部 OCR 字段，非仅血量；两版均与负载档无关 |
| 正确性回归（3 图 diff） | — | **通过** | — | 3/3 逐字节一致，MD5 全等 |

**通过标准对照**（§0）：
- **性能（热轮）**：优化后 3.56 / 2.52 / 1.07 s，在**本工况**下达标；但基线 4.93 / 3.81 / 1.77 s **同样达标** —— 说明这组绝对阈值在本工况下**没有区分度**（§1 已声明阈值只在记录的工况下成立，跨工况只比相对倍数）。
- **稳定性**：两版 max/min ≤ 1.3 ✓（1.14 / 1.08~1.18）
- **正确性**：逐字节一致 ✓
- **冷启动**：优化后首请求 3.99 s 与热轮 3.56 s **同量级** ✓（预热生效；基线首请求 9.12 s 含懒加载）

### 6.1 为何提升是 ~1.4× 而不是 §0 的 9~13×

| 模式 | §0/§1.5 参考（基线 → 优化后） | 本次实测（基线 → 优化后） | 差异 |
|---|---|---|---|
| ally | ~33 s → ~3.7 s（~9×） | 4.93 s → 3.56 s（~1.4×） | 优化后**与参考吻合**（3.56 vs 3.7 s）；基线比参考快 **6.7×** |
| enemy | ~28 s → ~2.7 s（~10×） | 3.81 s → 2.52 s（~1.5×） | 基线比参考快 7.3× |
| augment | ~17 s → ~1.3 s（~13×） | 1.77 s → 1.07 s（~1.65×） | 基线比参考快 9.6× |

**原因**：文档 9~13× 建立在"基线最大瓶颈 `ocr:health` 每张 ~13.8 s"之上（占总耗时 42%~81%）。本次实测该路径在基线快档下只要 **~3.8 s/张**（`ocr:health` 11405 ms / 3 图，base_rerun2 热轮），基线总耗时随之从 ~33 s 压到 ~4.8 s；**优化后的绝对耗时基本不变**（3.56 s vs 参考 3.7 s）。基线的大头被机器状态"抹平"后，优化的相对收益自然从 ~9× 收缩到 ~1.4×。

> 换句话说：**这次优化在轻工况下的收益远小于文档口径，但仍真实存在**（三模式、首请求、模型加载、rec 次数全部朝同方向改善，且正确性零回归）。

### 6.2 顺序式执行（§2 字面流程）的实测结果与不可用原因

| 运行（时间） | ally | enemy | augment | 判读 |
|---|---|---|---|---|
| 基线 #1（15:27） | 5.28 s | 4.40 s | 2.62 s | 快档 |
| 优化后 #1（15:29） | 20.37 s | 14.08 s | 8.19 s | 慢档 |
| 优化后 #2（15:32） | 20.76 s | 14.27 s | 8.47 s | 慢档 |
| 基线 #2（15:35） | 28.73 s | 23.00 s | 13.11 s | 慢档 |
| 基线 #3（15:38） | 11.96 s | 4.62 s | 2.02 s | 冷轮慢 / 热轮快 |

- 字面结论会写成"**优化后比基线慢 3.9×**"——完全错误。
- 反证：**同一份基线代码**在 15:27 与 15:35 之间自身摆幅 **5.4×**（5.28 s → 28.73 s）；同一次运行内相同调用 `onnx:identity` 从 1.63 s 漂到 9.86 s。
- 所以这不是代码回退。§7"差 3 倍以上先怀疑工况不一致"这条在本机是**常态**，不是例外。

### 6.3 交替配对测量（本次实际采用）

改为 **A/B 紧邻交替**（`scratch/ab_alt.sh`，每对先基线后优化，`quick_bench.py` 记每次耗时）：

| 配对 | 基线 ally | 优化后 ally | 判读 |
|---|---|---|---|
| 第 1 对（15:41） | 26.75 / **5.56** | **3.35 / 3.47** | 基线首个样本仍在慢档 |
| 第 2 对（15:43） | **4.80 / 4.87** | 20.42 / 20.51 | 优化后落入慢档 |

**两对结论相反** —— 档位在**一对之内的两次运行之间**就会翻转（第 2 对：基线模型加载 3.24 s＝快档，20 s 后优化后加载 13.03 s＝慢档）。因此只有**跨对取同档样本**才可比：

- ally：基线 4.80~5.56 s vs 优化后 3.35~3.47 s → **~1.5×**
- enemy：基线 3.75~3.97 s vs 优化后 2.34~2.41 s → **~1.65×**
- augment：基线 1.54~1.77 s vs 优化后 0.81~0.96 s → **~1.9×**

与 §6 主表（~1.4×/~1.5×/~1.65×）一致，互为佐证。

> **流程结论**：在这台机器上做性能验收，**"先 A 后 B"必须改成"A/B 紧邻交替配对"**，否则任何一次单独测量都不可信。

### 6.4 档位漂移的证据与归因状态

监控脚本 `scratch/monitor.py`（2 s 采样：整机 CPU + 空闲内存 + 进程级 CPU 排行）在慢档下显示：

- 本进程 CPU 占用 ~15~18%（整机 20 逻辑核，约 3 核），整机 ~22~27%，**其余进程均在 1% 以下** —— **无竞争进程**；
- 但同样的活要烧掉约 **3 倍的 CPU 时间**（慢档 ally ≈ 181 CPU·s vs 快档 ≈ 60 CPU·s）—— **同占用率、同工作量，吞吐差 3 倍**；
- 快慢档对各阶段**打击比例不同**（`onnx:identity` 7.5×、`onnx:equipment` 18×、`ocr:health` 2.3×），**不是简单降频**。

**归因未闭合**：机器内部找不到成因（无进程竞争、CPU 占用率相同、空闲内存与快慢档不单调相关）。特征符合**宿主/虚拟化层争抢**——本机同时跑着 ToDesk、sandbox-center、TXIDEControlCenter 等远程/虚拟化组件（PowerShell/注册表查询被沙箱拦截，未能核实 CPU 型号与 CurrentClockSpeed）。

**建议**：① 长期做性能验收请换到负载可控的专用环境；② 若必须在本机验，把 **A/B 交替配对（§6.3）固化为流程**，并同时跑 `monitor.py` 留档；③ 绝对阈值只有配合工况栏才有效，跨次只比倍数。

## 7. 常见问题

| 现象 | 排查 |
|---|---|
| 耗时突然变 20s+ | 先核对工况（第 1 节）：两次测量时 TFT 是否都在跑、有没有多开别的东西。**同一个 TFT 工况下正常波动约 1~2 倍**；差 3 倍以上先怀疑工况不一致，再怀疑代码 |
| 基线跑 profile 报 `AttributeError: _star_locate` | 用的是旧脚本（不兼容基线）。换成当前 `scripts/profile_latency.py` |
| 做完 A/B 后 `vision/` 的改动不见了 | `git checkout HEAD -- vision/` 覆盖了未提交改动。跑之前先 `git stash`（或改用路线 2 的 worktree 副本），跑完 `git stash pop` |
| 切基线后 `git status` 的 M 在第 1 列（已暂存） | 正常：`git checkout <commit> -- <path>` 会同时写索引。**别在这个状态下 `git commit`**；第②步 `git checkout HEAD -- vision/` 会自动复位索引 |
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
| ③ | 第 1 节"CPU 空闲率应 <20%"与命令矛盾（`LoadPercentage` 是占用率） | 改为"CPU 占用率 <20%"，内存判据显式写成 `>4GB`（**该判据已在 ⑨ 被整体取代**） |
| ④ | 检查点的 `cv:preprocess`、`rec 次数` 脚本不产出；`load:BenchMatcher` 阈值两处不一致（<0.1s / <0.2s） | 脚本补 preprocess 计时与 rec 计数两个埋点；阈值统一为 <0.2s（轻载 <0.1s）；检查点只保留脚本真实产出的标签 |
| ⑤ | `/api/health` 无预热状态，"等 40s"不可验证，冷启动判据不可复现 | `vision_api.py` 的 health 增加 `warm` / `warmup_ms`；文档改为轮询 `warm=true` 后再计时（`vision/API.md` 已同步） |
| ⑥ | 产物写在仓库根目录，`-o NUL` 在 Git Bash 下会生成垃圾文件 | 统一输出到 `scratch\`（已加入 `.gitignore`）；稳定性判据量化为 max/min ≤ 1.3 |
| ⑦ | 切版本做 A/B 会静默丢弃 `vision/` 下未提交的改动（`git checkout HEAD -- vision/` 是覆盖而非合并） | §2 明确"`git stash` 是必做项"，补 §7 排查行；另提供不切工作区的 worktree 路线 |
| ⑧ | §2 第①步"应列出 4 个 M"的预期值不准，且未说明 `git checkout <commit> -- <path>` 会把改动写进索引 | 实测改为 **6 个 M**（+ `API.md`、`recognize.py`），并补"标记落在**已暂存**列、此时别 commit"的说明；`recognize.py` 用法字符串同步补 `enemy` |
| ⑨ | §0/§1 把"CPU 空闲、空闲内存 >4GB"当门禁 —— **前提就错了**：本项目识别的是游戏截图，真实运行时主机必然同时跑着 TFT，追求空载等于在测一个用户拿不到的场景 | §1 重写为「**锁定工况、如实记录**」：**直接以当前状态测、不清理负载**，主工况 = TFT 运行中，测试期间也不新增负载（两次之间机器状态不变）；绝对阈值只在记录的工况下成立，跨工况以"优化后 ≈ 基线的 1/8~1/10"为主判据；§0/§5/§6/§7 同步（§6 增必填工况栏） |

> ⑦ 是这次写方案时亲历的：先改了 `vision/vision_api.py`，随后为验证方案 A 切基线再恢复，改动被覆盖丢失，不得不重做——按本方案执行时请先 stash/提交。
