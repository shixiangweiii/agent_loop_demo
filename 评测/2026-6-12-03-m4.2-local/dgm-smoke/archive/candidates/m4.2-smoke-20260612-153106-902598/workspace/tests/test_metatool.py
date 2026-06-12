from __future__ import annotations

import json
from pathlib import Path

from mu.agent import Agent
from mu.events import EventEmitter, ToolCallStarted
from mu.metatool import MetaToolManager, load_metatool_specs
from mu.model import ModelResult
from mu.permission import read_only
from mu.prompts import build_system_prompt
from mu.session import Session
from mu.tools import ToolRegistry


class _FF:
    def __init__(self, n, a): self.name = n; self.arguments = a
class _FTC:
    def __init__(self, i, n, a): self.id = i; self.function = _FF(n, a)
class _FM:
    def __init__(self, content=None, tool_calls=None): self.content = content; self.tool_calls = tool_calls
class FakeModel:
    def __init__(self, scripted): self._scr = scripted; self.i = 0; self.seen_tools = []
    async def acomplete(self, messages, tools, *, stream=False, on_delta=None):
        self.seen_tools.append([t["function"]["name"] for t in tools])
        m = self._scr[self.i]; self.i += 1
        return ModelResult(message=m)


def _write_spec(root: Path, name: str, code: str, parameters: dict | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.json"
    path.write_text(
        json.dumps(
            {
                "name": name,
                "version": "0.1",
                "description": f"{name} description",
                "parameters": parameters or {"type": "object", "properties": {}},
                "code": code,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def test_load_metatool_specs_accepts_valid_and_reports_invalid(tmp_path):
    meta_dir = tmp_path / "metatools"
    _write_spec(meta_dir, "read_count", "mu.result('ok')")
    (meta_dir / "bad-name.json").write_text(
        json.dumps(
            {
                "name": "bad-name",
                "version": "0.1",
                "description": "bad",
                "parameters": {"type": "object"},
                "code": "mu.result('x')",
            }
        ),
        encoding="utf-8",
    )
    (meta_dir / "missing_code.json").write_text(
        json.dumps(
            {
                "name": "missing_code",
                "version": "0.1",
                "description": "bad",
                "parameters": {"type": "object"},
            }
        ),
        encoding="utf-8",
    )
    (meta_dir / "bad_params.json").write_text(
        json.dumps(
            {
                "name": "bad_params",
                "version": "0.1",
                "description": "bad",
                "parameters": {"type": "string"},
                "code": "mu.result('x')",
            }
        ),
        encoding="utf-8",
    )

    specs, errors = load_metatool_specs(meta_dir)

    assert [s.name for s in specs] == ["read_count"]
    assert len(errors) == 3
    assert any("name must match" in e for e in errors)
    assert any("missing required field" in e for e in errors)
    assert any("parameters must be a JSON Schema object" in e for e in errors)


def test_load_metatool_specs_reports_duplicate_names(tmp_path):
    meta_dir = tmp_path / "metatools"
    _write_spec(meta_dir, "quick_pytest", "mu.result('first')")
    duplicate = meta_dir / "duplicate.json"
    duplicate.write_text(
        json.dumps(
            {
                "name": "quick_pytest",
                "version": "0.2",
                "description": "duplicate",
                "parameters": {"type": "object"},
                "code": "mu.result('second')",
            }
        ),
        encoding="utf-8",
    )

    specs, errors = load_metatool_specs(meta_dir)

    assert [s.name for s in specs] == ["quick_pytest"]
    assert any("duplicate meta-tool name" in e for e in errors)


def test_agent_loads_metatools_only_when_enabled(tmp_path):
    meta_dir = tmp_path / "metatools"
    _write_spec(meta_dir, "answer", "mu.result('42')")

    disabled = Agent(model=FakeModel([]), session=Session(base_dir=tmp_path), extensions=False)
    assert "answer" not in disabled.tools.names()
    assert "list_metatools" not in disabled.tools.names()

    enabled = Agent(
        model=FakeModel([]),
        session=Session(base_dir=tmp_path / "sessions"),
        extensions=False,
        metatools=True,
        metatool_dir=meta_dir,
    )
    assert "answer" in enabled.tools.names()
    assert "list_metatools" in enabled.tools.names()


async def test_metatool_executes_as_normal_tool_and_emits_inner_events(tmp_path):
    target = tmp_path / "target.txt"
    target.write_text("hello")
    meta_dir = tmp_path / "metatools"
    _write_spec(
        meta_dir,
        "read_len",
        "text = mu.read(args['path'])\nmu.log('read done')\nmu.result(len(text))",
        {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    )
    events = []
    emitter = EventEmitter()
    emitter.subscribe(events.append)
    model = FakeModel(
        [
            _FM(tool_calls=[_FTC("c1", "read_len", json.dumps({"path": str(target)}))]),
            _FM(content="done"),
        ]
    )
    agent = Agent(
        model=model,
        emitter=emitter,
        session=Session(base_dir=tmp_path / "sessions"),
        extensions=False,
        metatools=True,
        metatool_dir=meta_dir,
    )

    final = await agent.run("use read_len")

    assert final == "done"
    tool_msgs = [m for m in agent.messages if m["role"] == "tool"]
    assert "5" in tool_msgs[0]["content"]
    assert "[log]\nread done" in tool_msgs[0]["content"]
    inner = [e for e in events if isinstance(e, ToolCallStarted) and e.call_id == "metatool:read_len:read"]
    assert len(inner) == 1
    assert "read_len" in model.seen_tools[0]


async def test_metatool_blocked_by_readonly_permission(tmp_path):
    target = tmp_path / "x.txt"
    meta_dir = tmp_path / "metatools"
    _write_spec(meta_dir, "write_x", f"mu.write({str(target)!r}, 'x')\nmu.result('done')")
    reg = ToolRegistry(policy=read_only)
    manager = MetaToolManager(reg, EventEmitter(), base_dir=meta_dir)
    manager.load_all()

    out = await reg.execute("write_x", {})

    assert "permission denied" in out
    assert not target.exists()


async def test_reload_metatools_loads_new_specs_and_reports_errors(tmp_path):
    meta_dir = tmp_path / "metatools"
    _write_spec(meta_dir, "first", "mu.result('one')")
    reg = ToolRegistry()
    manager = MetaToolManager(reg, EventEmitter(), base_dir=meta_dir)
    manager.load_all()
    assert "first" in reg.names()

    _write_spec(meta_dir, "second", "mu.result('two')")
    (meta_dir / "bad.json").write_text("{}", encoding="utf-8")
    out = await reg.execute("reload_metatools", {})

    assert "Loaded 2 meta-tool(s)" in out
    assert "first" in reg.names()
    assert "second" in reg.names()
    assert "[errors]" in out
    assert "missing required field" in out


def test_metatool_name_conflict_is_reported(tmp_path):
    meta_dir = tmp_path / "metatools"
    _write_spec(meta_dir, "read", "mu.result('bad')")
    reg = ToolRegistry()
    manager = MetaToolManager(reg, EventEmitter(), base_dir=meta_dir)

    loaded = manager.load_all()

    assert loaded == []
    assert any("conflicts with existing tool" in e for e in manager.errors)


def test_metatool_prompt_hint_is_opt_in():
    assert "repo-local meta-tools" not in build_system_prompt()
    assert "repo-local meta-tools" in build_system_prompt(metatools=True)
