#!/usr/bin/env python3
"""链路延迟剖析: 实测「截图识别」和「教练对话」两条链路各阶段的耗时。

用法 (仓库根目录, 用 .venv 的 python):
  python scripts/profile_latency.py vision   # 视觉链路 (本地模型, 无需网络)
  python scripts/profile_latency.py chat --base http://localhost:8123  # 对话链路

chat 模式需先启动后端: .venv/Scripts/python -m uvicorn backend.app:app --port 8123

不改生产代码: 运行时用计时代理包装 onnx session / OCR 函数。

A/B 对比: 本脚本对**基线(6e14bd2)与优化后(perf/latency)两版 vision/ 都可用**
(`git checkout <commit> -- vision/` 切版本后直接跑, 无需改脚本):
- 星级血条定位: 优化后是 `board._star_locate`, 基线是 `board._star_stage` 三元组 → 自行分支
- 预处理 `cv:preprocess (基线:37格裁图落盘)` 只在基线出现(优化后已内存化, 该行消失即为预期)
- `[计数] RapidOCR rec 次数` 反映"减 rec 次数"的优化效果(基线 11~15/图, 优化后 1~2/图)
注意: 各阶段耗时之和 ≈ 总耗时的差额 = 未单独归类的开销(如基线的裁图读回、图像解码)。
"""
import argparse
import functools
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "vision"))
sys.path.insert(0, str(ROOT / "vision" / "hud"))
sys.path.insert(0, str(ROOT))

EXAMPLES = [
    ("ally", ROOT / "examples" / "my_board.png"),
    ("enemy", ROOT / "examples" / "enemy_board.png"),
    ("augment", ROOT / "examples" / "augments.png"),
]


class Timer:
    def __init__(self):
        self.recs = []
        self.counts = {}   # 调用计数(如 RapidOCR 的 rec 次数), 与耗时分开记

    def add(self, label, dt):
        self.recs.append((label, dt))

    def bump(self, label, n=1):
        self.counts[label] = self.counts.get(label, 0) + n

    def reset(self):
        self.recs.clear()
        self.counts.clear()

    def summary(self, title):
        agg, order = {}, []
        for label, dt in self.recs:
            if label not in agg:
                agg[label] = [0.0, 0]
                order.append(label)
            agg[label][0] += dt
            agg[label][1] += 1
        print(f"\n--- {title} ---")
        total = 0.0
        for label in order:
            t, n = agg[label]
            total += t
            print(f"  {label:<30} {t * 1000:8.0f} ms  x{n}")
        print(f"  {'(以上合计)':<30} {total * 1000:8.0f} ms")
        for label, n in self.counts.items():
            print(f"  [计数] {label:<23} {n:8d}")


class TimingSession:
    """onnx InferenceSession 计时代理"""
    def __init__(self, sess, name, timer):
        self._s, self._name, self._t = sess, name, timer

    def __getattr__(self, attr):
        return getattr(self._s, attr)

    def run(self, *a, **k):
        t = time.perf_counter()
        r = self._s.run(*a, **k)
        self._t.add(f"onnx:{self._name}", time.perf_counter() - t)
        return r


class OcrCounter:
    """RapidOCR 引擎代理: 统计 rec 实际执行次数(不改变行为)。
    rec 是 OCR 侧的主要成本(每次 ~0.2~0.9s 固定开销), "减次数"是血量的优化点:
    - 基线 `self.ocr(strip)` 一次调用里 det+rec 全跑 → 次数 = 检出的文本框数(11~15)
    - 优化后 det-only 取候选框 + 按优先级惰性 `_rec` → 次数 = 1~2
    耗时日志看不到次数, 故单独计数: rec-only 调用记 1 次, det+rec 记 box 数,
    `text_rec([N])` 记 N 次, det-only(`use_rec=False`)不计。"""
    def __init__(self, engine, timer):
        self._e, self._t = engine, timer

    def __getattr__(self, attr):
        return getattr(self._e, attr)

    def __call__(self, *a, **k):
        r = self._e(*a, **k)
        if k.get("use_rec", True):        # 未显式关掉 rec 时, rec 执行次数 = 检出框数
            boxes = r[0] if isinstance(r, (tuple, list)) and r else None
            self._t.bump("RapidOCR rec 次数", max(len(boxes), 1) if isinstance(boxes, list) else 1)
        return r

    def text_rec(self, imgs, *a, **k):
        self._t.bump("RapidOCR rec 次数", len(imgs))
        return self._e.text_rec(imgs, *a, **k)


