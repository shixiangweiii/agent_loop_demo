"""Repo-local procedural memory: JSON-defined meta-tools (M4.2).

Meta-tools are persisted code-action snippets loaded explicitly from
`.mu/metatools/*.json` (or `MU_METATOOL_DIR`). They register as normal tools,
go through the same permission gate, and use `mu.*` to call existing tools.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .events import EventEmitter, ToolCallFinished, ToolCallStarted
from .permission import CODE_EXEC
from .tools import ToolRegistry, ToolResult

_META_TOOL_TIMEOUT = 120.0
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


class MetaToolConfigError(ValueError):
    """A meta-tool spec is invalid or conflicts with an existing tool."""


@dataclass(frozen=True)
class MetaToolSpec:
    name: str
    version: str
    description: str
    parameters: dict[str, Any]
    code: str
    path: str | None = None


def default_metatool_dir() -> Path:
    configured = os.environ.get("MU_METATOOL_DIR")
    return Path(configured) if configured else Path.cwd() / ".mu" / "metatools"


def load_metatool_specs(base_dir: str | Path) -> tuple[list[MetaToolSpec], list[str]]:
    """Load and validate all `*.json` meta-tool specs from a directory."""
    root = Path(base_dir)
    if not root.exists():
        return [], []
    if not root.is_dir():
        return [], [f"{root}: not a directory"]
    specs: list[MetaToolSpec] = []
    errors: list[str] = []
    seen: dict[str, str] = {}
    for path in sorted(root.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            spec = _spec_from_json(raw, path)
        except (OSError, json.JSONDecodeError, MetaToolConfigError) as e:
            errors.append(f"{path}: {e}")
            continue
        if spec.name in seen:
            errors.append(f"{path}: duplicate meta-tool name {spec.name!r}; first defined in {seen[spec.name]}")
            continue
        seen[spec.name] = spec.path or str(path)
        specs.append(spec)
    return specs, errors


def _spec_from_json(raw: Any, path: Path) -> MetaToolSpec:
    if not isinstance(raw, dict):
        raise MetaToolConfigError("spec must be a JSON object")
    missing = [k for k in ("name", "version", "description", "parameters", "code") if k not in raw]
    if missing:
        raise MetaToolConfigError(f"missing required field(s): {', '.join(missing)}")
    name = raw["name"]
    version = raw["version"]
    description = raw["description"]
    parameters = raw["parameters"]
    code = raw["code"]
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise MetaToolConfigError("name must match ^[A-Za-z_][A-Za-z0-9_]{0,63}$")
    if not isinstance(version, str) or not version.strip():
        raise MetaToolConfigError("version must be a non-empty string")
    if not isinstance(description, str) or not description.strip():
        raise MetaToolConfigError("description must be a non-empty string")
    if not isinstance(parameters, dict) or parameters.get("type") != "object":
        raise MetaToolConfigError("parameters must be a JSON Schema object")
    if not isinstance(code, str) or not code.strip():
        raise MetaToolConfigError("code must be a non-empty string")
    return MetaToolSpec(
        name=name,
        version=version,
        description=description,
        parameters=parameters,
        code=code,
        path=str(path.resolve()),
    )


class MetaToolManager:
    def __init__(
        self,
        registry: ToolRegistry,
        emitter: EventEmitter,
        *,
        base_dir: str | Path | None = None,
    ) -> None:
        self._registry = registry
        self._emitter = emitter
        self._base_dir = Path(base_dir) if base_dir is not None else default_metatool_dir()
        self._registered: set[str] = set()
        self.specs: dict[str, MetaToolSpec] = {}
        self.errors: list[str] = []
        self._register_management_tools()

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    def load_all(self) -> list[MetaToolSpec]:
        """Reload all specs from disk and register valid ones."""
        for name in list(self._registered):
            self._registry.unregister(name)
        self._registered.clear()
        self.specs.clear()

        specs, errors = load_metatool_specs(self._base_dir)
        self.errors = list(errors)
        loaded: list[MetaToolSpec] = []
        for spec in specs:
            try:
                self.register(spec)
            except MetaToolConfigError as e:
                location = spec.path or spec.name
                self.errors.append(f"{location}: {e}")
            else:
                loaded.append(spec)
        return loaded

    def register(self, spec: MetaToolSpec) -> None:
        """Register one spec as a normal tool with code_exec capability."""
        if spec.name in self._registered:
            self._registry.unregister(spec.name)
            self._registered.remove(spec.name)
            self.specs.pop(spec.name, None)
        elif spec.name in self._registry.names():
            raise MetaToolConfigError(f"tool name conflicts with existing tool: {spec.name}")
        schema = {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": f"{spec.description} (meta-tool v{spec.version})",
                "parameters": spec.parameters,
            },
        }
        self._registry.register(
            spec.name,
            schema,
            self._make_handler(spec),
            capabilities={CODE_EXEC},
        )
        self._registered.add(spec.name)
        self.specs[spec.name] = spec

    def _register_management_tools(self) -> None:
        self._registry.register(
            "list_metatools",
            _LIST_METATOOLS_SCHEMA,
            self._tool_list,
            capabilities={"read"},
        )
        self._registry.register(
            "reload_metatools",
            _RELOAD_METATOOLS_SCHEMA,
            self._tool_reload,
            capabilities={CODE_EXEC},
        )

    async def _tool_list(self, _args: dict[str, Any]) -> str:
        lines: list[str] = []
        if self.specs:
            lines.extend(
                f"{s.name} v{s.version}: {s.description}"
                for s in sorted(self.specs.values(), key=lambda s: s.name)
            )
        else:
            lines.append("No meta-tools loaded.")
        if self.errors:
            lines.append("[errors]")
            lines.extend(self.errors)
        return "\n".join(lines)

    async def _tool_reload(self, _args: dict[str, Any]) -> str:
        loaded = self.load_all()
        parts = [f"Loaded {len(loaded)} meta-tool(s) from {self._base_dir.resolve()}."]
        if loaded:
            parts.append("Tools: " + ", ".join(sorted(s.name for s in loaded)))
        if self.errors:
            parts.append("[errors]\n" + "\n".join(self.errors))
        return "\n".join(parts)

    def _make_handler(self, spec: MetaToolSpec):
        async def handler(args: dict[str, Any]) -> ToolResult:
            return await self._execute(spec, args)

        return handler

    async def _execute(self, spec: MetaToolSpec, args: dict[str, Any]) -> ToolResult:
        loop = asyncio.get_running_loop()
        mu = _MetaToolApi(self, loop, spec.name)
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._run_code, spec, dict(args), mu),
                timeout=_META_TOOL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            mu._cancelled = True
            return ToolResult(
                f"Error: meta-tool {spec.name} timed out (soft timeout). "
                "Further mu.* tool calls from the worker are blocked."
            )

    def _run_code(self, spec: MetaToolSpec, args: dict[str, Any], mu: "_MetaToolApi") -> ToolResult:
        globals_: dict[str, Any] = {"mu": mu, "args": args, "__name__": "__metatool__"}
        try:
            exec(compile(spec.code, f"<metatool:{spec.name}>", "exec"), globals_)  # noqa: S102
        except Exception as e:  # noqa: BLE001 - returned to model for self-correction
            logs = "\n".join(mu._logs)
            tail = f"\n[log]\n{logs}" if logs else ""
            return ToolResult(f"Error during meta-tool {spec.name}: {type(e).__name__}: {e}{tail}")
        parts: list[str] = []
        if mu._has_result:
            parts.append(str(mu._result))
        if mu._logs:
            parts.append("[log]\n" + "\n".join(mu._logs))
        return ToolResult("\n".join(parts) if parts else "(meta-tool ran; use mu.result(...) to return a value)")

    async def _proxied(self, meta_name: str, tool_name: str, args: dict[str, Any]) -> str:
        call_id = f"metatool:{meta_name}:{tool_name}"
        raw_args = json.dumps(args, ensure_ascii=False)
        self._emitter.emit(ToolCallStarted(call_id, tool_name, raw_args))
        t0 = time.perf_counter()
        result = await self._registry.execute(tool_name, args)
        self._emitter.emit(
            ToolCallFinished(
                call_id,
                tool_name,
                str(result),
                time.perf_counter() - t0,
                bool(getattr(result, "terminate", False)),
            )
        )
        return str(result)


class _MetaToolApi:
    def __init__(self, manager: MetaToolManager, loop: asyncio.AbstractEventLoop, meta_name: str) -> None:
        self._manager = manager
        self._loop = loop
        self._meta_name = meta_name
        self._result: Any = None
        self._has_result = False
        self._logs: list[str] = []
        self._cancelled = False

    def call(self, name: str, args: dict | None = None) -> str:
        if self._cancelled:
            raise RuntimeError("meta-tool timed out and was cancelled; aborting further tool calls")
        fut = asyncio.run_coroutine_threadsafe(
            self._manager._proxied(self._meta_name, name, args or {}),
            self._loop,
        )
        return fut.result()

    def read(self, path, offset=0, limit=None):
        args: dict[str, Any] = {"path": path}
        if offset:
            args["offset"] = offset
        if limit is not None:
            args["limit"] = limit
        return self.call("read", args)

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


_LIST_METATOOLS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_metatools",
        "description": "List loaded repo-local meta-tools and any load errors.",
        "parameters": {"type": "object", "properties": {}},
    },
}

_RELOAD_METATOOLS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "reload_metatools",
        "description": "Reload repo-local meta-tools from the configured metatool directory.",
        "parameters": {"type": "object", "properties": {}},
    },
}
