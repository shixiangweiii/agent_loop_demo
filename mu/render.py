"""StdoutRenderer：事件流的 stdout 订阅者，替代 M0 的 `_default_emit`。

保持 M0 的纯文本观感；额外支持流式（AssistantTextDelta 实时打印）。
"""
from __future__ import annotations

import sys
from typing import TextIO

from .events import (
    AssistantText,
    AssistantTextDelta,
    ErrorEvent,
    Event,
    RunAborted,
    RunStarted,
    ToolCallFinished,
    ToolCallStarted,
)


def _short(s: str, n: int = 200) -> str:
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[:n] + "…"


class StdoutRenderer:
    def __init__(self, out: TextIO | None = None) -> None:
        self._out = out if out is not None else sys.stdout
        self._in_delta = False

    def __call__(self, event: Event) -> None:
        # 流式增量：在收到任何非增量事件前，保持同一行
        if isinstance(event, AssistantTextDelta):
            if not self._in_delta:
                self._out.write("\n=== 🤖 assistant ===\n")
                self._in_delta = True
            self._out.write(event.delta)
            self._out.flush()
            return
        if self._in_delta:
            self._out.write("\n")
            self._in_delta = False

        if isinstance(event, RunStarted):
            self._block("👤 user", event.task)
            self._line(f"(session: {event.session_id})")
        elif isinstance(event, AssistantText):
            self._block("🤖 assistant", event.text)
        elif isinstance(event, ToolCallStarted):
            self._block("🔧 tool", f"{event.name}({_short(event.args_preview)})")
        elif isinstance(event, ToolCallFinished):
            self._block("📤 result", event.result)
        elif isinstance(event, RunAborted):
            self._line(f"[aborted: {event.reason}]")
        elif isinstance(event, ErrorEvent):
            self._line(f"[error: {event.message}]")

    def _block(self, label: str, body: str) -> None:
        self._out.write(f"\n=== {label} ===\n{body}\n")
        self._out.flush()

    def _line(self, text: str) -> None:
        self._out.write(text + "\n")
        self._out.flush()
