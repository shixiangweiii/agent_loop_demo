"""Model: 官方 openai SDK 的异步薄封装，对接 OpenAI 兼容端点。

只封装 AsyncOpenAI.chat.completions.create —— 不自建 HTTP/provider 适配。
百炼(Qwen)/DeepSeek/OpenAI 等均为 OpenAI 兼容端点，一套接口覆盖。
Model 是可替换的（agent 只依赖 .acomplete），M1+ 若需非兼容 provider 再换实现。
"""
from __future__ import annotations

import os
from typing import Any

from openai import AsyncOpenAI


class ConfigError(RuntimeError):
    """配置缺失（MU_MODEL / MU_API_KEY 未设）。"""


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
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> Any:
        """返回 ChatCompletionMessage（含 .content 与 .tool_calls）。"""
        resp = await self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
        )
        return resp.choices[0].message
