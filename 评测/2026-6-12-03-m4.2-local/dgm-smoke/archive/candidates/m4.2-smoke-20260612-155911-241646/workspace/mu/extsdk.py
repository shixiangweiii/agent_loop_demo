"""扩展 SDK：扩展作者（含 agent）import 它来声明工具并跑起 JSONL 协议。

最小用法（一个扩展就是一个 python 文件）：

    from mu.extsdk import tool, run_extension

    @tool(name="word_count", description="Count words in text.",
          parameters={"type": "object",
                      "properties": {"text": {"type": "string"}},
                      "required": ["text"]})
    def word_count(args):
        return f"{len(args['text'].split())} words"

    if __name__ == "__main__":
        run_extension(name="textstats", version="0.1")

协议（JSONL，每行一个对象，stdin/stdout）见 `extensions/README.md`：
- 启动即在 stdout 首行输出 manifest。
- core→ext: init / execute / shutdown；ext→core: manifest / result / error / log / state。
工具函数返回 str（或 (str, terminate_bool)）；可用 set_state/get_state 持久化配置、log 输出日志。
"""
from __future__ import annotations

import asyncio
import inspect
import json
import sys
from typing import Any, Callable

_TOOLS: dict[str, dict[str, Any]] = {}
_STATE: dict[str, Any] = {}


def tool(
    name: str,
    description: str,
    parameters: dict[str, Any] | None = None,
    permissions: list[str] | None = None,
) -> Callable:
    schema = {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters or {"type": "object", "properties": {}},
        },
    }

    def deco(fn: Callable) -> Callable:
        _TOOLS[name] = {"schema": schema, "fn": fn, "permissions": permissions or []}
        return fn

    return deco


def get_state() -> dict[str, Any]:
    return _STATE


def set_state(state: dict[str, Any]) -> None:
    """整体替换扩展状态，并持久化到 session（core 收到后写入 session）。"""
    global _STATE
    _STATE = dict(state)
    _emit({"type": "state", "state": _STATE})


def log(message: str, level: str = "info") -> None:
    _emit({"type": "log", "level": level, "message": str(message)})


def _emit(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _manifest(name: str, version: str) -> dict[str, Any]:
    return {
        "type": "manifest",
        "name": name,
        "version": version,
        "tools": [t["schema"] for t in _TOOLS.values()],
        "permissions": sorted({p for t in _TOOLS.values() for p in t["permissions"]}),
    }


def _handle(req: dict[str, Any]) -> None:
    global _STATE
    t = req.get("type")
    if t == "init":
        _STATE = dict(req.get("state") or {})
        return
    if t == "execute":
        rid = req.get("id")
        entry = _TOOLS.get(req.get("tool"))
        if entry is None:
            _emit({"type": "error", "id": rid, "message": f"unknown tool {req.get('tool')!r}"})
            return
        try:
            result = entry["fn"](req.get("args") or {})
            if inspect.isawaitable(result):
                result = asyncio.run(result)
        except Exception as e:  # noqa: BLE001 - 错误回传给 core
            _emit({"type": "error", "id": rid, "message": f"{type(e).__name__}: {e}"})
            return
        content, terminate = result, False
        if isinstance(result, tuple) and len(result) == 2:
            content, terminate = result
        _emit({"type": "result", "id": rid, "content": str(content), "terminate": bool(terminate)})


def run_extension(name: str, version: str = "0.1") -> None:
    if "--manifest" in sys.argv[1:]:
        _emit(_manifest(name, version))
        return
    _emit(_manifest(name, version))  # 首行 manifest
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        if req.get("type") == "shutdown":
            break
        _handle(req)
