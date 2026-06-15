"""四个工具的单测：tmp 目录、async、无网络、无付费。"""
from __future__ import annotations

import asyncio


async def test_write_then_read(registry, tmp_path):
    p = tmp_path / "a.txt"
    out = await registry.execute("write", {"path": str(p), "content": "hello\nworld"})
    assert "Wrote" in out
    content = await registry.execute("read", {"path": str(p)})
    assert content == "hello\nworld"


async def test_write_creates_parent_dirs(registry, tmp_path):
    p = tmp_path / "sub" / "dir" / "b.txt"
    await registry.execute("write", {"path": str(p), "content": "x"})
    assert p.read_text() == "x"


async def test_read_with_offset_and_limit(registry, tmp_path):
    p = tmp_path / "lines.txt"
    p.write_text("l0\nl1\nl2\nl3\n")
    out = await registry.execute("read", {"path": str(p), "offset": 1, "limit": 2})
    assert out == "l1\nl2\n"


async def test_read_missing_file(registry, tmp_path):
    out = await registry.execute("read", {"path": str(tmp_path / "nope.txt")})
    assert "not found" in out.lower()


async def test_read_empty_file(registry, tmp_path):
    p = tmp_path / "empty.txt"
    p.write_text("")
    out = await registry.execute("read", {"path": str(p)})
    assert "empty" in out.lower()


async def test_edit_unique(registry, tmp_path):
    p = tmp_path / "c.txt"
    p.write_text("foo bar baz")
    out = await registry.execute(
        "edit", {"path": str(p), "old_string": "bar", "new_string": "BAR"}
    )
    assert "1 replacement" in out
    assert p.read_text() == "foo BAR baz"


async def test_edit_not_found_leaves_file_unchanged(registry, tmp_path):
    p = tmp_path / "c.txt"
    p.write_text("foo")
    out = await registry.execute(
        "edit", {"path": str(p), "old_string": "zzz", "new_string": "y"}
    )
    assert "not found" in out.lower()
    assert p.read_text() == "foo"


async def test_edit_not_unique_leaves_file_unchanged(registry, tmp_path):
    p = tmp_path / "c.txt"
    p.write_text("x x x")
    out = await registry.execute(
        "edit", {"path": str(p), "old_string": "x", "new_string": "y"}
    )
    assert "not unique" in out.lower()
    assert p.read_text() == "x x x"


async def test_bash_echo(registry):
    out = await registry.execute("bash", {"command": "echo hello"})
    assert "hello" in out
    assert "exit code: 0" in out


async def test_bash_nonzero_exit(registry):
    out = await registry.execute("bash", {"command": "exit 3"})
    assert "exit code: 3" in out


async def test_bash_stderr_captured(registry):
    out = await registry.execute("bash", {"command": "echo oops 1>&2"})
    assert "stderr" in out.lower()
    assert "oops" in out


async def test_bash_timeout(registry):
    out = await registry.execute("bash", {"command": "sleep 5", "timeout": 0.2})
    assert "timed out" in out.lower()
    assert "124" in out


async def test_unknown_tool(registry):
    out = await registry.execute("nope", {})
    assert "unknown tool" in out.lower()


async def test_missing_required_arg(registry):
    out = await registry.execute("read", {})  # 缺 path
    assert "missing required argument" in out.lower()


def test_schemas_expose_four_tools(registry):
    names = {s["function"]["name"] for s in registry.schemas()}
    assert names == {"read", "write", "edit", "bash"}


async def test_bash_timeout_kills_child_processes(registry, tmp_path):
    """回归：派生后台子进程的命令超时后，子进程应被整组清理（不应留下孤儿）。"""
    marker = tmp_path / "ORPHAN_RAN"
    cmd = f"( sleep 1; touch {marker.as_posix()} ) & wait"
    out = await registry.execute("bash", {"command": cmd, "timeout": 0.3})
    assert "timed out" in out.lower()
    # 给「本应」写 marker 的时刻留足时间；若进程组已被杀，marker 不会出现
    await asyncio.sleep(1.5)
    assert not marker.exists(), "派生子进程在 timeout 后仍执行（进程组未被清理）"
