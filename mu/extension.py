"""ExtensionManager：加载/调用/重载/卸载扩展子进程，并把工具注册进 ToolRegistry。

每个扩展 = 一个长驻 `python ext.py` 子进程，JSONL stdin/stdout 通信（见 extensions/README.md）。
- load：spawn → 读首行 manifest → 注册工具 → 起 reader task → 发 init（含 session 恢复的 state）。
- call：发 execute → await 对应 id 的结果 → ToolResult。
- reader task：result/error→resolve future；log→emit 事件；state→持久化进 session。
- 进程组隔离（start_new_session + killpg），复用 environment.run_bash 的清理思路。
隔离 ≠ 安全沙箱：扩展以 agent 同等权限运行（YOLO）；权限/沙箱见 M3.5。
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .events import (
    EventEmitter,
    ExtensionError,
    ExtensionLoaded,
    ExtensionLog,
    ExtensionUnloaded,
)
from .session import Session
from .tools import ToolRegistry, ToolResult

_MANIFEST_TIMEOUT = 10.0
_CALL_TIMEOUT = 120.0


def default_ext_dir() -> Path:
    env = os.environ.get("MU_EXT_DIR")
    return Path(env) if env else Path.cwd() / ".mu" / "extensions"


@dataclass
class Extension:
    name: str
    version: str
    path: str
    process: asyncio.subprocess.Process
    tool_names: list[str] = field(default_factory=list)
    reader_task: asyncio.Task | None = None
    pending: dict[int, asyncio.Future] = field(default_factory=dict)  # 每扩展独立，避免跨扩展误处理


_LOAD_SCHEMA = {
    "type": "function",
    "function": {
        "name": "load_extension",
        "description": "Load a Python tool extension from a file path; its tools become available immediately.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "path to the extension .py file"}},
            "required": ["path"],
        },
    },
}
_RELOAD_SCHEMA = {
    "type": "function",
    "function": {
        "name": "reload_extension",
        "description": "Reload an already-loaded extension by name (after editing its file).",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
}
_LIST_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_extensions",
        "description": "List currently loaded extensions and their tools.",
        "parameters": {"type": "object", "properties": {}},
    },
}


class ExtensionManager:
    def __init__(
        self,
        registry: ToolRegistry,
        session: Session,
        emitter: EventEmitter,
        *,
        python: str | None = None,
        ext_dir: str | Path | None = None,
    ) -> None:
        self._registry = registry
        self._session = session
        self._emitter = emitter
        self._python = python or sys.executable
        self._ext_dir = Path(ext_dir) if ext_dir is not None else default_ext_dir()
        self._exts: dict[str, Extension] = {}
        self._next_id = 0
        self._autoloaded = False
        self._register_management_tools()

    # ---- 管理工具（注册进 registry，让 agent 能自延伸）----

    def _register_management_tools(self) -> None:
        # 加载/重载扩展 = 执行任意 Python → extension_exec 能力（restrictive 策略会拦）
        self._registry.register("load_extension", _LOAD_SCHEMA, self._tool_load, capabilities={"extension_exec"})
        self._registry.register("reload_extension", _RELOAD_SCHEMA, self._tool_reload, capabilities={"extension_exec"})
        self._registry.register("list_extensions", _LIST_SCHEMA, self._tool_list, capabilities={"read"})

    async def _tool_load(self, args: dict[str, Any]) -> str:
        ext = await self.load(args["path"])
        if isinstance(ext, str):
            return ext
        return f"Loaded extension {ext.name} v{ext.version}; new tools: {', '.join(ext.tool_names)}"

    async def _tool_reload(self, args: dict[str, Any]) -> str:
        return await self.reload(args["name"])

    async def _tool_list(self, args: dict[str, Any]) -> str:
        if not self._exts:
            return "No extensions loaded."
        return "\n".join(
            f"{e.name} v{e.version}: {', '.join(e.tool_names)}" for e in self._exts.values()
        )

    # ---- 生命周期 ----

    async def load(self, path: str) -> Extension | str:
        p = Path(path)
        if not p.exists():
            msg = f"Error: extension file not found: {path}"
            self._emitter.emit(ExtensionError("?", msg))
            return msg

        proc = await asyncio.create_subprocess_exec(
            self._python, str(p),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        try:
            manifest = await asyncio.wait_for(self._read_json(proc.stdout), timeout=_MANIFEST_TIMEOUT)
        except asyncio.TimeoutError:
            manifest = None
        if not manifest or manifest.get("type") != "manifest":
            self._kill(proc)
            err = ""
            if proc.stderr is not None:
                try:
                    err = (await asyncio.wait_for(proc.stderr.read(), timeout=2)).decode("utf-8", "replace")
                except asyncio.TimeoutError:
                    pass
            await self._discard_proc(proc)
            msg = f"Error: extension {path} did not produce a valid manifest." + (f"\n{err.strip()}" if err.strip() else "")
            self._emitter.emit(ExtensionError(Path(path).stem, msg))
            return msg

        name = manifest.get("name") or p.stem
        version = manifest.get("version", "?")
        if name in self._exts:
            await self._discard_proc(proc)
            msg = f"Error: extension {name!r} is already loaded; use reload_extension to reload it."
            self._emitter.emit(ExtensionError(name, msg))
            return msg
        registered: list[str] = []
        for schema in manifest.get("tools", []):
            tname = schema.get("function", {}).get("name")
            try:
                self._registry.register(tname, schema, self._make_handler(name, tname))
                registered.append(tname)
            except ValueError:
                for r in registered:
                    self._registry.unregister(r)
                await self._discard_proc(proc)
                msg = f"Error: tool {tname!r} from extension {name} conflicts with an existing tool name."
                self._emitter.emit(ExtensionError(name, msg))
                return msg

        ext = Extension(name, version, str(p), proc, registered)
        self._exts[name] = ext
        ext.reader_task = asyncio.create_task(self._reader_loop(ext))
        await self._send(ext, {"type": "init", "state": self._restore_state(name)})
        self._emitter.emit(ExtensionLoaded(name, version, registered))
        return ext

    async def reload(self, name: str) -> str:
        ext = self._exts.get(name)
        if ext is None:
            return f"Extension {name!r} is not loaded."
        path = ext.path
        await self.unload(name)
        res = await self.load(path)
        return res if isinstance(res, str) else f"Reloaded {name}."

    async def unload(self, name: str) -> str:
        ext = self._exts.pop(name, None)
        if ext is None:
            return f"Extension {name!r} is not loaded."
        for tname in ext.tool_names:
            self._registry.unregister(tname)
        proc = ext.process
        try:
            if proc.returncode is None:
                await self._send(ext, {"type": "shutdown"})
                try:
                    await asyncio.wait_for(proc.wait(), timeout=3)
                except asyncio.TimeoutError:
                    self._kill(proc)
                    await proc.wait()
        except Exception:  # noqa: BLE001
            self._kill(proc)
            try:
                await proc.wait()
            except Exception:  # noqa: BLE001
                pass
        # 在事件循环内显式关闭 stdin，避免循环关闭后 transport __del__ 的资源告警
        if proc.stdin is not None:
            try:
                proc.stdin.close()
            except Exception:  # noqa: BLE001
                pass
        if ext.reader_task is not None:
            ext.reader_task.cancel()
            try:
                await ext.reader_task
            except asyncio.CancelledError:
                pass
        self._emitter.emit(ExtensionUnloaded(name))
        return f"Unloaded {name}."

    async def autoload(self) -> None:
        """启动时自动加载 ext_dir 下的所有扩展（只做一次）。"""
        if self._autoloaded:
            return
        self._autoloaded = True
        if not self._ext_dir.exists():
            return
        for p in sorted(self._ext_dir.glob("*.py")):
            await self.load(str(p))

    async def aclose(self) -> None:
        for name in list(self._exts):
            await self.unload(name)

    # ---- 调用 ----

    async def call(self, ext_name: str, tool: str, args: dict[str, Any]) -> ToolResult:
        ext = self._exts.get(ext_name)
        if ext is None or ext.process.returncode is not None:
            return ToolResult(f"Error: extension {ext_name!r} is not running.")
        rid = self._next_id
        self._next_id += 1
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        ext.pending[rid] = fut  # 先置入再发送，确保崩溃时 reader 能解挂
        await self._send(ext, {"type": "execute", "id": rid, "tool": tool, "args": args})
        try:
            content, terminate = await asyncio.wait_for(fut, timeout=_CALL_TIMEOUT)
        except asyncio.TimeoutError:
            ext.pending.pop(rid, None)
            return ToolResult(f"Error: extension {ext_name} tool {tool} timed out.")
        return ToolResult(content, terminate=terminate)

    def _make_handler(self, ext_name: str, tool_name: str):
        async def handler(args: dict[str, Any]) -> ToolResult:
            return await self.call(ext_name, tool_name, args)

        return handler

    # ---- IPC 内部 ----

    async def _reader_loop(self, ext: Extension) -> None:
        stream = ext.process.stdout
        while stream is not None:
            obj = await self._read_json(stream)
            if obj is None:
                break
            t = obj.get("type")
            if t == "result":
                fut = ext.pending.pop(obj.get("id"), None)
                if fut is not None and not fut.done():
                    fut.set_result((obj.get("content", ""), bool(obj.get("terminate", False))))
            elif t == "error":
                rid = obj.get("id")
                fut = ext.pending.pop(rid, None) if rid is not None else None
                if fut is not None and not fut.done():
                    fut.set_result((f"Error: {obj.get('message', '')}", False))
                self._emitter.emit(ExtensionError(ext.name, obj.get("message", "")))
            elif t == "log":
                self._emitter.emit(ExtensionLog(ext.name, obj.get("level", "info"), obj.get("message", "")))
            elif t == "state":
                self._session.append(
                    {"type": "ext_state", "ext": ext.name, "state": obj.get("state") or {}}
                )
        # 进程退出（崩溃/自退；unload 取消 reader 时停在 readline await，不会走到这里）：统一降级
        self._degrade_on_exit(ext)

    def _degrade_on_exit(self, ext: Extension) -> None:
        code = ext.process.returncode
        # 1) 解挂所有 pending（当前/排队调用立即返回错误，不等 _CALL_TIMEOUT）
        for fut in list(ext.pending.values()):
            if not fut.done():
                fut.set_result((f"Error: extension {ext.name} exited (code {code}) during call.", False))
        ext.pending.clear()
        # 2) 未被 unload 处理过 → 注销其工具、从 _exts 移除，异常退出再报错
        if self._exts.get(ext.name) is ext:
            for tname in ext.tool_names:
                self._registry.unregister(tname)
            self._exts.pop(ext.name, None)
            if code not in (0, None):
                self._emitter.emit(
                    ExtensionError(ext.name, f"extension process exited with code {code}; its tools were removed")
                )

    async def _read_json(self, stream: asyncio.StreamReader) -> dict[str, Any] | None:
        line = await stream.readline()
        if not line:
            return None
        try:
            return json.loads(line.decode("utf-8"))
        except json.JSONDecodeError:
            return {"type": "_invalid"}  # 忽略协议外的 stdout 噪声

    async def _send(self, ext: Extension, obj: dict[str, Any]) -> None:
        if ext.process.stdin is None:
            return
        ext.process.stdin.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        await ext.process.stdin.drain()

    def _restore_state(self, name: str) -> dict[str, Any]:
        state: dict[str, Any] = {}
        for m in self._session.path_to_head():
            if m.get("type") == "ext_state" and m.get("ext") == name:
                state = m.get("state") or {}
        return state

    async def _discard_proc(self, proc: asyncio.subprocess.Process) -> None:
        """错误路径：杀进程 + 回收 + 关闭 stdin，避免循环关闭后的 transport 告警。"""
        self._kill(proc)
        try:
            await proc.wait()
        except Exception:  # noqa: BLE001
            pass
        if proc.stdin is not None:
            try:
                proc.stdin.close()
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _kill(proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is not None:
            return
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                proc.kill()
            except ProcessLookupError:
                pass
