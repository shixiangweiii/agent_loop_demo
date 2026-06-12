"""terminate 提示：本轮工具结果全部 terminate → loop 跳过自动后续 LLM 调用。"""
from __future__ import annotations

from mu.agent import Agent
from mu.model import ModelResult
from mu.session import Session
from mu.tools import ToolResult


class _FF:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _FTC:
    def __init__(self, id, name, arguments):
        self.id = id
        self.function = _FF(name, arguments)


class _FM:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class FakeModel:
    def __init__(self, scripted):
        self._scripted = scripted
        self.calls = 0

    async def acomplete(self, messages, tools, *, stream=False, on_delta=None):
        msg = self._scripted[self.calls]
        self.calls += 1
        return ModelResult(message=msg)


class TerminatingRegistry:
    """一个返回 terminate=True 的假工具注册表（鸭子类型）。"""

    def schemas(self):
        return [{
            "type": "function",
            "function": {"name": "stop", "description": "stop",
                         "parameters": {"type": "object", "properties": {}}},
        }]

    async def execute(self, name, args):
        return ToolResult("stopped", terminate=True)


async def test_terminate_stops_loop_after_tool_batch(tmp_path):
    scripted = [
        _FM(tool_calls=[_FTC("c1", "stop", "{}")]),
        _FM(content="should-not-reach"),  # 若 loop 继续才会用到
    ]
    model = FakeModel(scripted)
    agent = Agent(model=model, tools=TerminatingRegistry(), session=Session(base_dir=tmp_path), extensions=False)

    final = await agent.run("do something")

    assert model.calls == 1   # 第二次模型调用未发生（被 terminate 早停）
    assert final == ""
    roles = [m["role"] for m in agent.messages]
    assert roles == ["system", "user", "assistant", "tool"]


def test_tool_result_default_not_terminate():
    assert ToolResult("x").terminate is False
    assert ToolResult("x", terminate=True).terminate is True
    assert ToolResult("x") == "x"  # 仍是字符串（向后兼容）
