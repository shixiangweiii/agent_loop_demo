"""上下文管线：transform_context → convert_to_llm。

对应 Pi 的 AgentMessage[] → transformContext() → convertToLlm() → Message[]。
- transform_context：裁剪/注入的钩子（M1 默认 identity，完整 compaction 留后续）。
- convert_to_llm：把内部历史（含自定义消息类型）转成 OpenAI 格式 list[dict]。
  标准消息（system/user/assistant/tool）**透传**，从而保持 M0 行为不变。
"""
from __future__ import annotations

from typing import Any

STANDARD_ROLES = {"system", "user", "assistant", "tool"}


def transform_context(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """M1 默认 identity。后续可在此做压缩/裁剪/注入；保留为可替换钩子。"""
    return messages


def convert_to_llm(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """内部历史 → OpenAI 格式。标准消息透传；自定义类型转换或丢弃。"""
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.get("role") in STANDARD_ROLES:
            out.append(m)
        elif m.get("type") == "branch_summary":
            # 把侧分支摘要作为上下文注入主线
            out.append({"role": "user", "content": f"[侧分支摘要] {m.get('content', '')}"})
        # 其它未知自定义类型：不进入 LLM 上下文
    return out
