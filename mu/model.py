"""Model: 官方 openai SDK 的异步薄封装，对接 OpenAI 兼容端点。

M1 变化：
- 返回 ModelResult（message + usage + latency），供归因底座使用。
- 支持可选流式（stream=True）：累积文本与 tool_call 增量，逐块回调 on_delta。
  流式累积逻辑抽成 `consume_stream`，便于离线测试（喂 fake 异步流）。
仍只封装 AsyncOpenAI.chat.completions.create —— 不自建 HTTP/provider 适配。
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable

from openai import AsyncOpenAI


class ConfigError(RuntimeError):
    """配置缺失（MU_MODEL / MU_API_KEY 未设）。"""


@dataclass
class ModelResult:
    message: Any  # 含 .content 与 .tool_calls（openai message 或等价对象）
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    latency_s: float = 0.0


# ---- 流式累积用的轻量消息对象（与 openai message 形状一致，供 _message_to_dict 消费）----

class _StreamFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _StreamToolCall:
    def __init__(self, id: str, name: str, arguments: str) -> None:
        self.id = id
        self.function = _StreamFunction(name, arguments)


class _StreamMessage:
    def __init__(self, content: str | None, tool_calls: list | None) -> None:
        self.content = content
        self.tool_calls = tool_calls


async def consume_stream(
    chunks: AsyncIterator[Any], on_delta: Callable[[str], None] | None = None
) -> tuple[_StreamMessage, Any]:
    """把 OpenAI 流式 chunks 累积成完整 message，并取出末块 usage。

    chunk.choices[0].delta 可能含 content 增量与 tool_calls 增量（按 index 累积）。
    """
    content_parts: list[str] = []
    tool_slots: dict[int, dict[str, str]] = {}
    usage = None
    async for chunk in chunks:
        usage = getattr(chunk, "usage", None) or usage
        choices = getattr(chunk, "choices", None)
        if not choices:
            continue
        delta = choices[0].delta
        text = getattr(delta, "content", None)
        if text:
            content_parts.append(text)
            if on_delta is not None:
                on_delta(text)
        for tcd in (getattr(delta, "tool_calls", None) or []):
            slot = tool_slots.setdefault(tcd.index, {"id": "", "name": "", "args": ""})
            if getattr(tcd, "id", None):
                slot["id"] = tcd.id
            fn = getattr(tcd, "function", None)
            if fn is not None:
                if getattr(fn, "name", None):
                    slot["name"] = fn.name
                if getattr(fn, "arguments", None):
                    slot["args"] += fn.arguments
    tool_calls = [
        _StreamToolCall(s["id"], s["name"], s["args"])
        for _, s in sorted(tool_slots.items())
    ] or None
    message = _StreamMessage("".join(content_parts) or None, tool_calls)
    return message, usage


class Model:
    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.model = model or os.environ.get("MU_MODEL", "")
        base_url = base_url or os.environ.get("MU_BASE_URL") or None
        api_key = (
            api_key
            or os.environ.get("MU_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or ""
        )
        if not self.model:
            raise ConfigError("MU_MODEL is not set. See .env.example.")
        if not api_key:
            raise ConfigError("MU_API_KEY (or OPENAI_API_KEY) is not set. See .env.example.")
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key)

    async def acomplete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        stream: bool = False,
        on_delta: Callable[[str], None] | None = None,
    ) -> ModelResult:
        t0 = time.perf_counter()
        if stream:
            raw = await self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                stream=True,
                stream_options={"include_usage": True},
            )
            message, usage = await consume_stream(raw, on_delta)
        else:
            resp = await self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
            )
            message = resp.choices[0].message
            usage = resp.usage
        return ModelResult(
            message=message,
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
            total_tokens=getattr(usage, "total_tokens", None),
            latency_s=time.perf_counter() - t0,
        )
