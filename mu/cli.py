"""CLI 入口：取 task（argv 优先，否则 stdin）→ asyncio.run(Agent.run)。

CLI 体验是同步的（asyncio.run），但内核 loop / model / tool 全程 async。
"""
from __future__ import annotations

import asyncio
import sys

from .agent import Agent
from .model import ConfigError


def main() -> int:
    args = sys.argv[1:]
    task = " ".join(args).strip() if args else sys.stdin.read().strip()
    if not task:
        print('usage: python -m mu "<task>"   (or pipe the task via stdin)', file=sys.stderr)
        return 2
    try:
        asyncio.run(_run(task))
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n[interrupted]", file=sys.stderr)
        return 130
    return 0


async def _run(task: str) -> None:
    agent = Agent()
    await agent.run(task)  # 输出已通过 emit 实时打印


if __name__ == "__main__":
    raise SystemExit(main())
