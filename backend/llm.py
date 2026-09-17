"""LLM 客户端: 支持 OpenAI 兼容供应商 (DeepSeek) 和 Anthropic 官方 SDK (Claude)。

统一接口: stream_chat(messages, handlers, deep) 产出 ('delta'|'tool'|'done', payload) 事件。
messages 用 OpenAI 风格 ({role, content}), Anthropic 路径内部转换。
"""
import json
import os

TOOL_DEFS = [
    {
        "name": "lookup_details",
        "description": "查询本赛季棋子/羁绊/装备/海克斯的详细数据(数值、技能、合成、效果)。支持中文名和常见简称(如 剑圣/女警/金身), 可一次查多个。",
        "parameters": {
            "type": "object",
            "properties": {
                "names": {"type": "array", "items": {"type": "string"}, "description": "要查询的名称列表"}
            },
            "required": ["names"],
        },
    },
    {
        "name": "verify_comp",
        "description": "核验一套阵容的羁绊激活情况: 精确计算每个羁绊的数量、激活到哪一档、是否溢出或未激活。给出任何终局阵容推荐前必须先用它核验, 有浪费就调整阵容再核验。纹章(含棋子携带的)一并传入。",
        "parameters": {
            "type": "object",
            "properties": {
                "units": {"type": "array", "items": {"type": "string"}, "description": "阵容棋子名列表(官方中文名; 拉克丝写具体形态如 '拉克丝 (魔女)')"},
                "emblems": {"type": "array", "items": {"type": "string"}, "description": "阵容携带的转职纹章列表, 没有则省略"}
            },
            "required": ["units"],
        },
    },
]

TOOL_LABELS = {"lookup_details": "查询", "verify_comp": "核验阵容"}


def _tool_brief(name: str, args: dict) -> str:
    brief = ", ".join(str(x) for v in args.values() if isinstance(v, list) for x in v[:8])
    return f"{TOOL_LABELS.get(name, name)}: {brief}"[:120]


def _resolve_key(pcfg: dict, api_key: str | None) -> str:
    key = api_key or os.environ.get(pcfg.get("api_key_env", ""), "")
    if not key:
        raise RuntimeError(
            f"没有可用的 API key: 服务端未配置 {pcfg.get('api_key_env')} (.env 或环境变量), "
            "且请求未携带用户 key")
    return key


class OpenAICompatClient:
    """DeepSeek 等 OpenAI 兼容供应商"""

    def __init__(self, pcfg: dict, api_key: str | None = None,
                 model: str | None = None):
        from openai import OpenAI
        self.client = OpenAI(api_key=_resolve_key(pcfg, api_key), base_url=pcfg["base_url"])
        self.model = model or pcfg["model"]
        self.model_deep = pcfg.get("model_deep", self.model)

    def stream_chat(self, messages: list, handlers: dict, deep: bool = False, max_tool_rounds: int = 4):
        model = self.model_deep if deep else self.model
        use_tools = not (deep and self.model_deep != self.model)  # reasoner 类深度模型不用工具
        tools = [{"type": "function", "function": t} for t in TOOL_DEFS]
        full_text = []
        msgs = list(messages)

        for _ in range(max_tool_rounds + 1):
            kwargs = dict(model=model, messages=msgs, stream=True, temperature=0.7)
            if use_tools:
                kwargs["tools"] = tools
            stream = self.client.chat.completions.create(**kwargs)

            tool_calls: dict[int, dict] = {}
            finish = None
            for chunk in stream:
                if not chunk.choices:
                    continue
                ch = chunk.choices[0]
                delta = ch.delta
                if delta and delta.content:
                    full_text.append(delta.content)
                    yield ("delta", delta.content)
                if delta and delta.tool_calls:
                    for tc in delta.tool_calls:
                        acc = tool_calls.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                        if tc.id:
                            acc["id"] = tc.id
                        if tc.function and tc.function.name:
                            acc["name"] = tc.function.name
                        if tc.function and tc.function.arguments:
                            acc["args"] += tc.function.arguments
                if ch.finish_reason:
                    finish = ch.finish_reason

            if finish != "tool_calls" or not tool_calls:
                break

            msgs.append({
                "role": "assistant",
                "content": "".join(full_text) or None,
                "tool_calls": [
                    {"id": a["id"], "type": "function",
                     "function": {"name": a["name"], "arguments": a["args"]}}
                    for a in tool_calls.values()
                ],
            })
            for a in tool_calls.values():
                try:
                    args = json.loads(a["args"]) if a["args"] else {}
                except json.JSONDecodeError:
                    args = {}
                fn = handlers.get(a["name"])
                result = fn(args) if fn else f"未知工具 {a['name']}"
                yield ("tool", _tool_brief(a["name"], args))
                msgs.append({"role": "tool", "tool_call_id": a["id"], "content": result})

        yield ("done", "".join(full_text))


