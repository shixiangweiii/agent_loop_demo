"""系统提示：<1000 token，Pi 风格——信任前沿模型已懂 coding agent，不堆冗长指令。"""
from __future__ import annotations

import os
from pathlib import Path

SYSTEM_PROMPT = """You are an expert coding assistant. You help with coding tasks by reading files, running commands, editing code, and writing new files.

You have four tools:
- read: read a file's contents
- write: create or overwrite a file
- edit: make an exact, unique string replacement in a file
- bash: run a shell command (ls, grep, find, running tests, etc.)

Guidelines:
- Use absolute paths for the file tools.
- Read a file before editing it; edit requires old_string to match exactly and uniquely.
- Use bash to explore the filesystem and to run/verify your work (e.g. run the tests).
- Work step by step. When the task is fully done, reply with a short final message and NO tool call.
- Be concise.

Self-extension: if you need a capability you don't have, you can write your own Python tool extension and load it with load_extension — its tools become available immediately. See extensions/README.md for the format.
"""

CODE_ACTION_HINT = (
    "\n\nYou also have a `code` tool: write a Python snippet using mu.read/write/edit/bash/call to "
    "combine several tool calls with control flow in ONE step. Prefer it over many separate tool "
    "calls when looping over files or doing multi-step work; use mu.result(value) to return."
)

METATOOL_HINT = (
    "\n\nYou may also have repo-local meta-tools: verified reusable workflows compiled from prior "
    "tool use. Prefer them for matching high-frequency tasks; use list_metatools if unsure."
)


def load_prompt_snippets(base_dir: str | Path | None = None) -> str:
    """Load optional prompt snippets without editing SYSTEM_PROMPT.

    DGM-lite candidates can add `.mu/prompts/*.md|*.txt` snippets in an isolated
    candidate workspace. The eval runner points MU_PROMPT_SNIPPET_DIR at that
    directory, so snippets are opt-in and never require changing this module.
    """
    if base_dir is None:
        configured = os.environ.get("MU_PROMPT_SNIPPET_DIR")
        directory = Path(configured) if configured else Path.cwd() / ".mu" / "prompts"
    else:
        directory = Path(base_dir)
    if not directory.exists() or not directory.is_dir():
        return ""
    parts: list[str] = []
    for p in sorted([*directory.glob("*.md"), *directory.glob("*.txt")]):
        try:
            text = p.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            parts.append(f"\n\n[Prompt snippet: {p.name}]\n{text}")
    return "".join(parts)


def build_system_prompt(*, code_action: bool = False, metatools: bool = False) -> str:
    """Assemble the runtime system prompt from the base prompt + optional snippets."""
    return (
        SYSTEM_PROMPT
        + load_prompt_snippets()
        + (CODE_ACTION_HINT if code_action else "")
        + (METATOOL_HINT if metatools else "")
    )
