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
    return p.parse_args(argv)


def main() -> int:
    ns = _parse_args(sys.argv[1:])
    task = " ".join(ns.task).strip() if ns.task else sys.stdin.read().strip()
    if not task:
        print('usage: python -m mu "<task>" [--resume ID] [--branch NODE] [--stream]', file=sys.stderr)
        return 2

    try:
        if ns.resume:
            session = Session.load(ns.resume)
            if ns.branch:
                session.branch_from(ns.branch)
        else:
            session = Session()
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


async def _run(task: str, session: Session, emitter: EventEmitter, stream: bool) -> None:
    agent = Agent(emitter=emitter, session=session, stream=stream)
    await agent.run(task)


if __name__ == "__main__":
    raise SystemExit(main())
