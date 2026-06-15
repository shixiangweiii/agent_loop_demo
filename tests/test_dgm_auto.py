from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from mu.dgm import DgmConfigError, read_archive
from mu.dgm_auto import run_dgm_auto
from mu.eval import EvalSuite, EvalTask


def _script(tmp_path: Path, body: str) -> Path:
    p = tmp_path / f"script_{len(list(tmp_path.glob('script_*.py')))}.py"
    p.write_text(body, encoding="utf-8")
    return p


def _project_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "mu").mkdir(parents=True)
    (root / "mu" / "__init__.py").write_text("", encoding="utf-8")
    (root / "README.md").write_text("root", encoding="utf-8")
    return root


def _suite() -> EvalSuite:
    def setup(workspace: Path):
        def validate(ws: Path):
            marker = ws / "marker.txt"
            return (0 if marker.exists() else 1), "validation", ([] if marker.exists() else ["marker missing"])

        return "create marker", validate

    return EvalSuite("mini-auto", [EvalTask("marker", setup, 5)])


def _env(secret: str = "SECRET_AUTO_KEY") -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", ""),
        "MU_MODEL": "fake-model",
        "MU_API_KEY": secret,
    }


def _agent_builder(agent: Path):
    return lambda _ws, prompt: [sys.executable, str(agent), prompt]


def _generator_builder(generator: Path):
    return lambda _ws, prompt: [sys.executable, str(generator), prompt]


def test_auto_generates_multiple_candidates_and_archives(tmp_path):
    project = _project_root(tmp_path)
    archive = tmp_path / "archive"
    agent = _script(tmp_path, "from pathlib import Path\nPath('marker.txt').write_text('ok')\n")
    generator = _script(
        tmp_path,
        "from pathlib import Path\n"
        "import sys\n"
        "root = Path.cwd()\n"
        "(root / '.mu' / 'prompts').mkdir(parents=True, exist_ok=True)\n"
        "idx = 'one' if '1/2' in sys.argv[-1] else 'two'\n"
        "(root / '.mu' / 'prompts' / f'{idx}.md').write_text('hint\\n', encoding='utf-8')\n",
    )

    run = run_dgm_auto(
        description="prompt hints",
        count=2,
        project_root=project,
        archive_dir=archive,
        suite=_suite(),
        agent_cmd_builder=_agent_builder(agent),
        generate_cmd_builder=_generator_builder(generator),
        env=_env(),
    )

    assert run.generated_count == 2
    assert run.passed_count == 2
    assert run.best_candidate_id
    assert len(read_archive(archive)) == 2
    assert Path(run.summary_json_file).exists()
    assert Path(run.summary_md_file).exists()


def test_auto_records_generation_failure_and_continues(tmp_path):
    project = _project_root(tmp_path)
    archive = tmp_path / "archive"
    agent = _script(tmp_path, "from pathlib import Path\nPath('marker.txt').write_text('ok')\n")
    generator = _script(
        tmp_path,
        "from pathlib import Path\n"
        "import sys\n"
        "root = Path.cwd()\n"
        "if '1/2' in sys.argv[-1]:\n"
        "    Path('README.md').write_text('bad', encoding='utf-8')\n"
        "else:\n"
        "    (root / '.mu' / 'prompts').mkdir(parents=True, exist_ok=True)\n"
        "    (root / '.mu' / 'prompts' / 'ok.md').write_text('hint\\n', encoding='utf-8')\n",
    )

    run = run_dgm_auto(
        description="one bad one good",
        count=2,
        project_root=project,
        archive_dir=archive,
        suite=_suite(),
        agent_cmd_builder=_agent_builder(agent),
        generate_cmd_builder=_generator_builder(generator),
        env=_env(),
    )

    assert run.generated_count == 1
    assert run.passed_count == 1
    assert len(read_archive(archive)) == 1
    assert run.candidates[0].generated is False
    assert "prompt-only DGM scope" in (run.candidates[0].error or "")
    assert run.candidates[1].generated is True


def test_auto_missing_model_env_fails_before_archive_entry(tmp_path):
    project = _project_root(tmp_path)
    archive = tmp_path / "archive"

    with pytest.raises(DgmConfigError):
        run_dgm_auto(
            description="missing env",
            project_root=project,
            archive_dir=archive,
            suite=_suite(),
            env={"PATH": os.environ.get("PATH", "")},
        )

    assert read_archive(archive) == []


def test_auto_rejects_generated_non_prompt_paths(tmp_path):
    project = _project_root(tmp_path)
    archive = tmp_path / "archive"
    agent = _script(tmp_path, "from pathlib import Path\nPath('marker.txt').write_text('ok')\n")
    generator = _script(tmp_path, "from pathlib import Path\nPath('README.md').write_text('bad')\n")

    run = run_dgm_auto(
        description="bad path",
        count=1,
        project_root=project,
        archive_dir=archive,
        suite=_suite(),
        agent_cmd_builder=_agent_builder(agent),
        generate_cmd_builder=_generator_builder(generator),
        env=_env(),
    )

    assert run.generated_count == 0
    assert read_archive(archive) == []
    assert "prompt-only DGM scope" in (run.candidates[0].error or "")


def test_auto_summary_redacts_secret(tmp_path):
    project = _project_root(tmp_path)
    archive = tmp_path / "archive"
    secret = "SECRET_AUTO_VALUE_123"
    agent = _script(tmp_path, "from pathlib import Path\nPath('marker.txt').write_text('ok')\n")
    generator = _script(
        tmp_path,
        "from pathlib import Path\n"
        "root = Path.cwd()\n"
        "(root / '.mu' / 'prompts').mkdir(parents=True, exist_ok=True)\n"
        "(root / '.mu' / 'prompts' / 'hint.md').write_text('hint\\n', encoding='utf-8')\n",
    )

    run = run_dgm_auto(
        description=f"do not leak {secret}",
        count=1,
        project_root=project,
        archive_dir=archive,
        suite=_suite(),
        agent_cmd_builder=_agent_builder(agent),
        generate_cmd_builder=_generator_builder(generator),
        env=_env(secret),
    )

    assert run.secret_scan_passed
    assert secret not in Path(run.summary_json_file).read_text(encoding="utf-8")
    assert secret not in Path(run.summary_md_file).read_text(encoding="utf-8")
