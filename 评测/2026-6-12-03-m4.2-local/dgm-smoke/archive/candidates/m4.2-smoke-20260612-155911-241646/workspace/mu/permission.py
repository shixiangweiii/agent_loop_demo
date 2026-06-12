"""权限策略：基于 **capability** gate 工具调用。默认 allow_all（YOLO / Pi 等价）。

每个工具带能力集（read / write / shell / code_exec / extension_exec），策略按能力判断，
而非按工具名黑名单——这样 code-action（code_exec）、扩展加载（extension_exec）、bash（shell）
都能被 restrictive 策略真正拦住（而不是只拦内置 write/edit/bash 这三个名字）。

policy(name, args, caps) -> None（放行）| str（拒绝原因）。钩在 ToolRegistry.execute。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

PermissionPolicy = Callable[[str, dict, "set[str]"], "str | None"]

# 能力常量
WRITE = "write"
SHELL = "shell"
CODE_EXEC = "code_exec"
EXTENSION_EXEC = "extension_exec"

# read-only 下禁止的能力
_READONLY_BLOCK = {WRITE, SHELL, CODE_EXEC, EXTENSION_EXEC}
# 无法被「限定在 workspace 内」保证的能力（bash/code/扩展都能逃逸路径约束）
_UNCONFINABLE = {SHELL, CODE_EXEC, EXTENSION_EXEC}


def allow_all(name: str, args: dict[str, Any], caps: set[str]) -> str | None:
    return None


def read_only(name: str, args: dict[str, Any], caps: set[str]) -> str | None:
    blocked = caps & _READONLY_BLOCK
    if blocked:
        return f"{name} needs {sorted(blocked)} which is blocked in read-only mode"
    return None


def workspace_write(root: str) -> PermissionPolicy:
    root_path = Path(root).resolve()

    def policy(name: str, args: dict[str, Any], caps: set[str]) -> str | None:
        unconfinable = caps & _UNCONFINABLE
        if unconfinable:
            return f"{name} ({sorted(unconfinable)}) cannot be confined to the workspace; blocked"
        if WRITE in caps:
            p = args.get("path")
            if p is not None:
                try:
                    target = Path(p).resolve()
                except Exception:  # noqa: BLE001
                    return f"invalid path: {p}"
                if target != root_path and root_path not in target.parents:
                    return f"write outside workspace {root_path} is blocked: {p}"
        return None

    return policy


def make_policy(kind: str = "allow", *, root: str | None = None) -> PermissionPolicy:
    if kind == "allow":
        return allow_all
    if kind == "readonly":
        return read_only
    if kind == "workspace":
        return workspace_write(root or os.getcwd())
    raise ValueError(f"unknown permission kind: {kind!r}")
