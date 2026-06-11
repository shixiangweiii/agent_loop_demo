"""Agent loop 测试：FakeModel 脚本化驱动，验证循环/终止/消息结构。无网络、无付费。

M1：FakeModel 返回 ModelResult；Agent 用 Session（tmp 目录，不污染工作目录）。
行为断言（文件内容、roles、闭环）保持不变。
"""
from __future__ import annotations

import asyncio
import json

from mu.agent import Agent
from mu.context import convert_to_llm
from mu.model import ModelResult
from mu.session import Session
from mu.tools import ToolRegistry, ToolResult


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
    """按脚本逐轮返回 ModelResult。"""

    def __init__(self, scripted: list[FakeMessage]) -> None:
        self._scripted = list(scripted)
        self.calls = 0
        self.seen_messages: list[list[dict]] = []

    async def acomplete(self, messages, tools, *, stream=False, on_delta=None):
        self.seen_messages.append(list(messages))
        msg = self._scripted[self.calls]
        self.calls += 1
        return ModelResult(message=msg, prompt_tokens=1, completion_tokens=1, total_tokens=2)


def _agent(model, tmp_path) -> Agent:
    return Agent(model=model, tools=ToolRegistry(), session=Session(base_dir=tmp_path))


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
    agent = _agent(model, tmp_path)

    final = await agent.run("write hi to a file")

    assert final == "Done. Wrote the file."
    assert p.read_text() == "hi"
    assert model.calls == 2
    roles = [m["role"] for m in agent.messages]
    assert roles == ["system", "user", "assistant", "tool", "assistant"]
    assert agent.messages[3]["tool_call_id"] == "call_1"
    assert agent.messages[3]["content"].startswith("Wrote")


async def test_loop_no_tool_calls_immediate_stop(tmp_path):
    scripted = [FakeMessage(content="Nothing to do.", tool_calls=None)]
    agent = _agent(FakeModel(scripted), tmp_path)

    final = await agent.run("hi")

    assert final == "Nothing to do."
    roles = [m["role"] for m in agent.messages]
    assert roles == ["system", "user", "assistant"]


async def test_loop_handles_bad_json_arguments(tmp_path):
    scripted = [
        FakeMessage(content=None, tool_calls=[FakeToolCall("c1", "write", "{not valid json")]),
        FakeMessage(content="ok", tool_calls=None),
    ]
    agent = _agent(FakeModel(scripted), tmp_path)

    final = await agent.run("x")

    assert final == "ok"
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
    agent = _agent(FakeModel(scripted), tmp_path)

    final = await agent.run("write two files")

    assert final == "both written"
    assert a.read_text() == "A"
    assert b.read_text() == "B"
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
    agent = _agent(model, tmp_path)

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
    assert model.seen_messages[1][-1]["role"] == "tool"
    assert model.seen_messages[2][-1]["role"] == "tool"


class _SlowRegistry:
    """工具执行很慢，便于测试「执行中被取消」。"""

    def schemas(self):
        return [{
            "type": "function",
            "function": {"name": "slow", "description": "slow",
                         "parameters": {"type": "object", "properties": {}}},
        }]

    async def execute(self, name, args):
        await asyncio.sleep(10)
        return ToolResult("done")


async def test_cancel_during_tool_execution_keeps_session_resumable(tmp_path):
    """P1 回归：工具执行中被取消，session 不留 dangling tool_call（每个 tool_call 都有结果）。"""
    session = Session(base_dir=tmp_path)
    model = FakeModel([FakeMessage(tool_calls=[FakeToolCall("call_1", "slow", "{}")])])
    agent = Agent(model=model, tools=_SlowRegistry(), session=session)

    task = asyncio.create_task(agent.run("trigger slow tool"))
    await asyncio.sleep(0.05)  # 让它进入工具执行
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    path = session.path_to_head()
    roles = [m["role"] for m in path]
    assert roles == ["system", "user", "assistant", "tool"]
    assert path[-1]["tool_call_id"] == "call_1"
    assert "cancelled" in path[-1]["content"]
    # 协议完整性：assistant 的每个 tool_call id 都有对应 tool 结果
    tc_ids = {tc["id"] for tc in path[2]["tool_calls"]}
    tool_ids = {m["tool_call_id"] for m in path if m["role"] == "tool"}
    assert tc_ids <= tool_ids


async def test_summarize_branch_brings_summary_to_mainline(tmp_path):
    """P2 回归：side-quest 分支 → 回主线 → summary 注入 LLM 上下文。"""
    session = Session(base_dir=tmp_path)
    session.append({"role": "system", "content": "sys"})
    user_id = session.append({"role": "user", "content": "main task"})
    main_leaf = session.append({"role": "assistant", "content": "main answer"})
    session.branch_from(user_id)  # 从 user 开 side-quest
    side_leaf = session.append({"role": "assistant", "content": "fixed tool X on side quest"})

    agent = Agent(model=FakeModel([]), tools=ToolRegistry(), session=session)
    agent.summarize_branch(side_leaf, return_to=main_leaf)  # deterministic 概括

    path = session.path_to_head()
    assert path[-1]["type"] == "branch_summary"
    assert "tool X" in path[-1]["content"]
    # 主线路径不含 side-quest 的 assistant（除了带回的摘要）
    assert all("side quest" not in str(m.get("content", "")) for m in path[:-1])
    # 摘要被 convert_to_llm 注入为 user 上下文
    llm = convert_to_llm(path)
    assert any(m["role"] == "user" and "侧分支摘要" in m.get("content", "") for m in llm)
