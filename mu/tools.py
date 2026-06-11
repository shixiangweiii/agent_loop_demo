"""四个工具 + 注册表。

Pi 哲学：工具返回字符串，错误也返回字符串（不抛异常），让模型自纠错。
工具走原生 function-calling（OpenAI tools schema）。
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from .environment import LocalEnvironment

ToolHandler = Callable[[LocalEnvironment, dict[str, Any]], Awaitable[str]]


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


class ToolRegistry:
    """name -> (schema, async handler)。M0 固定四工具；M3 在此基础上加扩展注册。"""

    def __init__(self, env: LocalEnvironment | None = None) -> None:
        self._env = env or LocalEnvironment()
        self._handlers: dict[str, ToolHandler] = dict(_HANDLERS)
        self._schemas: list[dict[str, Any]] = list(_SCHEMAS)

    def schemas(self) -> list[dict[str, Any]]:
        return self._schemas

    def names(self) -> list[str]:
        return list(self._handlers)

    async def execute(self, name: str, args: dict[str, Any]) -> str:
        handler = self._handlers.get(name)
        if handler is None:
            return f"Error: unknown tool '{name}'."
        try:
            return await handler(self._env, args)
        except KeyError as e:
            return f"Error: missing required argument {e} for tool '{name}'."
        except Exception as e:  # noqa: BLE001 - 工具错误转字符串
            return f"Error executing tool '{name}': {e}"
