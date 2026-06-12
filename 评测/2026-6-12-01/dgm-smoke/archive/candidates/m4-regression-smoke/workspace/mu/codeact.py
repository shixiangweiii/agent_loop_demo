"""Native code-action（M3.5，进程内 exec，默认关）。

模型写 Python，用 `mu.*` 在**一次 model round-trip** 内用控制流组合多个已注册工具 + 共享变量
状态 → 把 N 轮 tool-call 压成 1 轮（这就是相对 bash 的可测量收益）。

实现：handler 在 worker 线程里 exec 模型代码；`mu.read/write/edit/bash/call` 经
`run_coroutine_threadsafe` 回事件循环调 `ToolRegistry.execute`（过权限策略 + 发内层 ToolCall 事件）。
隔离 ≠ 安全沙箱：code 可 `import os` 绕过（风险等同 bash）；要真隔离把 μ 跑容器。
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from .events import EventEmitter, ToolCallFinished, ToolCallStarted
from .tools import ToolRegistry, ToolResult

_CODE_TIMEOUT = 120.0

_CODE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "code",
        "description": (
            "Run a Python snippet that composes the available tools in ONE step. Inside the code use: "
            "mu.read(path), mu.write(path, content), mu.edit(path, old, new), mu.bash(command), "
            "mu.call(name, args) for any other/extension tool, mu.log(msg), and mu.result(value) to "
            "return a result. Prefer this over many separate tool calls when you need loops/conditions "
            "or to act on multiple files in one go."
        ),
        "parameters": {
            "type": "object",
            "properties": {"code": {"type": "string", "description": "Python source to execute."}},
            "required": ["code"],
        },
    },
}


class _MuApi:
    """注入模型代码的 `mu` 对象：把线程内的同步调用 marshal 回事件循环。"""

    def __init__(self, runner: "CodeAction", loop: asyncio.AbstractEventLoop) -> None:
        self._runner = runner
        self._loop = loop
        self._result: Any = None
        self._has_result = False
        self._logs: list[str] = []
        self._cancelled = False  # 超时后置 True，阻止滞留线程继续经 mu.* 发起工具调用

    def call(self, name: str, args: dict | None = None) -> str:
        if self._cancelled:
            raise RuntimeError("code-action timed out and was cancelled; aborting further tool calls")
        fut = asyncio.run_coroutine_threadsafe(self._runner._proxied(name, args or {}), self._loop)
        return fut.result()

    def read(self, path, offset=0, limit=None):
        a: dict[str, Any] = {"path": path}
        if offset:
            a["offset"] = offset
        if limit is not None:
            a["limit"] = limit
        return self.call("read", a)

    def write(self, path, content):
        return self.call("write", {"path": path, "content": content})

    def edit(self, path, old_string, new_string):
        return self.call("edit", {"path": path, "old_string": old_string, "new_string": new_string})

    def bash(self, command, timeout=120):
        return self.call("bash", {"command": command, "timeout": timeout})

    def log(self, message):
        self._logs.append(str(message))

    def result(self, value):
        self._result = value
        self._has_result = True


class CodeAction:
    def __init__(self, registry: ToolRegistry, emitter: EventEmitter) -> None:
        self._registry = registry
        self._emitter = emitter

    def register(self) -> None:
        # code = 进程内执行任意 Python → code_exec 能力（restrictive 策略会拦掉整个 code 工具）
        self._registry.register("code", _CODE_SCHEMA, self._tool_code, capabilities={"code_exec"})

    async def _tool_code(self, args: dict[str, Any]) -> ToolResult:
        code = args.get("code", "")
        loop = asyncio.get_running_loop()
        mu = _MuApi(self, loop)  # 在 handler 里建，超时时可取消
        try:
            return await asyncio.wait_for(asyncio.to_thread(self._run, code, mu), timeout=_CODE_TIMEOUT)
        except asyncio.TimeoutError:
            mu._cancelled = True  # 后续 mu.* 调用被拒
            return ToolResult(
                "Error: code execution timed out (soft timeout). The worker thread may still be "
                "running; its further mu.* tool calls are now blocked, but direct Python I/O (e.g. "
                "open()) cannot be hard-stopped — run μ in a container or use --sandbox docker for "
                "real isolation."
            )

    def _run(self, code: str, mu: "_MuApi") -> ToolResult:
        g: dict[str, Any] = {"mu": mu, "__name__": "__codeaction__"}
        try:
            exec(compile(code, "<code-action>", "exec"), g)  # noqa: S102 - YOLO，同 bash 风险
        except Exception as e:  # noqa: BLE001 - 错误转字符串供模型自纠错
            logs = "\n".join(mu._logs)
            tail = f"\n[log]\n{logs}" if logs else ""
            return ToolResult(f"Error during code execution: {type(e).__name__}: {e}{tail}")
        parts: list[str] = []
        if mu._has_result:
            parts.append(str(mu._result))
        if mu._logs:
            parts.append("[log]\n" + "\n".join(mu._logs))
        return ToolResult("\n".join(parts) if parts else "(code ran; use mu.result(...) to return a value)")

    async def _proxied(self, name: str, args: dict[str, Any]) -> str:
        """在事件循环上执行内层工具调用：发事件 + 过 registry（含权限策略）。"""
        call_id = f"code:{name}"
        self._emitter.emit(ToolCallStarted(call_id, name, json.dumps(args, ensure_ascii=False)))
        t0 = time.perf_counter()
        res = await self._registry.execute(name, args)
        self._emitter.emit(
            ToolCallFinished(call_id, name, str(res), time.perf_counter() - t0, bool(getattr(res, "terminate", False)))
        )
        return str(res)
