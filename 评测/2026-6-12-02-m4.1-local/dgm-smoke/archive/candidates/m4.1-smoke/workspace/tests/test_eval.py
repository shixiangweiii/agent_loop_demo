from __future__ import annotations

import os
import sys
import json
from pathlib import Path

import pytest

from mu.eval import (
    EvalConfigError,
    EvalSuite,
    EvalTask,
    extract_attribution,
    run_eval_suite,
    run_pytest,
    scan_eval_artifacts_for_secrets,
    setup_create_pytest_project,
    setup_fix_existing_bug,
    setup_implement_slugify,
)


def _script(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "agent.py"
    p.write_text(body, encoding="utf-8")
    return p


def _suite(name: str = "mini", timeout: float = 5.0) -> EvalSuite:
    def setup(workspace: Path):
        prompt = "write ok"

        def validate(ws: Path):
            target = ws / "out.txt"
            notes = [] if target.exists() else ["out.txt missing"]
            return (0 if target.exists() else 1), "validation", notes

        return prompt, validate

    return EvalSuite(name, [EvalTask("write_file", setup, timeout)])


def _env(secret: str = "SECRET_KEY_VALUE") -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", ""),
        "MU_MODEL": "fake-model",
        "MU_API_KEY": secret,
    }


def test_eval_runner_writes_summary_and_redacts_secret(tmp_path):
    secret = "sk-test-secret-not-for-disk"
    agent = _script(
        tmp_path,
        """
import os
from pathlib import Path
Path('out.txt').write_text('ok')
print(os.environ['MU_API_KEY'])
print('=== 📊 归因报告（best-effort）===')
print('轮数            : 2')
print('LLM 总耗时      : 0.30s  (2 次调用)')
print('工具总耗时      : 0.10s')
print('tokens          : prompt=3 completion=4 total=7')
""",
    )
    run = run_eval_suite(
        _suite(),
        run_root=tmp_path / "runs",
        project_root=tmp_path,
        agent_cmd_builder=lambda _ws, prompt: [sys.executable, str(agent), prompt],
        env=_env(secret),
        require_model_env=True,
    )

    assert run.passed == 1 and run.total == 1
    assert run.secret_scan_passed is True
    assert Path(run.run_dir).is_absolute()
    summary = json.loads(Path(run.summary_json_file).read_text(encoding="utf-8"))
    assert Path(summary["run_dir"]).is_absolute()
    assert summary["secret_scan"]["passed"] is True
    result = run.results[0]
    assert result.attribution["turns"] == 2
    assert result.attribution["total_tokens"] == 7
    assert Path(result.workspace).is_absolute()
    assert Path(result.prompt_file).is_absolute()
    for file in [result.stdout_file, run.summary_json_file, run.summary_md_file]:
        assert secret not in Path(file).read_text(encoding="utf-8")
        assert "[REDACTED:MU_API_KEY]" in Path(result.stdout_file).read_text(encoding="utf-8")


def test_eval_runner_resolves_relative_run_root_and_prompt_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agent = _script(tmp_path, "from pathlib import Path\nPath('out.txt').write_text('ok')\n")

    def setup(workspace: Path):
        prompt = f"workspace={workspace}"

        def validate(ws: Path):
            target = ws / "out.txt"
            return (0 if target.exists() else 1), "validation", []

        return prompt, validate

    run = run_eval_suite(
        EvalSuite("paths", [EvalTask("absolute_prompt", setup)]),
        run_root="relative-runs",
        project_root=".",
        agent_cmd_builder=lambda _ws, prompt: [sys.executable, str(agent), prompt],
        env=_env(),
        require_model_env=True,
    )

    result = run.results[0]
    assert Path(run.run_dir).is_absolute()
    assert Path(result.workspace).is_absolute()
    assert str(Path(result.workspace)) in Path(result.prompt_file).read_text(encoding="utf-8")


def test_eval_runner_records_validator_failure(tmp_path):
    agent = _script(tmp_path, "print('done, but no file')\n")
    run = run_eval_suite(
        _suite(),
        run_root=tmp_path / "runs",
        project_root=tmp_path,
        agent_cmd_builder=lambda _ws, prompt: [sys.executable, str(agent), prompt],
        env=_env(),
        require_model_env=True,
    )

    assert run.passed == 0
    assert run.results[0].validation_returncode == 1
    assert "out.txt missing" in run.results[0].notes


