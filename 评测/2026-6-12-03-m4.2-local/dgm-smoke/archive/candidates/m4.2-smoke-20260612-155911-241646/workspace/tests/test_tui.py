"""TUI 测试：Textual Pilot 离线驱动（FakeModel，无网络）。验证 TUI 与 headless 共享同一 core。"""
from __future__ import annotations

import pytest

pytest.importorskip("textual")  # 仅安装了 [dev] 而无 [tui] 时跳过

from mu.agent import Agent  # noqa: E402
from mu.events import (  # noqa: E402
    AssistantText,
    ModelCallFinished,
    RunFinished,
    RunStarted,
    ToolCallFinished,
    ToolCallStarted,
    TurnStarted,
)
from mu.model import ModelResult  # noqa: E402
from mu.session import Session  # noqa: E402
from mu.tui import MuApp, TuiRenderer  # noqa: E402


# ---- fakes ----
class FF:
    def __init__(self, n, a): self.name = n; self.arguments = a
class FTC:
    def __init__(self, i, n, a): self.id = i; self.function = FF(n, a)
class FM:
    def __init__(self, content=None, tool_calls=None): self.content = content; self.tool_calls = tool_calls
class FakeModel:
    def __init__(self, scripted): self._scr = scripted; self.i = 0
    async def acomplete(self, messages, tools, *, stream=False, on_delta=None):
        m = self._scr[self.i]; self.i += 1
        return ModelResult(message=m, total_tokens=7)


class _FakeSubmit:
    """模拟 Input.Submitted（避免依赖 Pilot 的按键/消息分发，直接测我们的处理器）。"""
    def __init__(self, inp, value):
        self.input = inp
        self.value = value


def _factory(scripted):
    def make(emitter, session, stream):
        return Agent(model=FakeModel(scripted), emitter=emitter, session=session, stream=stream,
                     extensions=False)
    return make


async def _wait_idle(app, pilot, tries: int = 200) -> None:
    for _ in range(tries):
        await pilot.pause()
        if app._agent_worker is not None and not app._agent_busy:
            return
    raise AssertionError("agent run did not complete")


async def test_tui_runs_task_and_shares_core(tmp_path):
    """initial_task 自动提交，含 tool call：证明 TUI 复用同一 Agent/Session/tools（真写文件）。"""
    p = tmp_path / "out.txt"
    scripted = [
        FM(tool_calls=[FTC("c1", "write", f'{{"path": "{p.as_posix()}", "content": "hi"}}')]),
        FM(content="done"),
    ]
    app = MuApp(
        session=Session(base_dir=tmp_path),
        agent_factory=_factory(scripted),
        initial_task="write hi to a file",
    )
    async with app.run_test() as pilot:
        await _wait_idle(app, pilot)
        assert p.read_text() == "hi"
        roles = [m.get("role") for m in app.session.path_to_head()]
        assert roles == ["system", "user", "assistant", "tool", "assistant"]
        assert app._renderer.turns == 2
        assert app._agent_busy is False


async def test_tui_input_submit_starts_run_and_clears_input(tmp_path):
    """提交输入 → on_input_submitted → 起 worker 跑 agent，并清空输入框。"""
    app = MuApp(session=Session(base_dir=tmp_path), agent_factory=_factory([FM(content="ok")]))
    async with app.run_test() as pilot:
        inp = app.query_one("#task")
        inp.value = "do it"
        # 经 app 回调队列触发（设置 Textual active-app 上下文，run_worker 才正确挂载）
        app.call_later(app.on_input_submitted, _FakeSubmit(inp, "do it"))
        await _wait_idle(app, pilot)
        assert [m.get("role") for m in app.session.path_to_head()] == ["system", "user", "assistant"]
        assert inp.value == ""  # 提交后清空


def test_tui_renderer_maps_events_offline():
    """TuiRenderer 单元测试：不启动 app，用假 widget。"""
    class FakeLog:
        def __init__(self): self.writes = []
        def write(self, r): self.writes.append(r)

    class FakeLive:
        def __init__(self): self.content = None
        def update(self, r): self.content = r

    statuses: list[str] = []
    r = TuiRenderer(FakeLog(), FakeLive(), statuses.append)
    r(RunStarted("task", "sid"))
    r(TurnStarted(1))
    r(ToolCallStarted("c1", "bash", "{}"))
    r(ToolCallFinished("c1", "bash", "ok", 0.1, False))
    r(ModelCallFinished(1, 0.2, 3, 4, 7))
    r(AssistantText("hello"))
    r(RunFinished("hello"))

    assert r.turns == 1
    assert r.total_tokens == 7
    assert r._log.writes  # 有写入
    assert statuses[0] == "running…"
    assert any("turns=1" in s for s in statuses)


def test_tui_renderer_extension_events():
    """P2 回归：TuiRenderer 渲染扩展事件（与 headless StdoutRenderer 对齐，扩展不黑盒）。"""
    from mu.events import ExtensionError, ExtensionLoaded, ExtensionLog, ExtensionUnloaded

    class FakeLog:
        def __init__(self): self.writes = []
        def write(self, r): self.writes.append(str(r))

    class FakeLive:
        def update(self, r): pass

    statuses: list[str] = []
    r = TuiRenderer(FakeLog(), FakeLive(), statuses.append)
    r(ExtensionLoaded("ext", "0.1", ["foo"]))
    r(ExtensionLog("ext", "info", "hello-log"))
    r(ExtensionError("ext", "boom"))
    r(ExtensionUnloaded("ext"))

    text = " ".join(r._log.writes)
    assert "loaded ext" in text and "foo" in text
    assert "hello-log" in text
    assert "boom" in text
    assert "unloaded ext" in text
    assert any("extension error" in s for s in statuses)
