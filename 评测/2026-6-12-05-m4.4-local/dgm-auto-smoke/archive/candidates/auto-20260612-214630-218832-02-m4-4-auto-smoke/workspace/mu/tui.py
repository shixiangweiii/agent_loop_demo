"""Textual 前端（M2）。

TUI = 事件流的又一个消费者 + 输入驱动，零改 core：复用同一个 Agent/Session/EventEmitter。
agent.run 跑在 Textual 异步 worker（同一事件循环），故事件订阅者可直接更新 widget。
通过注入 agent_factory 可离线测试（FakeModel）。
"""
from __future__ import annotations

from typing import Callable

from rich.text import Text
from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, Input, RichLog, Static

from .agent import Agent
from .events import (
    AssistantText,
    AssistantTextDelta,
    ErrorEvent,
    Event,
    EventEmitter,
    ExtensionError,
    ExtensionLoaded,
    ExtensionLog,
    ExtensionUnloaded,
    ModelCallFinished,
    RunAborted,
    RunFinished,
    RunStarted,
    ToolCallFinished,
    ToolCallStarted,
    TurnStarted,
)
from .render import _short
from .session import Session

AgentFactory = Callable[[EventEmitter, Session, bool], Agent]


def _default_agent_factory(emitter: EventEmitter, session: Session, stream: bool) -> Agent:
    return Agent(emitter=emitter, session=session, stream=stream)


class TuiRenderer:
    """事件订阅者：把事件渲染到 RichLog / live Static，并维护归因 tally。

    与 StdoutRenderer 同构，只是写到 widget。所有动态内容用 rich.Text（不走 markup 解析），
    避免工具输出里的 `[` 被误当标记。
    """

    def __init__(self, log: RichLog, live: Static, set_status: Callable[[str], None]) -> None:
        self._log = log
        self._live = live
        self._set_status = set_status
        self._reset()

    def _reset(self) -> None:
        self.turns = 0
        self.llm_time = 0.0
        self.tool_time = 0.0
        self.total_tokens = 0
        self._delta_buf: list[str] = []

    def __call__(self, event: Event) -> None:
        if isinstance(event, RunStarted):
            self._reset()
            self._log.write(Text(f"👤 you  (session {event.session_id})", style="bold cyan"))
            self._log.write(Text(event.task))
            self._set_status("running…")
        elif isinstance(event, TurnStarted):
            self.turns += 1
        elif isinstance(event, AssistantTextDelta):
            self._delta_buf.append(event.delta)
            self._live.update(Text("".join(self._delta_buf), style="green"))
        elif isinstance(event, AssistantText):
            self._flush_live()
            self._log.write(Text("🤖 assistant", style="bold green"))
            self._log.write(Text(event.text))
        elif isinstance(event, ModelCallFinished):
            self.llm_time += event.latency_s
            self.total_tokens += event.total_tokens or 0
        elif isinstance(event, ToolCallStarted):
            self._flush_live()
            self._log.write(Text(f"🔧 {event.name}  {_short(event.args_preview)}", style="yellow"))
        elif isinstance(event, ToolCallFinished):
            self.tool_time += event.latency_s
            self._log.write(Text(f"📤 {_short(event.result, 500)}", style="grey50"))
        elif isinstance(event, RunFinished):
            self._flush_live()
            self._set_status(self._tally())
        elif isinstance(event, RunAborted):
            self._flush_live()
            self._log.write(Text(f"⛔ aborted: {event.reason}", style="red"))
            self._set_status("aborted")
        elif isinstance(event, ErrorEvent):
            self._log.write(Text(f"[error] {event.message}", style="red"))
        elif isinstance(event, ExtensionLoaded):
            self._log.write(Text(f"🧩 loaded {event.name} v{event.version}: {', '.join(event.tools)}", style="magenta"))
        elif isinstance(event, ExtensionUnloaded):
            self._log.write(Text(f"🧩 unloaded {event.name}", style="magenta"))
        elif isinstance(event, ExtensionLog):
            self._log.write(Text(f"🧩 [{event.name}] {event.message}", style="grey50"))
        elif isinstance(event, ExtensionError):
            self._log.write(Text(f"🧩 [{event.name}] error: {event.message}", style="red"))
            self._set_status(f"extension error: {event.name}")

    def _flush_live(self) -> None:
        buf = "".join(self._delta_buf)
        if buf:
            self._log.write(Text("🤖 assistant", style="bold green"))
            self._log.write(Text(buf))
            self._delta_buf = []
            self._live.update("")

    def _tally(self) -> str:
        return (
            f"turns={self.turns}  llm={self.llm_time:.2f}s  "
            f"tool={self.tool_time:.2f}s  tok={self.total_tokens}"
        )


class MuApp(App):
    CSS = """
    #log { height: 1fr; }
    #live { height: auto; padding: 0 1; }
    #task { dock: bottom; }
    """

    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("escape", "cancel", "Cancel run"),
    ]

    def __init__(
        self,
        session: Session | None = None,
        stream: bool = False,
        agent_factory: AgentFactory | None = None,
        initial_task: str | None = None,
    ) -> None:
        super().__init__()
        self.session = session or Session()
        self.stream = stream
        self._agent_factory = agent_factory or _default_agent_factory
        self._initial_task = initial_task
        self._agent_busy = False
        self._agent_worker = None
        self._agent = None  # 一个持久 agent，跨多轮复用（扩展/会话状态得以保留）
        self.emitter = EventEmitter()

    def compose(self) -> ComposeResult:
        yield Header()
        yield RichLog(id="log", wrap=True, markup=False)
        yield Static("", id="live")
        yield Input(placeholder="输入任务，回车提交（esc 取消运行 · ctrl+q 退出）", id="task")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "μ"
        self.sub_title = "idle"
        log = self.query_one("#log", RichLog)
        live = self.query_one("#live", Static)
        self._renderer = TuiRenderer(log, live, self._set_status)
        self.emitter.subscribe(self._renderer)
        self._agent = self._agent_factory(self.emitter, self.session, self.stream)
        self.query_one("#task", Input).focus()
        if self._initial_task:
            self.call_after_refresh(self._start_run, self._initial_task)

    def _set_status(self, text: str) -> None:
        self.sub_title = text

    def on_input_submitted(self, event: Input.Submitted) -> None:
        task = event.value.strip()
        if not task or self._agent_busy:
            return
        event.input.value = ""
        self._start_run(task)

    def _start_run(self, task: str) -> None:
        self._agent_busy = True
        self.query_one("#task", Input).disabled = True
        self._agent_worker = self.run_worker(self._run_task(task), exclusive=True)

    async def _run_task(self, task: str) -> None:
        try:
            await self._agent.run(task)
        except Exception as e:  # noqa: BLE001 - 渲染错误，不崩 UI
            self._renderer(ErrorEvent(str(e)))
        finally:
            self._agent_busy = False
            inp = self.query_one("#task", Input)
            inp.disabled = False
            inp.focus()

    def action_cancel(self) -> None:
        if self._agent_busy and self._agent_worker is not None:
            self._agent_worker.cancel()

    async def on_unmount(self) -> None:
        if self._agent is not None:
            await self._agent.aclose()  # best-effort：扩展子进程也会在父进程退出时随 stdin EOF 自终止
