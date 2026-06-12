"""权限策略：ToolRegistry.execute 单一钩点 gate 工具调用。"""
from __future__ import annotations

import pytest

from mu.permission import allow_all, make_policy, read_only, workspace_write
from mu.tools import ToolRegistry


async def test_allow_all_is_default(tmp_path):
    reg = ToolRegistry()  # 默认 allow_all
    out = await reg.execute("write", {"path": str(tmp_path / "a.txt"), "content": "x"})
    assert "Wrote" in out


async def test_read_only_blocks_mutations(tmp_path):
    reg = ToolRegistry(policy=read_only)
    p = tmp_path / "a.txt"
    p.write_text("hi")
    assert (await reg.execute("read", {"path": str(p)})) == "hi"  # read 放行
    assert "permission denied" in await reg.execute("write", {"path": str(p), "content": "y"})
    assert "permission denied" in await reg.execute(
        "edit", {"path": str(p), "old_string": "hi", "new_string": "yo"}
    )
    assert "permission denied" in await reg.execute("bash", {"command": "echo hi"})
    assert p.read_text() == "hi"  # 未被改动


async def test_workspace_write_blocks_outside(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    reg = ToolRegistry(policy=workspace_write(str(root)))
    assert "Wrote" in await reg.execute("write", {"path": str(root / "in.txt"), "content": "x"})
    outside = tmp_path / "out.txt"
    assert "permission denied" in await reg.execute("write", {"path": str(outside), "content": "x"})
    assert not outside.exists()


def test_make_policy():
    assert make_policy("allow") is allow_all
    assert make_policy("readonly") is read_only
    assert callable(make_policy("workspace", root="/tmp"))
    with pytest.raises(ValueError):
        make_policy("nope")


def _cap_schema(name):
    return {"type": "function", "function": {"name": name, "parameters": {"type": "object", "properties": {}}}}


async def _h(args):
    return "ran"


async def test_readonly_blocks_by_capability():
    """P1-a 回归：readonly 按能力拦 code_exec / extension_exec（不再是工具名黑名单）。"""
    reg = ToolRegistry(policy=read_only)
    reg.register("mycode", _cap_schema("mycode"), _h, capabilities={"code_exec"})
    reg.register("myext", _cap_schema("myext"), _h, capabilities={"extension_exec"})
    reg.register("myread", _cap_schema("myread"), _h, capabilities={"read"})
    assert "permission denied" in await reg.execute("mycode", {})
    assert "permission denied" in await reg.execute("myext", {})
    assert (await reg.execute("myread", {})) == "ran"


async def test_workspace_blocks_unconfinable(tmp_path):
    """P1-a 回归：workspace 拦无法限定在 workspace 内的能力（shell/code/extension）。"""
    reg = ToolRegistry(policy=workspace_write(str(tmp_path)))
    assert "permission denied" in await reg.execute("bash", {"command": "echo hi"})


def test_dynamic_tools_default_conservative_caps():
    reg = ToolRegistry()
    reg.register("ext_tool", _cap_schema("ext_tool"), _h)  # 不传 caps → 保守 {write, shell}
    assert reg.capabilities("ext_tool") == {"write", "shell"}


def test_permits():
    reg = ToolRegistry(policy=read_only)
    reg.register("c", _cap_schema("c"), _h, capabilities={"code_exec"})
    assert reg.permits("read") is True
    assert reg.permits("write") is False
    assert reg.permits("c") is False


async def test_autoload_skipped_under_readonly(tmp_path):
    """P1-a 回归：restrictive 策略下跳过 autoload（加载扩展=执行任意 Python）。"""
    from mu.agent import Agent
    from mu.model import ModelResult
    from mu.session import Session

    extdir = tmp_path / "ext"
    extdir.mkdir()
    (extdir / "e.py").write_text(
        "from mu.extsdk import tool, run_extension\n"
        "@tool(name='ext_wc', description='x', parameters={'type':'object','properties':{}})\n"
        "def f(args):\n    return 'wc'\n"
        "if __name__ == '__main__':\n    run_extension('e', '0.1')\n"
    )

    class _Msg:
        content = "ok"
        tool_calls = None

    class _M:
        async def acomplete(self, m, t, *, stream=False, on_delta=None):
            return ModelResult(message=_Msg())

    agent = Agent(model=_M(), session=Session(base_dir=tmp_path),
                  extensions=True, ext_dir=extdir, policy=read_only)
    try:
        await agent.run("hi")
        assert "ext_wc" not in agent.tools.names()  # autoload 被 readonly 跳过
    finally:
        await agent.aclose()
