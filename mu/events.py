"""事件流：结构化事件 + 同步订阅分发。

M1 用它替代 M0 的单点 `_emit(kind,text)` 打印 seam。多个订阅者（stdout 渲染、
归因统计、未来的 TUI）各自消费同一串事件。订阅者只做轻量工作（打印/累加），
故 emit 是同步分发，不引入 pub/sub 框架。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class Event:
    """事件基类。"""


@dataclass
class RunStarted(Event):
    task: str
    session_id: str


@dataclass
class TurnStarted(Event):
    turn: int


@dataclass
class ModelCallStarted(Event):
    turn: int


@dataclass
class ModelCallFinished(Event):
    turn: int
    latency_s: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass
class AssistantText(Event):
    """非流式：一次性给出 assistant 文本。"""
    text: str


@dataclass
class AssistantTextDelta(Event):
    """流式：assistant 文本增量。"""
    delta: str


@dataclass
class ToolCallStarted(Event):
    call_id: str
    name: str
    args_preview: str


@dataclass
class ToolCallFinished(Event):
    call_id: str
    name: str
    result: str
    latency_s: float
    terminate: bool = False


@dataclass
class TurnFinished(Event):
    turn: int


@dataclass
class RunFinished(Event):
    final_text: str


@dataclass
class RunAborted(Event):
    reason: str


@dataclass
class ErrorEvent(Event):
    message: str


# ---- 扩展相关事件（M3：让扩展不成为黑盒）----

@dataclass
class ExtensionLoaded(Event):
    name: str
    version: str
    tools: list[str]


@dataclass
class ExtensionUnloaded(Event):
    name: str


@dataclass
class ExtensionLog(Event):
    name: str
    level: str
    message: str


@dataclass
class ExtensionError(Event):
    name: str
    message: str


Subscriber = Callable[[Event], None]


class EventEmitter:
    """最简同步事件总线：subscribe 注册，emit 顺序分发。"""

    def __init__(self) -> None:
        self._subs: list[Subscriber] = []

    def subscribe(self, fn: Subscriber) -> None:
        self._subs.append(fn)

    def emit(self, event: Event) -> None:
        for fn in self._subs:
            fn(event)
