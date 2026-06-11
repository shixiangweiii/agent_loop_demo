"""LocalEnvironment: 本地无状态执行层（async-first）。

M0 只做本地执行（YOLO，无沙箱——沙箱/权限留给 roadmap M3.5）。
所有可能阻塞事件循环的操作都 offload 到子进程或线程。
"""
from __future__ import annotations

import asyncio
import os
import signal
from dataclasses import dataclass
from pathlib import Path


@dataclass
class BashResult:
    stdout: str
    stderr: str
    exit_code: int


class LocalEnvironment:
    """本地执行：bash 子进程 + 文件读写。每次 bash 调用都是新进程（无状态）。"""

    async def run_bash(self, command: str, timeout: float = 120.0) -> BashResult:
        # start_new_session=True：命令成为新会话/进程组的 leader（pgid==pid），
        # 便于超时时按进程组整组清理，避免派生的后台子进程成为孤儿继续运行。
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            await self._kill_process_group(proc)
            return BashResult(
                stdout="",
                stderr=f"command timed out after {timeout}s",
                exit_code=124,
            )
        return BashResult(
            stdout=stdout_b.decode("utf-8", errors="replace"),
            stderr=stderr_b.decode("utf-8", errors="replace"),
            exit_code=proc.returncode if proc.returncode is not None else -1,
        )

    @staticmethod
    async def _kill_process_group(proc: asyncio.subprocess.Process) -> None:
        """超时清理：SIGKILL 整个进程组（含派生子进程），再回收僵尸进程。"""
        if proc.returncode is not None:
            return
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:  # 回退：至少杀顶层进程
                proc.kill()
            except ProcessLookupError:
                pass
        try:
            await proc.wait()
        except ProcessLookupError:
            pass

    async def read_file(self, path: str, offset: int = 0, limit: int | None = None) -> str:
        return await asyncio.to_thread(self._read_file_sync, path, offset, limit)

    @staticmethod
    def _read_file_sync(path: str, offset: int, limit: int | None) -> str:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        if offset == 0 and limit is None:
            return text
        lines = text.splitlines(keepends=True)
        end = None if limit is None else offset + limit
        return "".join(lines[offset:end])

    async def write_file(self, path: str, content: str) -> None:
        await asyncio.to_thread(self._write_file_sync, path, content)

    @staticmethod
    def _write_file_sync(path: str, content: str) -> None:
        p = Path(path)
        if p.parent and not p.parent.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
