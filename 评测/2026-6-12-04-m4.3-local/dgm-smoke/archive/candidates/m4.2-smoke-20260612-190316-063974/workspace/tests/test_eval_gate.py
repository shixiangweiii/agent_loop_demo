from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from mu import eval_gate
from mu.eval import (
    setup_create_pytest_project,
    setup_fix_existing_bug,
    setup_implement_slugify,
)


def test_default_gate_dir_uses_zero_padded_date():
    assert eval_gate.default_gate_dir(datetime(2026, 6, 2, 3, 4, 5)) == Path(
        "评测/2026-06-02-030405"
    )


def test_render_report_covers_skipped_and_secret_failures():
    report = eval_gate._render_report(
        {
            "run_dir": "/tmp/gate",
            "project_root": "/tmp/project",
            "started_at": "2026-06-12T12:00:00",
            "passed": False,
            "secret_scan": {
                "passed": False,
                "hits": [{"path": "agent_stdout.txt", "kind": "env:MU_API_KEY"}],
            },
            "checks": [
                {
                    "name": "offline_pytest",
                    "passed": True,
                    "returncode": 0,
                    "output_file": "/tmp/gate/pytest-output.txt",
                },
                {
                    "name": "basic_eval_real_model",
                    "passed": True,
                    "skipped": True,
                    "missing_env": ["MU_MODEL"],
                },
                {
                    "name": "dgm_lite_fake_agent_smoke",
                    "passed": False,
                    "error": "candidate failed",
                },
                {
                    "name": "metatool_fake_model_smoke",
                    "passed": True,
                    "inner_bash_events": 1,
                    "permission_denied": True,
                    "metatool_file": "/tmp/gate/quick_pytest.json",
                },
                {
                    "name": "dgm_promotion_smoke",
                    "passed": True,
                    "dirty_rejected": True,
                    "applied": True,
                    "patch_file": "/tmp/gate/promotion.patch",
                },
            ],
        }
    )

    assert "- Overall: FAIL" in report
    assert "skipped: missing MU_MODEL" in report
    assert "candidate failed" in report
    assert "inner_bash_events=1" in report
    assert "dirty_rejected=True" in report
    assert "`agent_stdout.txt` (env:MU_API_KEY)" in report


def test_fake_agent_source_satisfies_basic_validators(tmp_path):
    fake_agent = tmp_path / "fake_agent.py"
    fake_agent.write_text(eval_gate._fake_agent_source(), encoding="utf-8")
    cases = [
        ("create", setup_create_pytest_project),
        ("fix", setup_fix_existing_bug),
        ("slugify", setup_implement_slugify),
    ]

    for name, setup in cases:
        workspace = tmp_path / name
        workspace.mkdir()
        _prompt, validate = setup(workspace)
        completed = subprocess.run(
            [sys.executable, str(fake_agent), "prompt"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert completed.returncode == 0, completed.stderr
        rc, text, notes = validate(workspace)
        assert rc == 0, text
        assert notes == []


def test_run_full_gate_fixed_root_is_repeatable(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    gate_dir = tmp_path / "gate"

    def fake_pytest(gate: Path, _project: Path):
        output = gate / "pytest-output.txt"
        output.write_text("fake pytest ok\n", encoding="utf-8")
        return {
            "name": "offline_pytest",
            "passed": True,
            "returncode": 0,
            "output_file": str(output),
        }

    monkeypatch.setattr(eval_gate, "_run_offline_pytest", fake_pytest)
    env = {"PATH": os.environ.get("PATH", "")}

    first = eval_gate.run_full_gate(
        run_root=gate_dir,
        project_root=project,
        allow_missing_model=True,
        env=env,
    )
    second = eval_gate.run_full_gate(
        run_root=gate_dir,
        project_root=project,
        allow_missing_model=True,
        env=env,
    )

    assert first["passed"] is True
    assert second["passed"] is True
    assert any(c["name"] == "metatool_fake_model_smoke" and c["passed"] for c in second["checks"])
    assert any(c["name"] == "dgm_promotion_smoke" and c["passed"] for c in second["checks"])
    archive = gate_dir / "dgm-smoke" / "archive" / "archive.jsonl"
    assert len(archive.read_text(encoding="utf-8").splitlines()) == 2


def test_run_reports_oserror_as_failed_command(tmp_path):
    result = eval_gate._run(
        ["/definitely/missing/python"],
        cwd=tmp_path,
        timeout=1,
    )

    assert result["returncode"] == 127
    assert "FileNotFoundError" in result["text"]
