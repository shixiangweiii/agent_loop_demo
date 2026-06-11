"""Agent loop 测试：用 FakeModel 脚本化驱动，验证循环/终止/消息结构。无网络、无付费。"""
from __future__ import annotations

import json

from mu.agent import Agent
from mu.tools import ToolRegistry


# ---- 模仿 openai ChatCompletionMessage 的最小 fake 对象 ----

class FakeFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class FakeToolCall:
    def __init__(self, id: str, name: str, arguments: str) -> None:
        self.id = id
        self.function = FakeFunction(name, arguments)


class FakeMessage:
    def __init__(self, content=None, tool_calls=None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class FakeModel:
    """按脚本逐轮返回 message。"""

    def __init__(self, scripted: list[FakeMessage]) -> None:
        self._scripted = list(scripted)
        self.calls = 0
        self.seen_messages: list[list[dict]] = []

    async def acomplete(self, messages, tools):
        self.seen_messages.append(list(messages))
        msg = self._scripted[self.calls]
        self.calls += 1
        return msg


def _silent(*_args) -> None:
    pass


async def test_loop_executes_tool_then_stops(tmp_path):
    p = tmp_path / "out.txt"
    scripted = [
        FakeMessage(
            content=None,
            tool_calls=[
                FakeToolCall("call_1", "write", f'{{"path": "{p.as_posix()}", "content": "hi"}}')
            ],
        ),
        FakeMessage(content="Done. Wrote the file.", tool_calls=None),
    ]
    model = FakeModel(scripted)
    agent = Agent(model=model, tools=ToolRegistry(), emit=_silent)

    final = await agent.run("write hi to a file")

    assert final == "Done. Wrote the file."
    assert p.read_text() == "hi"
    assert model.calls == 2
    # 线性历史：system, user, assistant(tool_calls), tool, assistant(final)
    roles = [m["role"] for m in agent.messages]
    assert roles == ["system", "user", "assistant", "tool", "assistant"]
    assert agent.messages[3]["tool_call_id"] == "call_1"
    assert agent.messages[3]["content"].startswith("Wrote")


async def test_loop_no_tool_calls_immediate_stop():
    scripted = [FakeMessage(content="Nothing to do.", tool_calls=None)]
    agent = Agent(model=FakeModel(scripted), tools=ToolRegistry(), emit=_silent)

    final = await agent.run("hi")

    assert final == "Nothing to do."
    roles = [m["role"] for m in agent.messages]
    assert roles == ["system", "user", "assistant"]


async def test_loop_handles_bad_json_arguments():
    scripted = [
        FakeMessage(content=None, tool_calls=[FakeToolCall("c1", "write", "{not valid json")]),
        FakeMessage(content="ok", tool_calls=None),
    ]
    agent = Agent(model=FakeModel(scripted), tools=ToolRegistry(), emit=_silent)

    final = await agent.run("x")

    assert final == "ok"
    # 坏 JSON 转成错误字符串回填，不崩
    assert "not valid JSON" in agent.messages[3]["content"]


async def test_loop_multiple_tool_calls_in_one_turn(tmp_path):
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    scripted = [
        FakeMessage(
            content=None,
            tool_calls=[
                FakeToolCall("c1", "write", f'{{"path": "{a.as_posix()}", "content": "A"}}'),
                FakeToolCall("c2", "write", f'{{"path": "{b.as_posix()}", "content": "B"}}'),
            ],
        ),
        FakeMessage(content="both written", tool_calls=None),
    ]
    agent = Agent(model=FakeModel(scripted), tools=ToolRegistry(), emit=_silent)

    final = await agent.run("write two files")

    assert final == "both written"
    assert a.read_text() == "A"
    assert b.read_text() == "B"
    # system,user,assistant(2 tool_calls),tool,tool,assistant
    roles = [m["role"] for m in agent.messages]
    assert roles == ["system", "user", "assistant", "tool", "tool", "assistant"]


async def test_loop_read_edit_bash_closed_loop(tmp_path):
    """M0 完成标志的回归保护网：read -> edit -> bash 多轮闭环，真工具执行。"""
    src = tmp_path / "calc.py"
    src.write_text("def add(a, b):\n    return a - b\n")  # 故意写错（减号）
    scripted = [
        FakeMessage(tool_calls=[FakeToolCall("c1", "read", json.dumps({"path": src.as_posix()}))]),
        FakeMessage(tool_calls=[FakeToolCall("c2", "edit", json.dumps(
            {"path": src.as_posix(), "old_string": "a - b", "new_string": "a + b"}))]),
        FakeMessage(tool_calls=[FakeToolCall("c3", "bash", json.dumps(
            {"command": f"grep -q 'a + b' {src.as_posix()}"}))]),
        FakeMessage(content="fixed"),
    ]
    model = FakeModel(scripted)
    agent = Agent(model=model, tools=ToolRegistry(), emit=_silent)

    final = await agent.run("修复 add 的 bug 并验证")

    assert final == "fixed"
    assert src.read_text() == "def add(a, b):\n    return a + b\n"
    roles = [m["role"] for m in agent.messages]
    assert roles == [
        "system", "user",
        "assistant", "tool",   # read
        "assistant", "tool",   # edit
        "assistant", "tool",   # bash(grep)
        "assistant",           # final
    ]
    tool_msgs = [m for m in agent.messages if m["role"] == "tool"]
    assert "a - b" in tool_msgs[0]["content"]          # read 回填了原(错误)内容
    assert "1 replacement" in tool_msgs[1]["content"]  # edit 成功
    assert "exit code: 0" in tool_msgs[2]["content"]   # grep 命中修复后的内容
    # 每轮模型都收到累积的、以 tool 结果结尾的历史（OpenAI tool-call 结构保持）
    assert model.seen_messages[1][-1]["role"] == "tool"
    assert model.seen_messages[2][-1]["role"] == "tool"
