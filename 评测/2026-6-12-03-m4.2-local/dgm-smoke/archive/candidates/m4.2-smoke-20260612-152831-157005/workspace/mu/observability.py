"""延迟-成本归因底座（🟢 v1 创新，落在 M1）。

作为事件流的订阅者，按任务累计：轮数 / LLM wall-clock / 工具 wall-clock /
token（prompt/completion/total）/ 每工具计数与耗时。RunFinished/RunAborted 时出报告。

成本（$）为 best-effort：仅当显式传入价格表时估算，默认只报 token，不用于精确计费
（与 roadmap M1 Provider 验收一致）。
"""
from __future__ import annotations

import sys
import time
from typing import TextIO

from .events import (
    Event,
    ModelCallFinished,
    RunAborted,
    RunFinished,
    RunStarted,
    ToolCallFinished,
    TurnStarted,
)


class AttributionCollector:
    def __init__(self, out: TextIO | None = None, price_per_1k: dict | None = None) -> None:
        self._out = out if out is not None else sys.stdout
        self._price = price_per_1k  # {"prompt": x, "completion": y} per 1K tokens（可选）
        self._wall_start: float | None = None
        self._reset()

    def _reset(self) -> None:
        """重置 run 级计数，使同一 collector 可安全复用于多个 run。"""
        self.turns = 0
        self.model_calls = 0
        self.llm_time = 0.0
        self.tool_time = 0.0
        self.tool_counts: dict[str, int] = {}
        self.tool_time_by_name: dict[str, float] = {}
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0

    def __call__(self, event: Event) -> None:
        if isinstance(event, RunStarted):
            self._reset()
            self._wall_start = time.perf_counter()
        elif isinstance(event, TurnStarted):
            self.turns += 1
        elif isinstance(event, ModelCallFinished):
            self.model_calls += 1
            self.llm_time += event.latency_s
            self.prompt_tokens += event.prompt_tokens or 0
            self.completion_tokens += event.completion_tokens or 0
            self.total_tokens += event.total_tokens or 0
        elif isinstance(event, ToolCallFinished):
            self.tool_time += event.latency_s
            self.tool_counts[event.name] = self.tool_counts.get(event.name, 0) + 1
            self.tool_time_by_name[event.name] = (
                self.tool_time_by_name.get(event.name, 0.0) + event.latency_s
            )
        elif isinstance(event, (RunFinished, RunAborted)):
            self._report()

    def _report(self) -> None:
        wall = (time.perf_counter() - self._wall_start) if self._wall_start else 0.0
        lines = [
            "",
            "=== 📊 归因报告（best-effort）===",
            f"轮数            : {self.turns}",
            f"墙钟总耗时      : {wall:.2f}s",
            f"LLM 总耗时      : {self.llm_time:.2f}s  ({self.model_calls} 次调用)",
            f"工具总耗时      : {self.tool_time:.2f}s",
            f"tokens          : prompt={self.prompt_tokens} completion={self.completion_tokens} total={self.total_tokens}",
        ]
        if self.tool_counts:
            detail = ", ".join(
                f"{name}×{cnt}({self.tool_time_by_name.get(name, 0.0):.2f}s)"
                for name, cnt in sorted(self.tool_counts.items())
            )
            lines.append(f"工具明细        : {detail}")
        if self._price and self.total_tokens:
            cost = (
                self.prompt_tokens / 1000 * self._price.get("prompt", 0)
                + self.completion_tokens / 1000 * self._price.get("completion", 0)
            )
            lines.append(f"估算成本($)     : {cost:.4f}  (best-effort, 不用于精确计费)")
        print("\n".join(lines), file=self._out, flush=True)