def _timed_call(timer, label, fn):
    """把 fn 包成"计时后原样调用"的可调用对象(用于只出现在某一版代码里的函数)。"""
    @functools.wraps(fn)
    def wrapper(*a, **k):
        t = time.perf_counter()
        r = fn(*a, **k)
        timer.add(label, time.perf_counter() - t)
        return r
    return wrapper


def profile_vision():
    from recognize import Recognizer

    rec = Recognizer()
    tm = Timer()

    # 1) 懒加载耗时 (服务进程生命周期内只付一次)
    t = time.perf_counter()
    hud = rec.hud
    tm.add("load:HudOCR (RapidOCR 初始化)", time.perf_counter() - t)
    t = time.perf_counter()
    board = rec.board
    tm.add("load:BoardRecognizer (identity+stars+equipment)", time.perf_counter() - t)
    t = time.perf_counter()
    bench = rec.bench
    tm.add("load:BenchMatcher (onnx+137参考图embedding)", time.perf_counter() - t)
    tm.summary("视觉模型懒加载 (每进程一次)")

    # 2) 包装计时
    #    ⚠ 两版 vision/ 都支持: 优化后基线/优化后属性名不同(见各处 hasattr 分支),
    #      切 commit 做 A/B 时本脚本无需改动、也不会因缺属性而崩。
    orig_field = hud._parse_field
    def timed_field(img, name, spec, _fn=orig_field):
        t = time.perf_counter()
        r = _fn(img, name, spec)
        tm.add(f"ocr:{name}", time.perf_counter() - t)
        return r
    hud._parse_field = timed_field
    hud.ocr = OcrCounter(hud.ocr, tm)

    board.id_s = TimingSession(board.id_s, "identity (37格批量)", tm)
    board.st_s = TimingSession(board.st_s, "stars (批量1次)", tm)
    board.eq_s = TimingSession(board.eq_s, "equipment (批量1次)", tm)

    def timed_star_locate(*a, _fn=None, **k):
        t = time.perf_counter()
        r = _fn(*a, **k)
        tm.add("cv:血条定位 detect_health_bars(星)", time.perf_counter() - t)
        return r
    if hasattr(board, "_star_locate"):          # 优化后: 单函数属性
        board._star_locate = functools.partial(timed_star_locate, _fn=board._star_locate)
    elif hasattr(board, "_star_stage"):         # 基线: (get_slots, locate, save_crops) 三元组
        _gs, _locate, _save = board._star_stage
        board._star_stage = (_gs, functools.partial(timed_star_locate, _fn=_locate), _save)

    if hasattr(board, "_pre"):                  # 基线: 37 格裁图落盘(protocol preprocess); 优化后已内存化
        board._pre = _timed_call(tm, "cv:preprocess (基线:37格裁图落盘)", board._pre)

    orig_eq_locate = board._eqv5.locate
    def timed_eq_locate(*a, _fn=orig_eq_locate, **k):
        t = time.perf_counter()
        r = _fn(*a, **k)
        tm.add("cv:血条定位 detect_health_bars(装)", time.perf_counter() - t)
        return r
    board._eqv5.locate = timed_eq_locate
    bench.model = TimingSession(bench.model, "bench_feat (参考+槽位批量)", tm)

    # 3) 两轮: 冷 (onnx 首次推理含图优化) / 热
    for rnd in (1, 2):
        tm.reset()
        for mode, img in EXAMPLES:
            t = time.perf_counter()
            out = rec.recognize(str(img), mode)
            dt = time.perf_counter() - t
            tm.add(f"== recognize({mode})", dt)
            print(f"第{rnd}轮 {mode:<8} 棋子数={len(out.get('board', [])):<3} 总耗时 {dt * 1000:6.0f} ms")
        tm.summary(f"视觉链路 第{rnd}轮 ({'冷' if rnd == 1 else '热'})")


# ---------------- 对话链路 ----------------

def sse_events(resp):
    """逐事件产出 (data_dict, 到达时刻)"""
    buf = b""
    read = getattr(resp, "read1", None) or resp.read  # read1: 有数据即返回, 不攒满
    while True:
        chunk = read(512)
        if not chunk:
            break
        buf += chunk
        while b"\n\n" in buf:
            raw, buf = buf.split(b"\n\n", 1)
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("data: "):
                yield json.loads(line[6:]), time.perf_counter()


