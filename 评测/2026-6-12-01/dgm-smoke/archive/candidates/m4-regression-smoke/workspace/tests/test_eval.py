from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from mu.eval import (
    EvalConfigError,
    EvalSuite,
    EvalTask,
    extract_attribution,
    run_eval_suite,
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
    result = run.results[0]
    assert result.attribution["turns"] == 2
    assert result.attribution["total_tokens"] == 7
    for file in [result.stdout_file, run.summary_json_file, run.summary_md_file]:
        assert secret not in Path(file).read_text(encoding="utf-8")
        assert "[REDACTED:MU_API_KEY]" in Path(result.stdout_file).read_text(encoding="utf-8")


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
