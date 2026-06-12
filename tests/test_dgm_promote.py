from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import pytest

from mu.dgm import DgmArchiveEntry
from mu.dgm_promote import (
    DgmPromotionError,
    apply_dgm_promotion,
    prepare_dgm_promotion,
)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )


def _project(tmp_path: Path, *, git: bool = True, failing_test: bool = False) -> Path:
    root = tmp_path / f"project_{len(list(tmp_path.glob('project_*')))}"
    (root / ".mu" / "prompts").mkdir(parents=True)
    (root / ".mu" / "prompts" / "update.md").write_text("old update\n", encoding="utf-8")
    (root / ".mu" / "prompts" / "delete.md").write_text("delete me\n", encoding="utf-8")
    (root / "README.md").write_text("root\n", encoding="utf-8")
    (root / "test_ok.py").write_text(
        "def test_ok():\n    assert " + ("False" if failing_test else "True") + "\n",
        encoding="utf-8",
    )
    if git:
        _git(root, "init")
        _git(root, "config", "user.email", "test@example.com")
        _git(root, "config", "user.name", "Test User")
        _git(root, "add", ".")
        _git(root, "commit", "-m", "init")
    return root


def _archive_candidate(
    archive: Path,
    project: Path,
    *,
    candidate_id: str,
    changes: dict[str, str | None],
    passed: int = 1,
    total: int = 1,
    secret_scan_passed: bool = True,
    duration: float = 1.0,
) -> DgmArchiveEntry:
    candidate_workspace = archive / "candidates" / candidate_id / "workspace"
    shutil.copytree(project, candidate_workspace, ignore=shutil.ignore_patterns(".git"))
    for rel, content in changes.items():
        target = candidate_workspace / rel
        if content is None:
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
    run_dir = archive / "runs" / candidate_id / "20260101-000000"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "summary.json").write_text(
        json.dumps(
            {
                "passed": passed,
                "total": total,
                "secret_scan": {"passed": secret_scan_passed, "hits": [] if secret_scan_passed else [{"path": "x"}]},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    entry = DgmArchiveEntry(
        id=candidate_id,
        parent_id=None,
        description=candidate_id,
        source_type="dir",
        source_path=None,
        changed_paths=list(changes),
        candidate_workspace=str(candidate_workspace),
        eval_run_dir=str(run_dir),
        passed=passed,
        total=total,
        score=round(passed / max(total, 1), 4),
        duration_seconds=duration,
        total_tokens=0,
        is_best=False,
        created_at=datetime.now().isoformat(timespec="seconds"),
    )
    archive.mkdir(parents=True, exist_ok=True)
    with (archive / "archive.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
    return entry


def test_prepare_defaults_to_best_and_does_not_modify_project(tmp_path):
    project = _project(tmp_path)
    archive = tmp_path / "archive"
    _archive_candidate(
        archive,
        project,
        candidate_id="bad",
        changes={".mu/prompts/update.md": "bad\n"},
        passed=0,
        total=1,
        duration=0.1,
    )
    _archive_candidate(
        archive,
        project,
        candidate_id="good",
        changes={".mu/prompts/update.md": "new update\n"},
        passed=1,
        total=1,
        duration=1.0,
    )

    promotion = prepare_dgm_promotion(archive, project_root=project)

    assert promotion.candidate_id == "good"
    assert Path(promotion.patch_file).exists()
    assert Path(promotion.summary_json_file).exists()
    assert Path(promotion.summary_md_file).exists()
    assert promotion.applied is False
    assert (project / ".mu" / "prompts" / "update.md").read_text(encoding="utf-8") == "old update\n"


def test_prepare_specific_candidate_and_missing_candidate(tmp_path):
    project = _project(tmp_path)
    archive = tmp_path / "archive"
    _archive_candidate(
        archive,
        project,
        candidate_id="one",
        changes={".mu/prompts/update.md": "one\n"},
    )

    promotion = prepare_dgm_promotion(archive, candidate_id="one", project_root=project)

    assert promotion.candidate_id == "one"
    with pytest.raises(DgmPromotionError, match="candidate not found"):
        prepare_dgm_promotion(archive, candidate_id="missing", project_root=project)


@pytest.mark.parametrize(
    ("passed", "total", "secret_scan_passed", "message"),
    [
        (0, 1, True, "did not pass"),
        (0, 0, True, "zero eval tasks"),
        (1, 1, False, "secret scan did not pass"),
    ],
)
def test_prepare_rejects_unpromotable_candidates(tmp_path, passed, total, secret_scan_passed, message):
    project = _project(tmp_path)
    archive = tmp_path / "archive"
    _archive_candidate(
        archive,
        project,
        candidate_id="candidate",
        changes={".mu/prompts/update.md": "new\n"},
        passed=passed,
        total=total,
        secret_scan_passed=secret_scan_passed,
    )

    with pytest.raises(DgmPromotionError, match=message):
        prepare_dgm_promotion(archive, project_root=project)


def test_prepare_rejects_missing_eval_summary(tmp_path):
    project = _project(tmp_path)
    archive = tmp_path / "archive"
    entry = _archive_candidate(
        archive,
        project,
        candidate_id="candidate",
        changes={".mu/prompts/update.md": "new\n"},
    )
    shutil.rmtree(Path(entry.eval_run_dir))

    with pytest.raises(DgmPromotionError, match="eval summary missing"):
        prepare_dgm_promotion(archive, project_root=project)


def test_apply_create_update_delete_after_preflight(tmp_path):
    project = _project(tmp_path)
    archive = tmp_path / "archive"
    _archive_candidate(
        archive,
        project,
        candidate_id="candidate",
        changes={
            ".mu/prompts/new.md": "created\n",
            ".mu/prompts/update.md": "updated\n",
            ".mu/prompts/delete.md": None,
        },
    )
    promotion = prepare_dgm_promotion(archive, project_root=project)

    applied = apply_dgm_promotion(promotion)

    assert applied.applied is True
    assert (project / ".mu" / "prompts" / "new.md").read_text(encoding="utf-8") == "created\n"
    assert (project / ".mu" / "prompts" / "update.md").read_text(encoding="utf-8") == "updated\n"
    assert not (project / ".mu" / "prompts" / "delete.md").exists()
    assert (Path(applied.backup_dir) / "manifest.json").exists()
    assert "new file mode" in Path(applied.patch_file).read_text(encoding="utf-8")
    assert "deleted file mode" in Path(applied.patch_file).read_text(encoding="utf-8")


def test_apply_rejects_dirty_target_but_ignores_unrelated_dirty_file(tmp_path):
    project = _project(tmp_path)
    archive = tmp_path / "archive"
    _archive_candidate(
        archive,
        project,
        candidate_id="candidate",
        changes={".mu/prompts/update.md": "updated\n"},
    )
    promotion = prepare_dgm_promotion(archive, project_root=project)
    (project / "README.md").write_text("unrelated dirty\n", encoding="utf-8")

    apply_dgm_promotion(promotion)
    assert (project / ".mu" / "prompts" / "update.md").read_text(encoding="utf-8") == "updated\n"

    _git(project, "add", ".mu/prompts/update.md")
    _git(project, "commit", "-m", "promote")
    archive2 = tmp_path / "archive2"
    _archive_candidate(
        archive2,
        project,
        candidate_id="candidate2",
        changes={".mu/prompts/update.md": "again\n"},
    )
    promotion2 = prepare_dgm_promotion(archive2, project_root=project)
    (project / ".mu" / "prompts" / "update.md").write_text("local dirty\n", encoding="utf-8")

    with pytest.raises(DgmPromotionError, match="target path has local changes"):
        apply_dgm_promotion(promotion2)


def test_apply_rejects_non_git_repo(tmp_path):
    project = _project(tmp_path, git=False)
    archive = tmp_path / "archive"
    _archive_candidate(
        archive,
        project,
        candidate_id="candidate",
        changes={".mu/prompts/update.md": "updated\n"},
    )
    promotion = prepare_dgm_promotion(archive, project_root=project)

    with pytest.raises(DgmPromotionError, match="git worktree"):
        apply_dgm_promotion(promotion)


def test_apply_rejects_failing_preflight_and_keeps_project_unchanged(tmp_path):
    project = _project(tmp_path, failing_test=True)
    archive = tmp_path / "archive"
    _archive_candidate(
        archive,
        project,
        candidate_id="candidate",
        changes={".mu/prompts/update.md": "updated\n"},
    )
    promotion = prepare_dgm_promotion(archive, project_root=project)

    with pytest.raises(DgmPromotionError, match="preflight pytest failed"):
        apply_dgm_promotion(promotion)

    assert (project / ".mu" / "prompts" / "update.md").read_text(encoding="utf-8") == "old update\n"


def test_apply_rejects_promotion_artifact_secret_scan(tmp_path):
    project = _project(tmp_path)
    archive = tmp_path / "archive"
    secret = "sk-secretvalue123"
    _archive_candidate(
        archive,
        project,
        candidate_id="candidate",
        changes={".mu/prompts/update.md": secret + "\n"},
    )

    promotion = prepare_dgm_promotion(archive, project_root=project, env={"MU_API_KEY": secret})

    assert promotion.secret_scan["passed"] is False
    assert secret not in Path(promotion.patch_file).read_text(encoding="utf-8")
    with pytest.raises(DgmPromotionError, match="secret scan"):
        apply_dgm_promotion(promotion, env={"MU_API_KEY": secret})
