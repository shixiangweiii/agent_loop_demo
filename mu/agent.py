"""Agent: 极薄的 async while loop（无 max_steps）。

- 线性 append-only 消息历史 = 每轮原样传给模型的 prompt（mini-swe 洞察）。
- 当 assistant 消息不含 tool_calls 即终止（Pi 哲学）。
- `_emit` 是单点观测 seam：M0 打印到 stdout，M1 在此替换为事件流。
"""
from __future__ import annotations

import json
from typing import Any, Callable

from .model import Model
from .prompts import SYSTEM_PROMPT
from .tools import ToolRegistry

EmitFn = Callable[[str, str], None]


class Agent:
    def __init__(
        self,
        model: Any | None = None,
        tools: ToolRegistry | None = None,
        emit: EmitFn | None = None,
    ) -> None:
        self.model = model if model is not None else Model()
        self.tools = tools or ToolRegistry()
        self._emit: EmitFn = emit if emit is not None else _default_emit
        self.messages: list[dict[str, Any]] = []

    async def run(self, task: str) -> str:
        self.messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": task},
        ]
        self._emit("user", task)

        while True:  # 无 max_steps：跑到模型不再调用工具为止（Pi 哲学）
            message = await self.model.acomplete(self.messages, self.tools.schemas())
            assistant_msg = _message_to_dict(message)
            self.messages.append(assistant_msg)

            if assistant_msg.get("content"):
                self._emit("assistant", assistant_msg["content"])

            tool_calls = assistant_msg.get("tool_calls") or []
            if not tool_calls:
                return assistant_msg.get("content") or ""

            for tc in tool_calls:  # M0 顺序执行（并行留 M1）
                name = tc["function"]["name"]
                raw_args = tc["function"].get("arguments") or "{}"
                try:
                    args = json.loads(raw_args)
                except json.JSONDecodeError:
                    result = f"Error: tool arguments were not valid JSON: {raw_args!r}"
                else:
                    self._emit("tool_call", f"{name}({_short(raw_args)})")
                    result = await self.tools.execute(name, args)
                self._emit("tool_result", result)
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result,
                    }
                )


def _message_to_dict(message: Any) -> dict[str, Any]:
    """把 openai ChatCompletionMessage（或测试用 fake）转成线性历史里的 dict。"""
    out: dict[str, Any] = {"role": "assistant", "content": message.content}
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        out["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in tool_calls
        ]
    return out


def _short(s: str, n: int = 200) -> str:
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[:n] + "…"


def _default_emit(kind: str, text: str) -> None:
    labels = {
        "user": "👤 user",
        "assistant": "🤖 assistant",
        "tool_call": "🔧 tool",
        "tool_result": "📤 result",
    }
    label = labels.get(kind, kind)
    body = _short(text) if kind == "tool_call" else text
    print(f"\n=== {label} ===\n{body}", flush=True)
