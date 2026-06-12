"""M3 自延伸：ToolRegistry 动态注册 + 扩展子进程 load/call/state/error/cleanup + agent 自延伸闭环。

全部离线：扩展是本地 python 子进程（import mu.extsdk），无 LLM/网络。
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from mu.agent import Agent
from mu.events import EventEmitter, ExtensionError, ExtensionLoaded
from mu.extension import ExtensionManager
from mu.model import ModelResult
from mu.session import Session
from mu.tools import ToolRegistry, ToolResult


@pytest.fixture
def example_path() -> str:
    return str(Path(__file__).resolve().parent.parent / "extensions" / "example_textstats.py")


def _schema(name: str) -> dict:
    return {"type": "function", "function": {"name": name, "parameters": {"type": "object", "properties": {}}}}


# ---------- ToolRegistry register/unregister ----------

async def test_registry_register_execute_unregister():
    reg = ToolRegistry()

    async def handler(args):
        return ToolResult("ok", terminate=True)

    reg.register("foo", _schema("foo"), handler)
    assert "foo" in reg.names()
    assert any(s["function"]["name"] == "foo" for s in reg.schemas())

    r = await reg.execute("foo", {})
    assert r == "ok" and r.terminate is True

    reg.unregister("foo")
    assert "foo" not in reg.names()
    assert all(s["function"]["name"] != "foo" for s in reg.schemas())


def test_registry_rejects_collisions_and_protects_builtins():
    reg = ToolRegistry()

    async def handler(args):
        return "x"

    with pytest.raises(ValueError):
        reg.register("read", _schema("read"), handler)  # 与内置重名
    reg.register("foo", _schema("foo"), handler)
    with pytest.raises(ValueError):
        reg.register("foo", _schema("foo"), handler)  # 重复
    reg.unregister("read")  # 内置受保护，应无效
    assert "read" in reg.names()


# ---------- ExtensionManager 子进程协议 ----------

async def test_load_and_call_extension(tmp_path, example_path):
    reg = ToolRegistry()
    session = Session(base_dir=tmp_path)
    emitter = EventEmitter()
    events: list = []
    emitter.subscribe(events.append)
    mgr = ExtensionManager(reg, session, emitter)
    try:
        ext = await mgr.load(example_path)
        assert not isinstance(ext, str), ext
        assert "word_count" in reg.names() and "reverse_text" in reg.names()
        assert (await reg.execute("word_count", {"text": "hello world foo"})) == "3"
        assert (await reg.execute("reverse_text", {"text": "abc"})) == "cba"
        assert any(isinstance(e, ExtensionLoaded) for e in events)
    finally:
        await mgr.aclose()
    assert "word_count" not in reg.names()  # aclose 后注销


async def test_extension_log_event(tmp_path, example_path):
    reg, session, emitter = ToolRegistry(), Session(base_dir=tmp_path), EventEmitter()
    events: list = []
    emitter.subscribe(events.append)
    mgr = ExtensionManager(reg, session, emitter)
    try:
        await mgr.load(example_path)
        await reg.execute("word_count", {"text": "a b"})  # word_count 内部 log()
    finally:
        await mgr.aclose()
    from mu.events import ExtensionLog
    assert any(isinstance(e, ExtensionLog) for e in events)


async def test_extension_state_persists_across_resume(tmp_path, example_path):
    # 会话1：set_prefix 持久化 state
    reg1, session1, em1 = ToolRegistry(), Session(base_dir=tmp_path), EventEmitter()
    mgr1 = ExtensionManager(reg1, session1, em1)
    sid = session1.id
    try:
        await mgr1.load(example_path)
        await reg1.execute("set_prefix", {"prefix": "Hi"})
    finally:
        await mgr1.aclose()

    # 会话2：resume → state 恢复
    reg2, session2, em2 = ToolRegistry(), Session.load(sid, base_dir=tmp_path), EventEmitter()
    mgr2 = ExtensionManager(reg2, session2, em2)
    try:
        await mgr2.load(example_path)
        assert (await reg2.execute("greet", {"name": "Bob"})) == "Hi, Bob!"
    finally:
        await mgr2.aclose()


async def test_extension_tool_error_returns_string_and_emits_event(tmp_path, example_path):
    reg, session, emitter = ToolRegistry(), Session(base_dir=tmp_path), EventEmitter()
    events: list = []
    emitter.subscribe(events.append)
    mgr = ExtensionManager(reg, session, emitter)
    try:
        await mgr.load(example_path)
        r = await reg.execute("word_count", {})  # 缺 text → 扩展内 KeyError
        assert "Error" in r
        assert any(isinstance(e, ExtensionError) for e in events)
    finally:
        await mgr.aclose()


async def test_extension_name_collision_rejected(tmp_path):
    ext = tmp_path / "bad.py"
    ext.write_text(
        "from mu.extsdk import tool, run_extension\n"
        "@tool(name='read', description='x', parameters={'type':'object','properties':{}})\n"
        "def r(args):\n    return 'nope'\n"
        "if __name__ == '__main__':\n    run_extension('bad', '0.1')\n"
    )
    reg, session, emitter = ToolRegistry(), Session(base_dir=tmp_path), EventEmitter()
    mgr = ExtensionManager(reg, session, emitter)
    try:
        res = await mgr.load(str(ext))
        assert isinstance(res, str) and "conflict" in res.lower()
        assert "read" in reg.names()  # 内置未被破坏
    finally:
        await mgr.aclose()


async def test_aclose_terminates_subprocess(tmp_path, example_path):
    reg, session, emitter = ToolRegistry(), Session(base_dir=tmp_path), EventEmitter()
    mgr = ExtensionManager(reg, session, emitter)
    ext = await mgr.load(example_path)
    assert ext.process.returncode is None
    await mgr.aclose()
    assert ext.process.returncode is not None


async def test_extension_crash_during_call_degrades_fast(tmp_path):
    """P1-a 回归：扩展执行中崩溃 → 当前调用秒级返回错误（非 120s）+ 工具注销 + 报错事件。"""
    ext = tmp_path / "crasher.py"
    ext.write_text(
        "from mu.extsdk import tool, run_extension\n"
        "@tool(name='crash', description='crash', parameters={'type':'object','properties':{}})\n"
        "def crash(args):\n    import os\n    os._exit(7)\n"
        "if __name__ == '__main__':\n    run_extension('crasher', '0.1')\n"
    )
    reg, session, emitter = ToolRegistry(), Session(base_dir=tmp_path), EventEmitter()
    events: list = []
    emitter.subscribe(events.append)
    mgr = ExtensionManager(reg, session, emitter)
    try:
        await mgr.load(str(ext))
        t0 = time.perf_counter()
        r = await asyncio.wait_for(reg.execute("crash", {}), timeout=10)
        assert (time.perf_counter() - t0) < 10  # 远小于 _CALL_TIMEOUT=120
        assert "Error" in r
        assert "crash" not in reg.names()  # 崩溃扩展的工具已注销
        assert any(isinstance(e, ExtensionError) for e in events)
    finally:
        await mgr.aclose()


async def test_extension_same_name_rejected_no_leak(tmp_path):
    """P1-b 回归：同名扩展第二次加载被拒；首个完好可用；aclose 无残留。"""
    def _write(fn, toolname):
        (tmp_path / fn).write_text(
            "from mu.extsdk import tool, run_extension\n"
            f"@tool(name='{toolname}', description='x', parameters={{'type':'object','properties':{{}}}})\n"
            f"def f(args):\n    return '{toolname}'\n"
            "if __name__ == '__main__':\n    run_extension('same', '0.1')\n"
        )
    _write("a.py", "tool_a")
    _write("b.py", "tool_b")
    reg, session, emitter = ToolRegistry(), Session(base_dir=tmp_path), EventEmitter()
    mgr = ExtensionManager(reg, session, emitter)
    try:
        e1 = await mgr.load(str(tmp_path / "a.py"))
        e2 = await mgr.load(str(tmp_path / "b.py"))
        assert not isinstance(e1, str)
        assert isinstance(e2, str) and "already loaded" in e2  # 第二次被拒
        assert "tool_a" in reg.names() and "tool_b" not in reg.names()
        assert (await reg.execute("tool_a", {})) == "tool_a"  # 首个仍可用
    finally:
        await mgr.aclose()
    assert "tool_a" not in reg.names()  # aclose 后清理


# ---------- agent 级自延伸闭环（FakeModel 离线，证明完成标志）----------

class _FF:
    def __init__(self, n, a): self.name = n; self.arguments = a
class _FTC:
    def __init__(self, i, n, a): self.id = i; self.function = _FF(n, a)
class _FM:
    def __init__(self, content=None, tool_calls=None): self.content = content; self.tool_calls = tool_calls
class FakeModel:
    def __init__(self, scripted): self._scr = scripted; self.i = 0
    async def acomplete(self, messages, tools, *, stream=False, on_delta=None):
        m = self._scr[self.i]; self.i += 1
        return ModelResult(message=m)


async def test_agent_self_extension_closed_loop(tmp_path, example_path):
    session = Session(base_dir=tmp_path)
    scripted = [
        _FM(tool_calls=[_FTC("c1", "load_extension", json.dumps({"path": example_path}))]),
        _FM(tool_calls=[_FTC("c2", "word_count", json.dumps({"text": "a b c d"}))]),
        _FM(content="done: 4 words"),
    ]
    # ext_dir 指向空目录，避免 autoload 自动加载仓库示例（这里要显式 load）
    agent = Agent(model=FakeModel(scripted), session=session, ext_dir=tmp_path / "noext")
    try:
        final = await agent.run("count words via a self-written extension")
        assert final == "done: 4 words"
        tool_msgs = [m for m in agent.messages if m["role"] == "tool"]
        assert "Loaded extension textstats" in tool_msgs[0]["content"]
        assert tool_msgs[1]["content"] == "4"
    finally:
        await agent.aclose()
