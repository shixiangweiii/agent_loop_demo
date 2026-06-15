"""上下文管线：transform_context（identity）+ convert_to_llm（透传/注入/丢弃）。"""
from __future__ import annotations

from mu.context import convert_to_llm, transform_context


def test_transform_context_identity():
    msgs = [{"role": "user", "content": "x"}]
    assert transform_context(msgs) == msgs


def test_convert_passes_standard_messages_unchanged():
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]},
        {"role": "tool", "tool_call_id": "1", "content": "r"},
    ]
    assert convert_to_llm(msgs) == msgs  # 标准消息透传（保证 M0 行为不变）


def test_convert_injects_branch_summary_as_user():
    msgs = [
        {"role": "user", "content": "main"},
        {"type": "branch_summary", "content": "修好了 X"},
    ]
    out = convert_to_llm(msgs)
    assert out[0] == {"role": "user", "content": "main"}
    assert out[1]["role"] == "user"
    assert "X" in out[1]["content"]


def test_convert_drops_unknown_custom_types():
    msgs = [
        {"role": "user", "content": "u"},
        {"type": "some_internal_marker", "content": "ignore me"},
    ]
    out = convert_to_llm(msgs)
    assert out == [{"role": "user", "content": "u"}]