def post_json(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    return urllib.request.urlopen(req, timeout=600)


SAMPLE_STATE = {
    "stage": "3-2", "level": 6, "gold": 34, "hp": 72, "streak": "W2",
    "augments": ["潘朵拉的装备"], "pending_augments": [],
    "emblems": [],
    "items": {"component": ["反曲之弓", "女神之泪"]},
    "board": [
        {"unit": "艾希", "star": 2, "items": ["鬼索的狂暴之刃"], "pos": [3, 2]},
        {"unit": "瑟庄妮", "star": 2, "items": [], "pos": [0, 3]},
        {"unit": "莉莉娅", "star": 1, "items": [], "pos": [1, 2]},
    ],
    "bench": [{"unit": "索拉卡", "star": 1}],
    "shop": ["卡尔玛", "卡蜜尔", "纳尔", "阿兹尔", "克格莫"],
    "note": "测试链路延迟",
}


def profile_chat(base):
    # 0) 提示词规模 (静态分析, 本地 import)
    from backend.knowledge import Knowledge, render_state
    import yaml
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    kn = Knowledge(cfg.get("set", 18))
    sp = kn.system_prompt()
    detail = kn.details_block(SAMPLE_STATE, set())
    print("--- 提示词规模 (静态) ---")
    print(f"  system prompt (常驻层: 通用策略+赛季速查+meta): {len(sp):,} 字符")
    print(f"  本轮按需注入 details_block: {len(detail):,} 字符")
    print(f"  render_state(当前状态): {len(render_state(SAMPLE_STATE)):,} 字符")
    print(f"  中文约 1.5-2 字符/token -> system 估算 {len(sp)//2:,} ~ {int(len(sp)/1.5):,} token")

    # 1) 会话与静态数据
    t = time.perf_counter()
    sid = json.loads(post_json(f"{base}/api/session", {}).read())["session_id"]
    t_sess = time.perf_counter() - t
    t = time.perf_counter()
    data = json.loads(urllib.request.urlopen(f"{base}/api/data", timeout=60).read())
    t_data = time.perf_counter() - t
    print("\n--- 准备阶段 ---")
    print(f"  POST /api/session {t_sess * 1000:.0f} ms | GET /api/data {t_data * 1000:.0f} ms "
          f"(champions={len(data['champions'])}, augments={len(data['augments'])})")

    # 2) 同一会话两轮: 第1轮含按需知识注入(缓存冷), 第2轮看复用
    for rnd in (1, 2):
        payload = {"session_id": sid, "state": SAMPLE_STATE, "deep": False}
        if rnd == 1:
            payload["action"] = "recommend"
        else:
            payload["message"] = "如果对手是6主宰, 站位要怎么调?"
        t0 = time.perf_counter()
        resp = post_json(f"{base}/api/chat", payload)
        first_delta, tools, n_delta, n_char, t_done = None, [], 0, 0, None
        for ev, ts in sse_events(resp):
            if ev["type"] == "delta":
                if first_delta is None:
                    first_delta = ts
                n_delta += 1
                n_char += len(ev["text"])
            elif ev["type"] == "tool":
                tools.append((ts, ev["text"]))
            elif ev["type"] == "done":
                t_done = ts
            elif ev["type"] == "error":
                print("  !! error:", ev["text"])
                return
        print(f"\n--- /api/chat 第{rnd}轮 ({'action=recommend' if rnd == 1 else '自由追问'}) ---")
        if first_delta:
            print(f"  首 token 延迟 TTFT: {(first_delta - t0) * 1000:.0f} ms")
        prev = first_delta or t0
        for ts, txt in tools:
            print(f"  工具调用 @{(ts - t0) * 1000:7.0f} ms (距上事件 {(ts - prev) * 1000:6.0f} ms): {txt}")
            prev = ts
        anchor = tools[-1][0] if tools else (first_delta or t0)
        print(f"  生成文本 {n_char} 字 / {n_delta} 个 delta, 末段生成 {(t_done - anchor) * 1000:.0f} ms")
        print(f"  端到端总耗时: {(t_done - t0) * 1000:.0f} ms")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["vision", "chat"])
    ap.add_argument("--base", default="http://localhost:8123")
    args = ap.parse_args()
    if args.what == "vision":
        profile_vision()
    else:
        profile_chat(args.base)
