from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from mu.dgm import DgmConfigError, read_archive, run_dgm_candidate
from mu.eval import EvalSuite, EvalTask
from mu.prompts import build_system_prompt


def _script(tmp_path: Path, body: str) -> Path:
    p = tmp_path / f"agent_{len(list(tmp_path.glob('agent_*.py')))}.py"
    p.write_text(body, encoding="utf-8")
    return p


def _suite() -> EvalSuite:
    def setup(workspace: Path):
        prompt = "create marker"

        def validate(ws: Path):
            marker = ws / "marker.txt"
            return (0 if marker.exists() else 1), "validation", ([] if marker.exists() else ["marker missing"])

        return prompt, validate

    return EvalSuite("mini-dgm", [EvalTask("marker", setup, 5)])


def _env(secret: str = "SECRET_DGM_KEY") -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", ""),
        "MU_MODEL": "fake-model",
        "MU_API_KEY": secret,
    }


def _project_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "mu").mkdir(parents=True)
    (root / "mu" / "__init__.py").write_text("", encoding="utf-8")
    (root / "README.md").write_text("root", encoding="utf-8")
    return root


def _candidate_dir(tmp_path: Path, rel: str, content: str = "candidate") -> Path:
    root = tmp_path / f"candidate_{len(list(tmp_path.glob('candidate_*')))}"
    target = root / rel
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")
    return root


def test_dgm_candidate_runs_in_copy_and_archives(tmp_path):
    project = _project_root(tmp_path)
    candidate = _candidate_dir(tmp_path, ".mu/prompts/hint.md", "Prefer tests.")
    agent = _script(tmp_path, "from pathlib import Path\nPath('marker.txt').write_text('ok')\n")

    entry = run_dgm_candidate(
        source_type="dir",
        source_path=candidate,
        description="prompt hint",
        candidate_id="cand-good",
        project_root=project,
        archive_dir=tmp_path / "archive",
        suite=_suite(),
        agent_cmd_builder=lambda _ws, prompt: [sys.executable, str(agent), prompt],
        env=_env(),
        require_model_env=True,
    )

    assert entry.passed == 1
    assert entry.changed_paths == [".mu/prompts/hint.md"]
    assert not (project / ".mu" / "prompts" / "hint.md").exists()
    assert (Path(entry.candidate_workspace) / ".mu" / "prompts" / "hint.md").exists()
    entries = read_archive(tmp_path / "archive")
    assert len(entries) == 1
    assert entries[0].id == "cand-good"
    assert entries[0].is_best is True
    assert "SECRET_DGM_KEY" not in (tmp_path / "archive" / "archive.jsonl").read_text(encoding="utf-8")
    assert "SECRET_DGM_KEY" not in (tmp_path / "archive" / "latest-summary.md").read_text(encoding="utf-8")


def test_dgm_archive_keeps_all_candidates_and_marks_best(tmp_path):
    project = _project_root(tmp_path)
    good_candidate = _candidate_dir(tmp_path, ".mu/prompts/good.md", "good")
    bad_candidate = _candidate_dir(tmp_path, ".mu/prompts/bad.md", "bad")
    good_agent = _script(tmp_path, "from pathlib import Path\nPath('marker.txt').write_text('ok')\n")
    bad_agent = _script(tmp_path, "print('no marker')\n")
    archive = tmp_path / "archive"

    run_dgm_candidate(
        source_type="dir",
        source_path=bad_candidate,
        description="bad",
        candidate_id="cand-bad",
        project_root=project,
        archive_dir=archive,
        suite=_suite(),
        agent_cmd_builder=lambda _ws, prompt: [sys.executable, str(bad_agent), prompt],
        env=_env(),
        require_model_env=True,
    )
    run_dgm_candidate(
        source_type="dir",
        source_path=good_candidate,
        description="good",
        parent_id="cand-bad",
        candidate_id="cand-good",
        project_root=project,
        archive_dir=archive,
        suite=_suite(),
        agent_cmd_builder=lambda _ws, prompt: [sys.executable, str(good_agent), prompt],
        env=_env(),
        require_model_env=True,
    )

    entries = read_archive(archive)
    assert {e.id for e in entries} == {"cand-bad", "cand-good"}
    assert [e.id for e in entries if e.is_best] == ["cand-good"]
    summary = (archive / "latest-summary.md").read_text(encoding="utf-8")
    assert "`cand-good`" in summary and "`cand-bad`" in summary
    assert "cand-bad" in [e.parent_id for e in entries if e.id == "cand-good"]


