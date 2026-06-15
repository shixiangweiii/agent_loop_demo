"""四个工具 + 注册表。

Pi 哲学：工具返回字符串，错误也返回字符串（不抛异常），让模型自纠错。
工具走原生 function-calling（OpenAI tools schema）。
"""
from __future__ import annotations

import functools
from typing import Any, Awaitable, Callable

from .environment import LocalEnvironment
from .permission import PermissionPolicy, allow_all

ToolHandler = Callable[[LocalEnvironment, dict[str, Any]], Awaitable[str]]
# 统一后的处理器签名（内置工具用 functools.partial 绑定 env；扩展工具路由到子进程）
RegisteredHandler = Callable[[dict[str, Any]], Awaitable[Any]]


class ToolResult(str):
    """工具结果：本身是字符串（向后兼容 M0），附带 terminate 标志。

    terminate=True 提示 loop 跳过本次工具批之后的自动 LLM 调用（Pi 的 terminate 语义）。
    内置四工具永不 terminate；该 seam 供 M3 扩展使用。
    """

    terminate: bool

    def __new__(cls, content: str, terminate: bool = False) -> "ToolResult":
        obj = super().__new__(cls, content)
        obj.terminate = terminate
        return obj

    @property
    def content(self) -> str:
        return str(self)


# ---- 工具实现 ----

async def _read(env: LocalEnvironment, args: dict[str, Any]) -> str:
    path = args["path"]
    offset = int(args.get("offset", 0) or 0)
    limit = args.get("limit")
    limit = int(limit) if limit is not None else None
    try:
        content = await env.read_file(path, offset=offset, limit=limit)
    except FileNotFoundError:
        return f"Error: file not found: {path}"
    except IsADirectoryError:
        return f"Error: path is a directory, not a file: {path}"
    except Exception as e:  # noqa: BLE001 - 工具错误转字符串
        return f"Error reading {path}: {e}"
    if content == "":
        return f"(file {path} is empty)"
    return content


async def _write(env: LocalEnvironment, args: dict[str, Any]) -> str:
    path = args["path"]
    content = args["content"]
    try:
        await env.write_file(path, content)
    except Exception as e:  # noqa: BLE001
        return f"Error writing {path}: {e}"
    n_lines = content.count("\n") + 1 if content else 0
    return f"Wrote {len(content)} chars ({n_lines} lines) to {path}"


async def _edit(env: LocalEnvironment, args: dict[str, Any]) -> str:
    path = args["path"]
    old = args["old_string"]
    new = args["new_string"]
    try:
        content = await env.read_file(path)
    except FileNotFoundError:
        return f"Error: file not found: {path}"
    except Exception as e:  # noqa: BLE001
        return f"Error reading {path}: {e}"
    count = content.count(old)
    if count == 0:
        return f"Error: old_string not found in {path}. No changes made."
    if count > 1:
        return (
            f"Error: old_string is not unique in {path} (found {count} matches). "
            "Add more surrounding context to make it unique."
        )
    updated = content.replace(old, new, 1)
    try:
        await env.write_file(path, updated)
    except Exception as e:  # noqa: BLE001
        return f"Error writing {path}: {e}"
    return f"Edited {path} (1 replacement)"


async def _bash(env: LocalEnvironment, args: dict[str, Any]) -> str:
    command = args["command"]
    timeout = float(args.get("timeout", 120.0) or 120.0)
    result = await env.run_bash(command, timeout=timeout)
    parts: list[str] = []
    if result.stdout:
        parts.append(result.stdout.rstrip("\n"))
    if result.stderr:
        parts.append("[stderr]\n" + result.stderr.rstrip("\n"))
    parts.append(f"[exit code: {result.exit_code}]")
    return "\n".join(parts)


# ---- JSON schema（OpenAI tools 格式）----

