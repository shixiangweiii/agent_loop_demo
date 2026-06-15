"""Guarded DGM candidate promotion (M4.3).

Promotion turns an already-evaluated DGM-lite candidate into an auditable patch.
By default it writes artifacts only; applying back to the source repository is
explicit and gated by git path checks plus a sandbox pytest preflight.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from .dgm import DgmArchiveEntry, _copy_project, _validate_candidate_path, read_archive
from .eval import redact_secrets, scan_eval_artifacts_for_secrets


class DgmPromotionError(RuntimeError):
    """A DGM candidate cannot be promoted safely."""


@dataclass
class DgmPromotion:
    candidate_id: str
    archive_dir: str
    project_root: str
    promotion_dir: str
    candidate_workspace: str
    changed_paths: list[str]
    patch_file: str
    summary_json_file: str
    summary_md_file: str
    secret_scan: dict[str, Any] = field(default_factory=dict)
    preflight_output_file: str | None = None
    backup_dir: str | None = None
    applied: bool = False
    apply_error: str | None = None


_SK_PATTERN = re.compile(r"sk-[A-Za-z0-9][A-Za-z0-9_-]{8,}")


def prepare_dgm_promotion(
    archive_dir: str | Path,
    *,
    candidate_id: str = "best",
    project_root: str | Path | None = None,
    promotion_dir: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> DgmPromotion:
    """Prepare promotion artifacts for a passing archive candidate.

    This function never mutates the project root. It writes a promotion patch
    and summaries under the archive's promotions directory unless overridden.
    """
    archive = Path(archive_dir).resolve()
    project = (Path(project_root) if project_root is not None else Path.cwd()).resolve()
    source_env = env or os.environ.copy()
    entry = _select_candidate(archive, candidate_id)
    _validate_promotable_entry(entry, archive)

    candidate_workspace = _resolve_recorded_path(entry.candidate_workspace, archive)
    if not candidate_workspace.exists() or not candidate_workspace.is_dir():
        raise DgmPromotionError(f"candidate workspace not found: {candidate_workspace}")
    changed_paths = [_validate_candidate_path(p) for p in entry.changed_paths]
    if not changed_paths:
        raise DgmPromotionError(f"candidate {entry.id} has no changed paths")

    target_dir = (
        Path(promotion_dir)
        if promotion_dir is not None
        else archive / "promotions" / f"{datetime.now():%Y%m%d-%H%M%S-%f}-{entry.id}"
    ).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    patch_file = target_dir / "promotion.patch"
    summary_json = target_dir / "promotion-summary.json"
    summary_md = target_dir / "promotion-summary.md"

    patch_text = _build_patch(project, candidate_workspace, changed_paths)
    memory_scan = _scan_text_for_secrets(patch_text, source_env, path="promotion.patch")
    safe_patch = _redact_patterns(redact_secrets(patch_text, source_env)) if memory_scan["hits"] else patch_text
    patch_file.write_text(safe_patch, encoding="utf-8")

    disk_scan = scan_eval_artifacts_for_secrets(target_dir, source_env)
    secret_scan = _merge_secret_scans(target_dir, memory_scan, disk_scan)
    promotion = DgmPromotion(
        candidate_id=entry.id,
        archive_dir=str(archive),
        project_root=str(project),
        promotion_dir=str(target_dir),
        candidate_workspace=str(candidate_workspace),
        changed_paths=changed_paths,
        patch_file=str(patch_file),
        summary_json_file=str(summary_json),
        summary_md_file=str(summary_md),
        secret_scan=secret_scan,
    )
    _write_promotion_summary(promotion, source_env)

    # Include summaries themselves in the final scan, while preserving any
    # in-memory patch hits that were redacted before persistence.
    disk_scan = scan_eval_artifacts_for_secrets(target_dir, source_env)
    promotion.secret_scan = _merge_secret_scans(target_dir, memory_scan, disk_scan)
    _write_promotion_summary(promotion, source_env)
    return promotion


def apply_dgm_promotion(
    promotion: DgmPromotion,
    *,
    project_root: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> DgmPromotion:
    """Apply a prepared promotion after git/path/preflight safety checks."""
    project = (Path(project_root) if project_root is not None else Path(promotion.project_root)).resolve()
    source_env = env or os.environ.copy()
    promotion.project_root = str(project)
    promotion.secret_scan = _merge_secret_scans(
        Path(promotion.promotion_dir),
        promotion.secret_scan,
        scan_eval_artifacts_for_secrets(promotion.promotion_dir, source_env),
    )
    if not promotion.secret_scan.get("passed", False):
        raise DgmPromotionError("promotion artifacts failed secret scan; refusing to apply")
    _ensure_git_repo(project)
    _ensure_changed_paths_clean(project, promotion.changed_paths)
    _run_preflight(promotion, project, source_env)

    backup_dir = Path(promotion.promotion_dir) / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    promotion.backup_dir = str(backup_dir.resolve())
    _backup_targets(project, promotion.changed_paths, backup_dir)
    try:
        _copy_candidate_changes(Path(promotion.candidate_workspace), project, promotion.changed_paths)
    except Exception as e:  # noqa: BLE001 - restore before surfacing
        promotion.apply_error = str(e)
        _restore_backups(project, backup_dir)
        _write_promotion_summary(promotion, source_env)
        raise DgmPromotionError(f"failed to apply promotion; restored backups: {e}") from e

    promotion.applied = True
    _write_promotion_summary(promotion, source_env)
    return promotion


def _select_candidate(archive: Path, candidate_id: str) -> DgmArchiveEntry:
    entries = read_archive(archive)
    if not entries:
        raise DgmPromotionError(f"no DGM archive entries found in {archive}")
    if candidate_id == "best":
        for entry in entries:
            if entry.is_best:
                return entry
        raise DgmPromotionError("archive has no best candidate")
    for entry in entries:
        if entry.id == candidate_id:
            return entry
    raise DgmPromotionError(f"candidate not found: {candidate_id}")


def _validate_promotable_entry(entry: DgmArchiveEntry, archive: Path) -> None:
    if entry.total <= 0:
        raise DgmPromotionError(f"candidate {entry.id} has zero eval tasks")
    if entry.passed != entry.total:
        raise DgmPromotionError(f"candidate {entry.id} did not pass eval: {entry.passed}/{entry.total}")
    eval_run = _resolve_recorded_path(entry.eval_run_dir, archive)
    summary_file = eval_run / "summary.json"
    if not summary_file.exists():
        raise DgmPromotionError(f"eval summary missing for candidate {entry.id}: {summary_file}")
    try:
        summary = json.loads(summary_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise DgmPromotionError(f"eval summary is invalid JSON: {summary_file}: {e}") from e
    scan = summary.get("secret_scan") or {}
    if not scan.get("passed", False):
        raise DgmPromotionError(f"candidate {entry.id} eval secret scan did not pass")


def _resolve_recorded_path(value: str, archive: Path) -> Path:
    p = Path(value)
    if p.is_absolute():
        return p.resolve()
    cwd_candidate = p.resolve()
    if cwd_candidate.exists():
        return cwd_candidate
    return (archive.parent / p).resolve()


def _build_patch(project: Path, candidate_workspace: Path, changed_paths: list[str]) -> str:
    parts: list[str] = []
    for rel in changed_paths:
        old_file = project / rel
        new_file = candidate_workspace / rel
        old_exists = old_file.exists()
        new_exists = new_file.exists()
        old_lines = _read_lines(old_file) if old_exists else []
        new_lines = _read_lines(new_file) if new_exists else []
        parts.append(f"diff --git a/{rel} b/{rel}\n")
        if not old_exists and new_exists:
            parts.append("new file mode 100644\n")
        elif old_exists and not new_exists:
            parts.append("deleted file mode 100644\n")
        fromfile = f"a/{rel}" if old_exists else "/dev/null"
        tofile = f"b/{rel}" if new_exists else "/dev/null"
        parts.extend(
            difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile=fromfile,
                tofile=tofile,
                lineterm="",
            )
        )
        if parts and not parts[-1].endswith("\n"):
            parts[-1] += "\n"
        if not parts[-1].endswith("\n\n"):
            parts.append("\n")
    return "".join(parts)


def _read_lines(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return text.splitlines(keepends=True)


def _scan_text_for_secrets(text: str, env: dict[str, str], *, path: str) -> dict[str, Any]:
    hits: list[dict[str, str]] = []
    for name, value in env.items():
        if _is_secret_env_value(name, value) and value in text:
            hits.append({"path": path, "kind": f"env:{name}"})
    if _SK_PATTERN.search(text):
        hits.append({"path": path, "kind": "pattern:sk"})
    return {"root": "<memory>", "passed": not hits, "hits": hits, "ignored": []}


def _is_secret_env_value(name: str, value: str) -> bool:
    upper = name.upper()
    return bool(value) and len(value) >= 8 and (
        "API_KEY" in upper or "TOKEN" in upper or "SECRET" in upper
    )


def _redact_patterns(text: str) -> str:
    return _SK_PATTERN.sub("[REDACTED:pattern:sk]", text)


def _merge_secret_scans(root: Path, *scans: dict[str, Any]) -> dict[str, Any]:
    hits: list[dict[str, str]] = []
    ignored: list[dict[str, str]] = []
    for scan in scans:
        hits.extend(scan.get("hits", []))
        ignored.extend(scan.get("ignored", []))
    return {
        "root": str(Path(root).resolve()),
        "passed": not hits,
        "hits": hits,
        "ignored": ignored,
    }


def _ensure_git_repo(project: Path) -> None:
    completed = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed.returncode != 0 or completed.stdout.strip() != "true":
        raise DgmPromotionError("--apply requires a git worktree")


def _ensure_changed_paths_clean(project: Path, changed_paths: list[str]) -> None:
    dirty: list[str] = []
    for rel in changed_paths:
        completed = subprocess.run(
            ["git", "status", "--porcelain", "--", rel],
            cwd=project,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if completed.returncode != 0:
            raise DgmPromotionError(f"git status failed for {rel}: {completed.stderr.strip()}")
        if completed.stdout.strip():
            dirty.append(rel)
    if dirty:
        raise DgmPromotionError("target path has local changes; refusing to apply: " + ", ".join(dirty))


def _run_preflight(promotion: DgmPromotion, project: Path, env: dict[str, str]) -> None:
    preflight_dir = Path(promotion.promotion_dir) / "preflight"
    workspace = preflight_dir / "workspace"
    if workspace.exists():
        shutil.rmtree(workspace)
    _copy_project(project, workspace)
    _copy_candidate_changes(Path(promotion.candidate_workspace), workspace, promotion.changed_paths)
    output_file = preflight_dir / "pytest-output.txt"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [_preferred_python(project), "-m", "pytest", "-q"],
        cwd=workspace,
        env={**os.environ, **env, "PYTHONPATH": str(workspace)},
        capture_output=True,
        text=True,
        timeout=600,
    )
    text = (
        "$ " + _preferred_python(project) + " -m pytest -q\n\n"
        + "[stdout]\n" + completed.stdout
        + "\n[stderr]\n" + completed.stderr
        + f"\n[exit code] {completed.returncode}\n"
    )
    output_file.write_text(redact_secrets(text, env), encoding="utf-8")
    promotion.preflight_output_file = str(output_file.resolve())
    if completed.returncode != 0:
        _write_promotion_summary(promotion, env)
        raise DgmPromotionError(f"promotion preflight pytest failed: {output_file}")


def _preferred_python(project: Path) -> str:
    venv_python = project / ".venv" / "bin" / "python"
    return str(venv_python) if venv_python.exists() else sys.executable


def _copy_candidate_changes(candidate_workspace: Path, target_root: Path, changed_paths: list[str]) -> None:
    for rel in changed_paths:
        source = candidate_workspace / rel
        target = target_root / rel
        if source.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        elif target.exists():
            target.unlink()


def _backup_targets(project: Path, changed_paths: list[str], backup_dir: Path) -> None:
    manifest: list[dict[str, Any]] = []
    for rel in changed_paths:
        source = project / rel
        target = backup_dir / rel
        if source.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            manifest.append({"path": rel, "existed": True})
        else:
            manifest.append({"path": rel, "existed": False})
    (backup_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _restore_backups(project: Path, backup_dir: Path) -> None:
    manifest_file = backup_dir / "manifest.json"
    if not manifest_file.exists():
        return
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    for item in manifest:
        rel = item["path"]
        target = project / rel
        backup = backup_dir / rel
        if item.get("existed"):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, target)
        elif target.exists():
            target.unlink()


def _write_promotion_summary(promotion: DgmPromotion, env: dict[str, str]) -> None:
    data = asdict(promotion)
    json_file = Path(promotion.summary_json_file)
    md_file = Path(promotion.summary_md_file)
    json_file.write_text(
        redact_secrets(json.dumps(data, ensure_ascii=False, indent=2), env),
        encoding="utf-8",
    )
    lines = [
        "# μ DGM Promotion",
        "",
        f"- Candidate: `{promotion.candidate_id}`",
        f"- Project root: `{promotion.project_root}`",
        f"- Promotion dir: `{promotion.promotion_dir}`",
        f"- Patch: `{promotion.patch_file}`",
        f"- Changed paths: {len(promotion.changed_paths)}",
        f"- Secret scan: {'PASS' if promotion.secret_scan.get('passed') else 'FAIL'}",
        f"- Applied: {'yes' if promotion.applied else 'no'}",
    ]
    if promotion.preflight_output_file:
        lines.append(f"- Preflight pytest: `{promotion.preflight_output_file}`")
    if promotion.apply_error:
        lines.append(f"- Apply error: `{promotion.apply_error}`")
    lines.extend(["", "## Changed Paths", ""])
    lines.extend(f"- `{p}`" for p in promotion.changed_paths)
    if not promotion.secret_scan.get("passed", False):
        lines.extend(["", "## Secret Scan Findings", ""])
        for hit in promotion.secret_scan.get("hits", []):
            lines.append(f"- `{hit.get('path')}` ({hit.get('kind')})")
    md_file.write_text(redact_secrets("\n".join(lines) + "\n", env), encoding="utf-8")


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m mu.dgm_promote",
        description="Prepare or apply a guarded DGM promotion",
    )
    parser.add_argument("--archive-dir", default="dgm_archive", help="DGM archive directory")
    parser.add_argument("--candidate", default="best", help="candidate id, or best")
    parser.add_argument("--project-root", default=".", help="source repository root")
    parser.add_argument("--promotion-dir", help="promotion artifact directory")
    parser.add_argument("--apply", action="store_true", help="apply after safety checks")
    return parser.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> int:
    ns = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        promotion = prepare_dgm_promotion(
            ns.archive_dir,
            candidate_id=ns.candidate,
            project_root=ns.project_root,
            promotion_dir=ns.promotion_dir,
        )
        if ns.apply:
            promotion = apply_dgm_promotion(promotion, project_root=ns.project_root)
    except DgmPromotionError as e:
        print(f"Promotion error: {e}", file=sys.stderr)
        return 1
    print(f"Promotion dir: {promotion.promotion_dir}")
    print(f"Patch: {promotion.patch_file}")
    print(f"Secret scan: {'PASS' if promotion.secret_scan.get('passed') else 'FAIL'}")
    print(f"Applied: {'yes' if promotion.applied else 'no'}")
    return 0 if promotion.secret_scan.get("passed", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
