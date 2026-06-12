"""Native code-action：进程内 exec，mu.* 一轮组合多工具；受权限约束；可观测；默认关。"""
from __future__ import annotations

import json

from mu.agent import Agent
from mu.codeact import CodeAction
from mu.events import EventEmitter, ToolCallStarted
from mu.model import ModelResult
from mu.permission import read_only
from mu.session import Session
from mu.tools import ToolRegistry


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


async def test_code_action_combines_tools_in_one_round(tmp_path):
    """收益：一次 code 调用内循环读 3 个文件（1 轮），而非 3 轮逐文件 tool-call。"""
    for i in range(3):
        (tmp_path / f"f{i}.txt").write_text(f"content-{i}")  # 各 9 字符
    paths = [str(tmp_path / f"f{i}.txt") for i in range(3)]
    code = "\n".join([
        f"paths = {paths!r}",
        "total = 0",
        "for p in paths:",
        "    total += len(mu.read(p))",
        "mu.result('read %d files, total %d chars' % (len(paths), total))",
    ])
    model = FakeModel([
        _FM(tool_calls=[_FTC("c1", "code", json.dumps({"code": code}))]),
        _FM(content="done"),
    ])
    events: list = []
    emitter = EventEmitter()
    emitter.subscribe(events.append)
    agent = Agent(model=model, emitter=emitter, session=Session(base_dir=tmp_path),
                  extensions=False, code_action=True)

    final = await agent.run("count chars in 3 files")

    assert final == "done"
    assert model.i == 2  # 只 1 轮 code + 1 轮 final（对比 tool-call 路径需 3 轮 read）
    tool_msgs = [m for m in agent.messages if m["role"] == "tool"]
    assert "read 3 files, total 27 chars" in tool_msgs[0]["content"]
    # 内层 read 真执行且可观测（3 对内层 ToolCall 事件）
    inner = [e for e in events if isinstance(e, ToolCallStarted) and e.call_id == "code:read"]
    assert len(inner) == 3


async def test_code_action_disabled_by_default(tmp_path):
    reg = ToolRegistry()
    Agent(model=FakeModel([]), tools=reg, session=Session(base_dir=tmp_path), extensions=False)
    assert "code" not in reg.names()  # 默认不注册 code 工具


async def test_code_action_constrained_by_permission(tmp_path):
    """read_only 下，code 里 mu.write 被拒（证明 code-action 受 permission 约束）。"""
    reg = ToolRegistry(policy=read_only)
    CodeAction(reg, EventEmitter()).register()
    target = tmp_path / "x.txt"
    code = f"r = mu.write({str(target)!r}, 'hi')\nmu.result(r)"
    out = await reg.execute("code", {"code": code})
    assert "permission denied" in out
    assert not target.exists()


async def test_code_action_reports_errors(tmp_path):
    reg = ToolRegistry()
    CodeAction(reg, EventEmitter()).register()
    out = await reg.execute("code", {"code": "raise ValueError('boom')"})
    assert "Error during code execution" in out and "boom" in out


async def test_code_blocked_entirely_under_readonly(tmp_path):
    """P1-a 回归：readonly 下整个 code 工具被拦（code_exec），连 open() 直写也无从发生。"""
    from mu.permission import read_only
    reg = ToolRegistry(policy=read_only)
    CodeAction(reg, EventEmitter()).register()
    target = tmp_path / "x.txt"
    out = await reg.execute("code", {"code": f"open({str(target)!r}, 'w').write('x')"})
    assert "permission denied" in out
    assert not target.exists()  # code 根本没跑


def test_muapi_cancelled_blocks_calls():
    """P1-b 回归：超时取消后，滞留线程的 mu.* 调用被拒（阻止与下一轮交错）。"""
    import pytest

    from mu.codeact import _MuApi
    api = _MuApi(None, None)
    api._cancelled = True
    with pytest.raises(RuntimeError):
        api.call("read", {})
