"""流式累积：consume_stream 把分片 chunks 拼成完整 message（含 tool_call 增量）。"""
from __future__ import annotations

from mu.model import consume_stream


class D:
    """通用属性容器，模仿 openai 流式 chunk/delta 的鸭子类型。"""

    def __init__(self, **kw):
        self.__dict__.update(kw)


async def _aiter(items):
    for it in items:
        yield it


async def test_consume_stream_accumulates_text_and_tool_calls():
    deltas: list[str] = []
    chunks = [
        D(choices=[D(delta=D(content="Hel", tool_calls=None))], usage=None),
        D(choices=[D(delta=D(content="lo", tool_calls=None))], usage=None),
        D(choices=[D(delta=D(content=None, tool_calls=[
            D(index=0, id="call_1", function=D(name="bash", arguments='{"comm'))]))], usage=None),
        D(choices=[D(delta=D(content=None, tool_calls=[
            D(index=0, id=None, function=D(name=None, arguments='and": "ls"}'))]))], usage=None),
        D(choices=[], usage=D(prompt_tokens=3, completion_tokens=4, total_tokens=7)),
    ]

    msg, usage = await consume_stream(_aiter(chunks), on_delta=deltas.append)

    assert msg.content == "Hello"
    assert deltas == ["Hel", "lo"]
    assert len(msg.tool_calls) == 1
    tc = msg.tool_calls[0]
    assert tc.id == "call_1"
    assert tc.function.name == "bash"
    assert tc.function.arguments == '{"command": "ls"}'
    assert usage.total_tokens == 7


async def test_consume_stream_text_only():
    msg, usage = await consume_stream(
        _aiter([D(choices=[D(delta=D(content="hi", tool_calls=None))], usage=None)])
    )
    assert msg.content == "hi"
    assert msg.tool_calls is None
