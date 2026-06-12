"""Full regression gate for M4.3 guarded DGM promotion.

This module intentionally keeps the gate boring and reproducible:
offline pytest, real-model basic eval, DGM-lite smoke, meta-tool smoke,
DGM promotion smoke, then one final secret scan over the process artifacts.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from .agent import Agent
from .dgm import DgmConfigError, run_dgm_candidate
from .dgm_promote import DgmPromotionError, apply_dgm_promotion, prepare_dgm_promotion
from .eval import (
    EvalConfigError,
    EvalSuite,
    EvalTask,
    basic_coding_suite,
    build_agent_env,
    default_project_root,
    missing_model_env,
    redact_secrets,
    run_eval_suite,
    scan_eval_artifacts_for_secrets,
)
from .events import EventEmitter, ToolCallStarted
from .metatool import MetaToolManager
from .model import ModelResult
from .permission import read_only
from .session import Session
from .tools import ToolRegistry


def default_gate_dir(now: datetime | None = None) -> Path:
    current = now or datetime.now()
    label = current.strftime("%Y-%m-%d-%H%M%S")
    return Path("评测") / label


def run_full_gate(
    *,
    run_root: str | Path | None = None,
    project_root: str | Path | None = None,
    timeout_seconds: float = 360.0,
    allow_missing_model: bool = False,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run the M4.3 full gate and write a compact report."""
    source_env = env or os.environ.copy()
    project = (Path(project_root) if project_root is not None else default_project_root()).resolve()
    gate_dir = (Path(run_root) if run_root is not None else project / default_gate_dir()).resolve()
    gate_dir.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now().isoformat(timespec="seconds")

    checks: list[dict[str, Any]] = []
    checks.append(_run_offline_pytest(gate_dir, project))
    checks.append(
        _run_real_eval(
            gate_dir,
            project,
            source_env,
            timeout_seconds=timeout_seconds,
            allow_missing_model=allow_missing_model,
        )
    )
    checks.append(_run_dgm_smoke(gate_dir, project))
    checks.append(_run_metatool_smoke(gate_dir, project))
    checks.append(_run_dgm_promotion_smoke(gate_dir, project))

    summary = {
        "name": "M4.3 Guarded DGM Promotion Full Gate",
        "run_dir": str(gate_dir),
        "project_root": str(project),
        "started_at": started_at,
        "checks": checks,
        "secret_scan": {},
        "passed": False,
    }
    summary["secret_scan"] = scan_eval_artifacts_for_secrets(gate_dir, source_env)
    summary["passed"] = all(c.get("passed") for c in checks) and summary["secret_scan"].get("passed", False)
    _write_gate_summary(gate_dir, summary, source_env)

    # Include the generated report/latest summaries in the final scan.
    summary["secret_scan"] = scan_eval_artifacts_for_secrets(gate_dir, source_env)
    summary["passed"] = all(c.get("passed") for c in checks) and summary["secret_scan"].get("passed", False)
    _write_gate_summary(gate_dir, summary, source_env)
    return summary


def _run_offline_pytest(gate_dir: Path, project: Path) -> dict[str, Any]:
    output_file = gate_dir / "pytest-output.txt"
    completed = _run([_preferred_python(project), "-m", "pytest", "-q"], cwd=project, timeout=600)
    output_file.write_text(completed["text"], encoding="utf-8")
    return {
        "name": "offline_pytest",
        "passed": completed["returncode"] == 0,
        "returncode": completed["returncode"],
        "output_file": str(output_file.resolve()),
    }


