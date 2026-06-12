"""CLI 入口：argparse（task / --resume / --branch / --stream）→ asyncio.run。

装配事件订阅者（StdoutRenderer + AttributionCollector）；CLI 同步、内核全程 async。
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from .agent import Agent
from .events import EventEmitter
from .model import ConfigError
from .observability import AttributionCollector
from .render import StdoutRenderer
from .session import Session


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="mu", description="μ — minimal Pi-style coding agent")
    p.add_argument("task", nargs="*", help="任务描述（留空则从 stdin 读取）")
    p.add_argument("--resume", metavar="SESSION_ID", help="续跑已有会话")
    p.add_argument("--branch", metavar="NODE_ID", help="从指定节点分支（配合 --resume）")
    p.add_argument("--stream", action="store_true", help="流式输出（默认关）")
    p.add_argument("--tui", action="store_true", help="启动 Textual 交互式终端界面（默认 headless）")
    return p.parse_args(argv)


def _build_session(ns: argparse.Namespace) -> Session:
    if ns.resume:
        session = Session.load(ns.resume)
        if ns.branch:
            session.branch_from(ns.branch)
        return session
    return Session()


def main() -> int:
    ns = _parse_args(sys.argv[1:])
    task = " ".join(ns.task).strip() if ns.task else ""

    if ns.tui:
        return _run_tui(ns, task)

    # headless（默认）：行为与 M1 一致
    if not task:
        task = sys.stdin.read().strip()
    if not task:
        print('usage: python -m mu "<task>" [--resume ID] [--branch NODE] [--stream] [--tui]', file=sys.stderr)
        return 2
    try:
        session = _build_session(ns)
    except (FileNotFoundError, KeyError) as e:
        print(f"Session error: {e}", file=sys.stderr)
        return 1

    emitter = EventEmitter()
    emitter.subscribe(StdoutRenderer())
    emitter.subscribe(AttributionCollector())
    try:
        asyncio.run(_run(task, session, emitter, ns.stream))
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n[interrupted]", file=sys.stderr)
        return 130
    return 0


def _run_tui(ns: argparse.Namespace, task: str) -> int:
    from .model import Model  # 预检配置，避免进了 TUI 首次提交才报错

    try:
        Model()
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 1
    try:
        session = _build_session(ns)
    except (FileNotFoundError, KeyError) as e:
        print(f"Session error: {e}", file=sys.stderr)
        return 1
    try:
        from .tui import MuApp
    except ImportError:
        print('TUI 需要 textual，请安装：pip install -e ".[tui]"', file=sys.stderr)
        return 1
    MuApp(session=session, stream=ns.stream, initial_task=task or None).run()
    return 0


async def _run(task: str, session: Session, emitter: EventEmitter, stream: bool) -> None:
    agent = Agent(emitter=emitter, session=session, stream=stream)
    try:
        await agent.run(task)
    finally:
        await agent.aclose()  # 关闭扩展子进程（即使被取消/异常）


if __name__ == "__main__":
    raise SystemExit(main())