def test_dgm_patch_candidate(tmp_path):
    project = _project_root(tmp_path)
    patch = tmp_path / "candidate.patch"
    patch.write_text(
        "\n".join(
            [
                "diff --git a/.mu/prompts/patch.md b/.mu/prompts/patch.md",
                "new file mode 100644",
                "index 0000000..ce01362",
                "--- /dev/null",
                "+++ b/.mu/prompts/patch.md",
                "@@ -0,0 +1 @@",
                "+patch hint",
                "",
            ]
        ),
        encoding="utf-8",
    )
    agent = _script(tmp_path, "from pathlib import Path\nPath('marker.txt').write_text('ok')\n")

    entry = run_dgm_candidate(
        source_type="patch",
        source_path=patch,
        candidate_id="cand-patch",
        project_root=project,
        archive_dir=tmp_path / "archive",
        suite=_suite(),
        agent_cmd_builder=lambda _ws, prompt: [sys.executable, str(agent), prompt],
        env=_env(),
        require_model_env=True,
    )

    assert entry.changed_paths == [".mu/prompts/patch.md"]
    assert (Path(entry.candidate_workspace) / ".mu" / "prompts" / "patch.md").read_text(encoding="utf-8") == "patch hint\n"


def test_dgm_rejects_core_candidate_path(tmp_path):
    project = _project_root(tmp_path)
    candidate = _candidate_dir(tmp_path, "mu/agent.py", "bad")
    agent = _script(tmp_path, "from pathlib import Path\nPath('marker.txt').write_text('ok')\n")

    with pytest.raises(DgmConfigError):
        run_dgm_candidate(
            source_type="dir",
            source_path=candidate,
            candidate_id="cand-core",
            project_root=project,
            archive_dir=tmp_path / "archive",
            suite=_suite(),
            agent_cmd_builder=lambda _ws, prompt: [sys.executable, str(agent), prompt],
            env=_env(),
            require_model_env=True,
        )


def test_dgm_rejects_extension_candidate_under_restrictive_permission(tmp_path):
    project = _project_root(tmp_path)
    candidate = _candidate_dir(
        tmp_path,
        ".mu/extensions/e.py",
        "from mu.extsdk import run_extension\nif __name__ == '__main__': run_extension('e')\n",
    )
    agent = _script(tmp_path, "from pathlib import Path\nPath('marker.txt').write_text('ok')\n")

    with pytest.raises(DgmConfigError):
        run_dgm_candidate(
            source_type="dir",
            source_path=candidate,
            candidate_id="cand-ext",
            project_root=project,
            archive_dir=tmp_path / "archive",
            suite=_suite(),
            agent_cmd_builder=lambda _ws, prompt: [sys.executable, str(agent), prompt],
            env=_env(),
            require_model_env=True,
            permission="readonly",
        )


def test_dgm_metatool_candidate_is_allowed_and_uses_candidate_dir_env(tmp_path):
    project = _project_root(tmp_path)
    candidate = _candidate_dir(
        tmp_path,
        ".mu/metatools/quick_pytest.json",
        json.dumps(
            {
                "name": "quick_pytest",
                "version": "0.1",
                "description": "Run pytest.",
                "parameters": {"type": "object", "properties": {}},
                "code": "mu.result('ok')",
            },
            ensure_ascii=False,
        ),
    )
    agent = _script(
        tmp_path,
        "import os\nfrom pathlib import Path\n"
        "assert Path(os.environ['MU_METATOOL_DIR']).name == 'metatools'\n"
        "Path('marker.txt').write_text('ok')\n",
    )

    entry = run_dgm_candidate(
        source_type="dir",
        source_path=candidate,
        candidate_id="cand-metatool",
        project_root=project,
        archive_dir=tmp_path / "archive",
        suite=_suite(),
        agent_cmd_builder=lambda _ws, prompt: [sys.executable, str(agent), prompt],
        agent_args=["--metatools"],
        env=_env(),
        require_model_env=True,
    )

    assert entry.changed_paths == [".mu/metatools/quick_pytest.json"]
    assert entry.passed == 1
    assert not (project / ".mu" / "metatools" / "quick_pytest.json").exists()
    assert (Path(entry.candidate_workspace) / ".mu" / "metatools" / "quick_pytest.json").exists()


def test_dgm_rejects_metatool_candidate_under_restrictive_permission(tmp_path):
    project = _project_root(tmp_path)
    candidate = _candidate_dir(
        tmp_path,
        ".mu/metatools/quick_pytest.json",
        json.dumps(
            {
                "name": "quick_pytest",
                "version": "0.1",
                "description": "Run pytest.",
                "parameters": {"type": "object", "properties": {}},
                "code": "mu.result('ok')",
            }
        ),
    )
    agent = _script(tmp_path, "from pathlib import Path\nPath('marker.txt').write_text('ok')\n")

    with pytest.raises(DgmConfigError):
        run_dgm_candidate(
            source_type="dir",
            source_path=candidate,
            candidate_id="cand-metatool-ro",
            project_root=project,
            archive_dir=tmp_path / "archive",
            suite=_suite(),
            agent_cmd_builder=lambda _ws, prompt: [sys.executable, str(agent), prompt],
            env=_env(),
            require_model_env=True,
            permission="readonly",
        )


def test_prompt_snippet_injection(monkeypatch, tmp_path):
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    (prompt_dir / "hint.md").write_text("Always verify.", encoding="utf-8")
    monkeypatch.setenv("MU_PROMPT_SNIPPET_DIR", str(prompt_dir))

    prompt = build_system_prompt()

    assert "[Prompt snippet: hint.md]" in prompt
    assert "Always verify." in prompt