def _run_real_eval(
    gate_dir: Path,
    project: Path,
    source_env: dict[str, str],
    *,
    timeout_seconds: float,
    allow_missing_model: bool,
) -> dict[str, Any]:
    agent_env = build_agent_env(project, source_env=source_env)
    missing = missing_model_env(agent_env)
    if missing:
        return {
            "name": "basic_eval_real_model",
            "passed": allow_missing_model,
            "skipped": allow_missing_model,
            "missing_env": missing,
            "note": "real model eval requires runtime env only; API key is not written",
        }
    try:
        run = run_eval_suite(
            basic_coding_suite(timeout_seconds=timeout_seconds),
            run_root=gate_dir / "real-eval-runs",
            project_root=project,
            env=source_env,
            require_model_env=True,
        )
    except EvalConfigError as e:
        return {
            "name": "basic_eval_real_model",
            "passed": False,
            "error": str(e),
        }
    return {
        "name": "basic_eval_real_model",
        "passed": run.passed == run.total and run.secret_scan_passed,
        "run_dir": run.run_dir,
        "passed_tasks": run.passed,
        "total_tasks": run.total,
        "secret_scan_passed": run.secret_scan_passed,
        "summary_json_file": run.summary_json_file,
        "summary_md_file": run.summary_md_file,
    }


def _run_dgm_smoke(gate_dir: Path, project: Path) -> dict[str, Any]:
    smoke_dir = gate_dir / "dgm-smoke"
    candidate_dir = smoke_dir / "candidate"
    prompt_file = candidate_dir / ".mu" / "prompts" / "smoke.md"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text("M4.2 smoke candidate: keep eval deterministic.\n", encoding="utf-8")
    fake_agent = smoke_dir / "fake_agent.py"
    fake_agent.parent.mkdir(parents=True, exist_ok=True)
    fake_agent.write_text(_fake_agent_source(), encoding="utf-8")

    def build(_workspace: Path, prompt: str) -> list[str]:
        return [_preferred_python(project), str(fake_agent), prompt]

    fake_env = {
        "PATH": os.environ.get("PATH", ""),
        "MU_MODEL": "fake-model",
        "MU_API_KEY": "not-a-real-key",
    }
    try:
        entry = run_dgm_candidate(
            source_type="dir",
            source_path=candidate_dir,
            description="m4.2 full gate smoke",
            candidate_id=f"m4.2-smoke-{datetime.now():%Y%m%d-%H%M%S-%f}",
            project_root=project,
            archive_dir=smoke_dir / "archive",
            suite=basic_coding_suite(timeout_seconds=120),
            agent_cmd_builder=build,
            env=fake_env,
            require_model_env=True,
        )
    except (DgmConfigError, EvalConfigError) as e:
        return {
            "name": "dgm_lite_fake_agent_smoke",
            "passed": False,
            "error": str(e),
        }
    archive = smoke_dir / "archive"
    archive_jsonl = archive / "archive.jsonl"
    latest_summary = archive / "latest-summary.json"
    return {
        "name": "dgm_lite_fake_agent_smoke",
        "passed": entry.passed == entry.total and archive_jsonl.exists() and latest_summary.exists(),
        "entry": asdict(entry),
        "archive_jsonl": str(archive_jsonl.resolve()),
        "latest_summary": str(latest_summary.resolve()),
    }