def test_eval_runner_timeout(tmp_path):
    agent = _script(
        tmp_path,
        "import time\ntime.sleep(5)\n",
    )
    run = run_eval_suite(
        _suite(timeout=0.1),
        run_root=tmp_path / "runs",
        project_root=tmp_path,
        agent_cmd_builder=lambda _ws, prompt: [sys.executable, str(agent), prompt],
        env=_env(),
        require_model_env=True,
    )

    assert run.passed == 0
    assert run.results[0].agent_timed_out is True
    assert run.results[0].agent_returncode is None


def test_eval_requires_model_env_by_default(tmp_path):
    with pytest.raises(EvalConfigError):
        run_eval_suite(
            _suite(),
            run_root=tmp_path / "runs",
            project_root=tmp_path,
            agent_cmd_builder=lambda _ws, prompt: [sys.executable, "-c", "pass"],
            env={"PATH": os.environ.get("PATH", "")},
            require_model_env=True,
        )


def test_extract_attribution_empty_when_absent():
    assert extract_attribution("plain output") == {}


def test_run_pytest_ignores_parent_pyproject_testpaths(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\ntestpaths = [\"tests\"]\n",
        encoding="utf-8",
    )
    parent_tests = tmp_path / "tests"
    parent_tests.mkdir()
    (parent_tests / "test_should_not_run.py").write_text(
        "def test_parent_failure():\n    assert False\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "eval" / "task" / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "test_local.py").write_text(
        "def test_local_passes():\n    assert True\n",
        encoding="utf-8",
    )

    rc, text = run_pytest(workspace, ["test_local.py"])

    assert rc == 0
    assert "test_should_not_run" not in text
    assert "--rootdir" in text


def test_basic_validators_pass_with_explicit_test_files(tmp_path):
    workspace = tmp_path / "create"
    workspace.mkdir()
    _, validate = setup_create_pytest_project(workspace)
    (workspace / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n\n"
        "def mul(a, b):\n    return a * b\n",
        encoding="utf-8",
    )
    (workspace / "test_calc.py").write_text(
        "from calc import add, mul\n\n"
        "def test_add():\n    assert add(2, 3) == 5\n\n"
        "def test_mul():\n    assert mul(2, 3) == 6\n",
        encoding="utf-8",
    )
    rc, _text, notes = validate(workspace)
    assert rc == 0 and notes == []

    workspace = tmp_path / "fix"
    workspace.mkdir()
    _, validate = setup_fix_existing_bug(workspace)
    (workspace / "stats_utils.py").write_text(
        "def average(nums):\n"
        "    if not nums:\n"
        "        raise ValueError('nums must not be empty')\n"
        "    return sum(nums) / len(nums)\n",
        encoding="utf-8",
    )
    rc, _text, notes = validate(workspace)
    assert rc == 0 and notes == []

    workspace = tmp_path / "slugify"
    workspace.mkdir()
    _, validate = setup_implement_slugify(workspace)
    (workspace / "string_utils.py").write_text(
        "import re\n\n"
        "def slugify(text: str) -> str:\n"
        "    return re.sub(r'-+', '-', re.sub(r'[^a-z0-9]+', '-', text.lower())).strip('-')\n",
        encoding="utf-8",
    )
    rc, _text, notes = validate(workspace)
    assert rc == 0 and notes == []


def test_secret_scan_reports_process_artifacts_and_ignores_copied_fixtures(tmp_path):
    secret = "sk-real-secret-value"
    (tmp_path / "agent_stdout.txt").write_text(
        f"leaked process output: {secret}\n",
        encoding="utf-8",
    )
    fixture = tmp_path / "archive" / "candidates" / "cand" / "workspace" / "tests"
    fixture.mkdir(parents=True)
    (fixture / "test_fixture.py").write_text(
        "FAKE_KEY = 'sk-fake-fixture-secret'\n",
        encoding="utf-8",
    )

    scan = scan_eval_artifacts_for_secrets(tmp_path, {"MU_API_KEY": secret})

    assert scan["passed"] is False
    assert scan["hits"] == [
        {"path": "agent_stdout.txt", "kind": "env:MU_API_KEY"},
        {"path": "agent_stdout.txt", "kind": "pattern:sk"},
    ]
    assert scan["ignored"] == [
        {
            "path": "archive/candidates/cand/workspace/tests/test_fixture.py",
            "reason": "workspace source fixture is not a process artifact",
        }
    ]

    (tmp_path / "agent_stdout.txt").write_text("[REDACTED:MU_API_KEY]\n", encoding="utf-8")
    scan = scan_eval_artifacts_for_secrets(tmp_path, {"MU_API_KEY": secret})
    assert scan["passed"] is True
