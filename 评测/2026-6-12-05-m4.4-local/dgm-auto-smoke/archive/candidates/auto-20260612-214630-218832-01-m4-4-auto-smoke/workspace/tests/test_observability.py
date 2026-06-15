"""归因底座：合成事件喂 AttributionCollector，断言汇总与报告。"""
from __future__ import annotations

import io

from mu.events import (
    ModelCallFinished,
    RunFinished,
    RunStarted,
    ToolCallFinished,
    TurnStarted,
)
from mu.observability import AttributionCollector


def test_attribution_accumulates_and_reports():
    out = io.StringIO()
    c = AttributionCollector(out=out)
    c(RunStarted("t", "s"))
    c(TurnStarted(1))
    c(ModelCallFinished(1, 0.5, 10, 5, 15))
    c(ToolCallFinished("c1", "bash", "ok", 0.2, False))
    c(TurnStarted(2))
    c(ModelCallFinished(2, 0.3, 8, 4, 12))
    c(ToolCallFinished("c2", "read", "data", 0.1, False))
    c(ToolCallFinished("c3", "bash", "ok", 0.4, False))
    c(RunFinished("done"))

    assert c.turns == 2
    assert c.model_calls == 2
    assert abs(c.llm_time - 0.8) < 1e-9
    assert abs(c.tool_time - 0.7) < 1e-9
    assert c.prompt_tokens == 18
    assert c.completion_tokens == 9
    assert c.total_tokens == 27
    assert c.tool_counts == {"bash": 2, "read": 1}

    report = out.getvalue()
    assert "归因报告" in report
    assert "total=27" in report
    assert "bash×2" in report


def test_attribution_optional_cost():
    out = io.StringIO()
    c = AttributionCollector(out=out, price_per_1k={"prompt": 0.001, "completion": 0.002})
    c(RunStarted("t", "s"))
    c(ModelCallFinished(1, 0.1, 1000, 1000, 2000))
    c(RunFinished("d"))
    assert "估算成本" in out.getvalue()


def test_collector_resets_between_runs():
    """P3a 回归：同一 collector 复用于多个 run，RunStarted 时重置、不跨 run 累计。"""
    out = io.StringIO()
    c = AttributionCollector(out=out)
    # run 1
    c(RunStarted("t1", "s1"))
    c(TurnStarted(1))
    c(ModelCallFinished(1, 0.1, 5, 5, 10))
    c(RunFinished("a"))
    # run 2 复用同一 collector
    c(RunStarted("t2", "s2"))
    c(TurnStarted(1))
    c(ModelCallFinished(1, 0.2, 3, 3, 6))
    c(RunFinished("b"))

    assert c.turns == 1          # 不是 2
    assert c.model_calls == 1    # 不是 2
    assert c.total_tokens == 6   # 不是 16
