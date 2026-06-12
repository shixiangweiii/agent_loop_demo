"""Tree session：消息以树存储（id/parent_id），JSONL 持久化。

- append-only：每条消息追加一行 JSONL（KV-cache/可复现友好）。
- 分支：从任意节点 branch_from 后继续 append 即 fork。
- 当前分支的线性历史 = 从 head 沿 parent_id 回溯到 root（path_to_head）。
- branch summary：在主线 head 追加 {type:"branch_summary"} 自定义消息，
  由 context.convert_to_llm 注入回 LLM 上下文。
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def default_session_dir() -> Path:
    """会话目录：MU_SESSION_DIR 覆盖，否则工作目录本地 ./.mu/sessions。"""
    env = os.environ.get("MU_SESSION_DIR")
    return Path(env) if env else Path.cwd() / ".mu" / "sessions"


@dataclass
class Node:
    id: str
    parent_id: str | None
    ts: float
    msg: dict[str, Any]


class Session:
    def __init__(self, session_id: str | None = None, base_dir: str | Path | None = None) -> None:
        self.id = session_id or _new_id()
        self.dir = Path(base_dir) if base_dir is not None else default_session_dir()
        self.path = self.dir / f"{self.id}.jsonl"
        self.nodes: dict[str, Node] = {}
        self.children: dict[str, list[str]] = {}
        self.head: str | None = None

    # ---- 写入 ----

    def append(self, msg: dict[str, Any]) -> str:
        node = Node(_new_id(), self.head, time.time(), msg)
        self._index(node)
        self.head = node.id
        self._persist(node)
        return node.id

    def add_branch_summary(self, content: str) -> str:
        """在当前 head（通常是主线）追加侧分支摘要消息。"""
        return self.append({"type": "branch_summary", "content": content})

    def _index(self, node: Node) -> None:
        self.nodes[node.id] = node
        if node.parent_id is not None:
            self.children.setdefault(node.parent_id, []).append(node.id)

    def _persist(self, node: Node) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {"id": node.id, "parent_id": node.parent_id, "ts": node.ts, "msg": node.msg},
            ensure_ascii=False,
        )
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    # ---- 读取 / 导航 ----

    def path_to(self, node_id: str | None) -> list[dict[str, Any]]:
        """从 root 到 node_id 的线性消息路径（node_id=None 返回空）。"""
        msgs: list[dict[str, Any]] = []
        cur = node_id
        while cur is not None:
            node = self.nodes[cur]
            msgs.append(node.msg)
            cur = node.parent_id
        msgs.reverse()
        return msgs

    def path_to_head(self) -> list[dict[str, Any]]:
        return self.path_to(self.head)

    def branch_from(self, node_id: str) -> None:
        if node_id not in self.nodes:
            raise KeyError(f"unknown node id: {node_id}")
        self.head = node_id

    def leaves(self) -> list[str]:
        return [nid for nid in self.nodes if nid not in self.children]

    @classmethod
    def load(cls, session_id: str, base_dir: str | Path | None = None) -> "Session":
        s = cls(session_id, base_dir)
        if not s.path.exists():
            raise FileNotFoundError(s.path)
        last_id: str | None = None
        with s.path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                node = Node(d["id"], d["parent_id"], d["ts"], d["msg"])
                s._index(node)
                last_id = node.id
        s.head = last_id  # 默认从最后追加的叶子续跑
        return s