class AnthropicClient:
    """Claude 官方 SDK。system 消息转 system 参数, 工具走 Anthropic 格式的手动循环。"""

    def __init__(self, pcfg: dict, api_key: str | None = None,
                 model: str | None = None):
        import anthropic
        self.client = anthropic.Anthropic(api_key=_resolve_key(pcfg, api_key))
        self.model = model or pcfg["model"]
        self.model_deep = pcfg.get("model_deep", self.model)

    def stream_chat(self, messages: list, handlers: dict, deep: bool = False, max_tool_rounds: int = 4):
        model = self.model_deep if deep else self.model
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        msgs = [{"role": m["role"], "content": m["content"]}
                for m in messages if m["role"] in ("user", "assistant")]
        tools = [{"name": t["name"], "description": t["description"],
                  "input_schema": t["parameters"]} for t in TOOL_DEFS]
        # 深度思考: 用 adaptive thinking + 更高 effort (Claude 5 系工具照常可用)
        extra = {}
        if deep:
            extra["thinking"] = {"type": "adaptive"}
            extra["output_config"] = {"effort": "high"}
        full_text = []

        for _ in range(max_tool_rounds + 1):
            with self.client.messages.stream(
                model=model, max_tokens=8000, system=system,
                messages=msgs, tools=tools, **extra,
            ) as stream:
                for text in stream.text_stream:
                    full_text.append(text)
                    yield ("delta", text)
                response = stream.get_final_message()

            if response.stop_reason != "tool_use":
                break

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            msgs.append({"role": "assistant", "content": response.content})
            results = []
            for tu in tool_uses:
                args = tu.input if isinstance(tu.input, dict) else {}
                fn = handlers.get(tu.name)
                result = fn(args) if fn else f"未知工具 {tu.name}"
                yield ("tool", _tool_brief(tu.name, args))
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": result})
            msgs.append({"role": "user", "content": results})

        yield ("done", "".join(full_text))


def make_client(cfg: dict, provider: str | None = None, api_key: str | None = None,
                model: str | None = None):
    """按配置构造客户端。provider/model 为 None 时用 .env 或 config 默认。"""
    providers = cfg["llm"]["providers"]
    pname = provider or os.environ.get("LLM_PROVIDER") or cfg["llm"]["provider"]
    if pname not in providers:
        raise RuntimeError(f"未知供应商 {pname}, 可用: {', '.join(providers)}")
    pcfg = dict(providers[pname])
    # .env 覆盖默认模型: DEEPSEEK_MODEL / CLAUDE_MODEL
    env_model = os.environ.get(f"{pname.upper()}_MODEL")
    if env_model:
        pcfg["model"] = env_model
    if model:
        if model not in (pcfg.get("models") or [model]):
            raise RuntimeError(f"模型 {model} 不在 {pname} 的可选列表中")
        pcfg["model"] = model
    if pcfg.get("kind") == "anthropic":
        return AnthropicClient(pcfg, api_key=api_key, model=pcfg["model"])
    return OpenAICompatClient(pcfg, api_key=api_key, model=pcfg["model"])