def _run_metatool_smoke(gate_dir: Path, project: Path) -> dict[str, Any]:
    smoke_dir = gate_dir / "metatool-smoke"
    metatool_dir = smoke_dir / "metatools"
    work_dir = smoke_dir / "work"
    metatool_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    test_file = work_dir / "test_smoke.py"
    test_file.write_text("def test_smoke():\n    assert True\n", encoding="utf-8")
    spec_file = metatool_dir / "quick_pytest.json"
    spec_file.write_text(
        json.dumps(
            {
                "name": "quick_pytest",
                "version": "0.1",
                "description": "Run pytest for a target path.",
                "parameters": {
                    "type": "object",
                    "properties": {"target": {"type": "string"}},
                },
                "code": (
                    "import shlex\n"
                    "target = shlex.quote(args.get('target') or '.')\n"
                    f"out = mu.bash({(_preferred_python(project) + ' -m pytest -q ')!r} + target, timeout=120)\n"
                    "mu.result(out)"
                ),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    events: list[Any] = []
    emitter = EventEmitter()
    emitter.subscribe(events.append)
    model = _GateFakeModel(
        [
            _GateFakeMessage(
                tool_calls=[
                    _GateFakeToolCall(
                        "m1",
                        "quick_pytest",
                        json.dumps({"target": str(test_file)}, ensure_ascii=False),
                    )
                ]
            ),
            _GateFakeMessage(content="meta-tool smoke done"),
        ]
    )
    try:
        agent = Agent(
            model=model,
            emitter=emitter,
            session=Session(base_dir=smoke_dir / "sessions"),
            extensions=False,
            metatools=True,
            metatool_dir=metatool_dir,
        )
        final = asyncio.run(agent.run("run quick pytest meta-tool"))
    except Exception as e:  # noqa: BLE001 - gate converts failures into report entries
        return {"name": "metatool_fake_model_smoke", "passed": False, "error": str(e)}
    tool_outputs = [
        m.get("content", "")
        for m in agent.session.path_to_head()
        if m.get("role") == "tool"
    ]
    inner_bash = [
        e for e in events
        if isinstance(e, ToolCallStarted) and e.call_id == "metatool:quick_pytest:bash"
    ]

    denied = ""
    try:
        reg = ToolRegistry(policy=read_only)
        manager = MetaToolManager(reg, EventEmitter(), base_dir=metatool_dir)
        manager.load_all()
        denied = str(asyncio.run(reg.execute("quick_pytest", {"target": str(test_file)})))
    except Exception as e:  # noqa: BLE001
        denied = str(e)

    passed = (
        final == "meta-tool smoke done"
        and any("passed" in output for output in tool_outputs)
        and bool(inner_bash)
        and "permission denied" in denied
    )
    return {
        "name": "metatool_fake_model_smoke",
        "passed": passed,
        "metatool_file": str(spec_file.resolve()),
        "inner_bash_events": len(inner_bash),
        "permission_denied": "permission denied" in denied,
    }


def _run_dgm_promotion_smoke(gate_dir: Path, project: Path) -> dict[str, Any]:
    smoke_dir = gate_dir / "dgm-promotion-smoke"
    if smoke_dir.exists():
        shutil.rmtree(smoke_dir)
    repo = smoke_dir / "project"
    candidate_dir = smoke_dir / "candidate"
    archive = smoke_dir / "archive"
    fake_agent = smoke_dir / "fake_agent.py"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / ".mu" / "prompts").mkdir(parents=True, exist_ok=True)
    (repo / ".mu" / "prompts" / "smoke.md").write_text("base\n", encoding="utf-8")
    (repo / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    _git(repo, "init")
    _git(repo, "config", "user.email", "gate@example.com")
    _git(repo, "config", "user.name", "Gate")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    candidate_file = candidate_dir / ".mu" / "prompts" / "smoke.md"
    candidate_file.parent.mkdir(parents=True, exist_ok=True)
    candidate_file.write_text("promoted\n", encoding="utf-8")
    fake_agent.write_text(
        "from pathlib import Path\nPath('marker.txt').write_text('ok')\n",
        encoding="utf-8",
    )

    def setup_marker(workspace: Path):
        def validate(ws: Path):
            marker = ws / "marker.txt"
            return (0 if marker.exists() else 1), "validation", ([] if marker.exists() else ["marker missing"])

        return "create marker", validate

    def build(_workspace: Path, prompt: str) -> list[str]:
        return [_preferred_python(project), str(fake_agent), prompt]

    fake_env = {
        "PATH": os.environ.get("PATH", ""),
        "MU_MODEL": "fake-model",
        "MU_API_KEY": "not-a-real-key",
    }
    try:
        run_dgm_candidate(
            source_type="dir",
            source_path=candidate_dir,
            description="m4.3 promotion smoke",
            candidate_id=f"m4.3-promotion-{datetime.now():%Y%m%d-%H%M%S-%f}",
            project_root=repo,
            archive_dir=archive,
            suite=EvalSuite("promotion-smoke", [EvalTask("marker", setup_marker, 30)]),
            agent_cmd_builder=build,
            env=fake_env,
            require_model_env=True,
        )
        promotion = prepare_dgm_promotion(archive, project_root=repo, env=fake_env)
        (repo / ".mu" / "prompts" / "smoke.md").write_text("dirty\n", encoding="utf-8")
        dirty_rejected = False
        try:
            apply_dgm_promotion(promotion, project_root=repo, env=fake_env)
        except DgmPromotionError:
            dirty_rejected = True
        (repo / ".mu" / "prompts" / "smoke.md").write_text("base\n", encoding="utf-8")
        applied = apply_dgm_promotion(promotion, project_root=repo, env=fake_env)
    except (DgmConfigError, EvalConfigError, DgmPromotionError, subprocess.CalledProcessError) as e:
        return {
            "name": "dgm_promotion_smoke",
            "passed": False,
            "error": str(e),
        }

    target_text = (repo / ".mu" / "prompts" / "smoke.md").read_text(encoding="utf-8")
    return {
        "name": "dgm_promotion_smoke",
        "passed": (
            dirty_rejected
            and applied.applied
            and target_text == "promoted\n"
            and applied.secret_scan.get("passed", False)
            and Path(applied.patch_file).exists()
            and bool(applied.preflight_output_file)
            and Path(applied.preflight_output_file).exists()
        ),
        "promotion_dir": applied.promotion_dir,
        "patch_file": applied.patch_file,
        "preflight_output_file": applied.preflight_output_file,
        "dirty_rejected": dirty_rejected,
        "applied": applied.applied,
    }


def _fake_agent_source() -> str:
    return """\
from pathlib import Path
import re

root = Path.cwd()
if (root / "test_stats_utils.py").exists():
    (root / "stats_utils.py").write_text(
        "def average(nums):\\n"
        "    if not nums:\\n"
        "        raise ValueError('nums must not be empty')\\n"
        "    return sum(nums) / len(nums)\\n",
        encoding="utf-8",
    )
elif (root / "test_string_utils.py").exists():
    (root / "string_utils.py").write_text(
        "import re\\n\\n"
        "def slugify(text: str) -> str:\\n"
        "    return re.sub(r'-+', '-', re.sub(r'[^a-z0-9]+', '-', text.lower())).strip('-')\\n",
        encoding="utf-8",
    )
else:
    (root / "calc.py").write_text(
        "def add(a, b):\\n    return a + b\\n\\n"
        "def mul(a, b):\\n    return a * b\\n",
        encoding="utf-8",
    )
    (root / "test_calc.py").write_text(
        "from calc import add, mul\\n\\n"
        "def test_add():\\n    assert add(2, 3) == 5\\n\\n"
        "def test_mul():\\n    assert mul(2, 3) == 6\\n",
        encoding="utf-8",
    )
print("fake agent done")
"""


def _run(cmd: Sequence[str], *, cwd: Path, timeout: float) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            list(cmd),
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        rc = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as e:
        rc = 124
        stdout = e.stdout or ""
        stderr = e.stderr or ""
    except OSError as e:
        rc = 127
        stdout = ""
        stderr = f"{type(e).__name__}: {e}"
    text = (
        "$ " + " ".join(cmd) + "\n\n"
        + "[stdout]\n" + _as_text(stdout)
        + "\n[stderr]\n" + _as_text(stderr)
        + f"\n[exit code] {rc}\n"
    )
    return {"returncode": rc, "text": text}


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )


def _write_gate_summary(gate_dir: Path, summary: dict[str, Any], env: dict[str, str]) -> None:
    json_file = gate_dir / "latest-summary.json"
    md_file = gate_dir / "latest-summary.md"
    report_file = gate_dir / "m4.3-guarded-dgm-promotion-report.md"
    json_file.write_text(
        redact_secrets(json.dumps(summary, ensure_ascii=False, indent=2), env),
        encoding="utf-8",
    )
    report = _render_report(summary)
    md_file.write_text(redact_secrets(report, env), encoding="utf-8")
    report_file.write_text(redact_secrets(report, env), encoding="utf-8")


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# M4.3 Guarded DGM Promotion Full Gate Report",
        "",
        f"- Run dir: `{summary['run_dir']}`",
        f"- Project root: `{summary['project_root']}`",
        f"- Started at: `{summary['started_at']}`",
        f"- Overall: {'PASS' if summary.get('passed') else 'FAIL'}",
        f"- Secret scan: {'PASS' if summary.get('secret_scan', {}).get('passed') else 'FAIL'}",
        "- API key: runtime environment only; not written to artifacts",
        "",
        "| Check | Result | Details |",
        "|---|---:|---|",
    ]
    for check in summary["checks"]:
        lines.append(
            f"| {check['name']} | {'PASS' if check.get('passed') else 'FAIL'} | "
            f"{_check_detail(check)} |"
        )
    scan = summary.get("secret_scan", {})
    if not scan.get("passed", False):
        lines.extend(["", "## Secret scan findings", ""])
        for hit in scan.get("hits", []):
            lines.append(f"- `{hit.get('path')}` ({hit.get('kind')})")
    return "\n".join(lines) + "\n"


def _check_detail(check: dict[str, Any]) -> str:
    if check.get("name") == "offline_pytest":
        return f"rc={check.get('returncode')}; output=`{check.get('output_file')}`"
    if check.get("name") == "basic_eval_real_model":
        if check.get("skipped"):
            return "skipped: missing " + ", ".join(check.get("missing_env", []))
        if check.get("error"):
            return str(check["error"])
        return f"{check.get('passed_tasks')}/{check.get('total_tasks')} tasks; run=`{check.get('run_dir')}`"
    if check.get("name") == "dgm_lite_fake_agent_smoke":
        if check.get("error"):
            return str(check["error"])
        entry = check.get("entry", {})
        return f"{entry.get('passed')}/{entry.get('total')} tasks; archive=`{check.get('archive_jsonl')}`"
    if check.get("name") == "metatool_fake_model_smoke":
        if check.get("error"):
            return str(check["error"])
        return (
            f"inner_bash_events={check.get('inner_bash_events')}; "
            f"permission_denied={check.get('permission_denied')}; "
            f"spec=`{check.get('metatool_file')}`"
        )
    if check.get("name") == "dgm_promotion_smoke":
        if check.get("error"):
            return str(check["error"])
        return (
            f"dirty_rejected={check.get('dirty_rejected')}; "
            f"applied={check.get('applied')}; "
            f"patch=`{check.get('patch_file')}`"
        )
    return ""


def _preferred_python(project: Path) -> str:
    venv_python = project / ".venv" / "bin" / "python"
    return str(venv_python) if venv_python.exists() else sys.executable


def _as_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m mu.eval_gate",
        description="Run the M4.3 guarded DGM promotion full gate",
    )
    parser.add_argument("--run-root", help="gate artifact directory; defaults to 评测/<date-run>")
    parser.add_argument("--project-root", default=".", help="repository root")
    parser.add_argument("--timeout", type=float, default=360.0, help="per real eval task timeout")
    parser.add_argument(
        "--allow-missing-model",
        action="store_true",
        help="skip real-model eval when MU_MODEL/API key are absent",
    )
    return parser.parse_args(list(argv))


class _GateFakeFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _GateFakeToolCall:
    def __init__(self, call_id: str, name: str, arguments: str) -> None:
        self.id = call_id
        self.function = _GateFakeFunction(name, arguments)


class _GateFakeMessage:
    def __init__(self, content: str | None = None, tool_calls: list[_GateFakeToolCall] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _GateFakeModel:
    def __init__(self, scripted: list[_GateFakeMessage]) -> None:
        self._scripted = list(scripted)
        self.calls = 0

    async def acomplete(self, messages, tools, *, stream=False, on_delta=None):
        message = self._scripted[self.calls]
        self.calls += 1
        return ModelResult(message=message)


def main(argv: Sequence[str] | None = None) -> int:
    ns = _parse_args(sys.argv[1:] if argv is None else argv)
    summary = run_full_gate(
        run_root=ns.run_root,
        project_root=ns.project_root,
        timeout_seconds=ns.timeout,
        allow_missing_model=ns.allow_missing_model,
    )
    print(f"Full gate run dir: {summary['run_dir']}")
    print(f"Overall: {'PASS' if summary.get('passed') else 'FAIL'}")
    print(f"Secret scan: {'PASS' if summary.get('secret_scan', {}).get('passed') else 'FAIL'}")
    return 0 if summary.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