_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "Read a text file's contents. Use absolute paths.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute path to the file."},
                    "offset": {"type": "integer", "description": "0-based start line (optional)."},
                    "limit": {"type": "integer", "description": "Max number of lines (optional)."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write",
            "description": "Create or overwrite a file with the given content. Parent dirs are auto-created.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute path to the file."},
                    "content": {"type": "string", "description": "Full file content."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit",
            "description": "Replace an exact, unique occurrence of old_string with new_string in a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute path to the file."},
                    "old_string": {"type": "string", "description": "Exact text to replace (must be unique in the file)."},
                    "new_string": {"type": "string", "description": "Replacement text."},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command and return its stdout, stderr and exit code. Stateless per call.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The shell command to run."},
                    "timeout": {"type": "number", "description": "Timeout in seconds (default 120)."},
                },
                "required": ["command"],
            },
        },
    },
]

_HANDLERS: dict[str, ToolHandler] = {
    "read": _read,
    "write": _write,
    "edit": _edit,
    "bash": _bash,
}

# 内置工具的能力（permission 策略按能力 gate，而非按工具名）
_CAPABILITIES: dict[str, set[str]] = {
    "read": {"read"},
    "write": {"write"},
    "edit": {"write"},
    "bash": {"shell"},
}


class ToolRegistry:
    """name -> (schema, async handler)。内置四工具固定；M3 起可动态 register/unregister 扩展工具。

    handler 统一签名 `(args) -> ToolResult|str`：内置工具用 functools.partial 绑定 env，
    扩展工具路由到其子进程。execute 对外行为与 M0 一致。
    """

    def __init__(
        self,
        env: LocalEnvironment | None = None,
        policy: PermissionPolicy | None = None,
    ) -> None:
        self._env = env or LocalEnvironment()
        self._policy: PermissionPolicy = policy or allow_all
        self._handlers: dict[str, RegisteredHandler] = {
            name: functools.partial(h, self._env) for name, h in _HANDLERS.items()
        }
        self._schemas: list[dict[str, Any]] = list(_SCHEMAS)
        self._builtins = set(_HANDLERS)
        self._caps: dict[str, set[str]] = {n: set(c) for n, c in _CAPABILITIES.items()}

    def schemas(self) -> list[dict[str, Any]]:
        return self._schemas

    def names(self) -> list[str]:
        return list(self._handlers)

    def capabilities(self, name: str) -> set[str]:
        return self._caps.get(name, set())

    def permits(self, name: str, args: dict[str, Any] | None = None) -> bool:
        """该工具在当前策略下是否被允许（不执行，仅判断）。"""
        return self._policy(name, args or {}, self._caps.get(name, set())) is None

    def register(
        self,
        name: str,
        schema: dict[str, Any],
        handler: RegisteredHandler,
        capabilities: set[str] | None = None,
    ) -> None:
        """注册一个动态工具（扩展工具 / 管理工具 / code）。重名直接拒绝。

        capabilities 不传时**保守默认** {write, shell}（restrictive 策略默认拦），
        因为扩展工具的副作用未知，应宁可拦错也不放过。
        """
        if name in self._handlers:
            raise ValueError(f"tool name already registered: {name!r}")
        self._handlers[name] = handler
        self._schemas.append(schema)
        self._caps[name] = set(capabilities) if capabilities is not None else {"write", "shell"}

    def unregister(self, name: str) -> None:
        """注销动态工具（内置四工具受保护，不可注销）。"""
        if name in self._builtins or name not in self._handlers:
            return
        del self._handlers[name]
        self._caps.pop(name, None)
        self._schemas = [
            s for s in self._schemas if s.get("function", {}).get("name") != name
        ]

    async def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        handler = self._handlers.get(name)
        if handler is None:
            return ToolResult(f"Error: unknown tool '{name}'.")
        reason = self._policy(name, args, self._caps.get(name, set()))  # 按能力 gate
        if reason:
            return ToolResult(f"Error: permission denied: {reason}")
        try:
            result = await handler(args)
        except KeyError as e:
            return ToolResult(f"Error: missing required argument {e} for tool '{name}'.")
        except Exception as e:  # noqa: BLE001 - 工具错误转字符串
            return ToolResult(f"Error executing tool '{name}': {e}")
        if isinstance(result, ToolResult):
            return result
        return ToolResult(result)
