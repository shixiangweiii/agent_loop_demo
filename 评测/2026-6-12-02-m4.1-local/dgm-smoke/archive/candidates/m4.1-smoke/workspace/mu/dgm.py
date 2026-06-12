"""DGM-lite foundation for μ (M4.0).

Candidates are evaluated in copied workspaces and then archived. Passing
candidates are never applied back to the source repository automatically.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Sequence

from .eval import (
    AgentCommandBuilder,
    EvalConfigError,
    EvalRun,
    EvalSuite,
    basic_coding_suite,
    build_agent_env,
    default_project_root,
    redact_secrets,
    run_eval_suite,
)


class DgmConfigError(RuntimeError):
    """DGM-lite candidate configuration is invalid."""


@dataclass
class DgmArchiveEntry:
    id: str
    parent_id: str | None
    description: str
    source_type: str
    source_path: str | None
    changed_paths: list[str]
    candidate_workspace: str
    eval_run_dir: str
    passed: int
    total: int
    score: float
    duration_seconds: float
    total_tokens: int
    is_best: bool
    created_at: str


_IGNORE_NAMES = {
    ".git", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".idea", ".claude", "mu.egg-info", "eval_runs", "dgm_archive",
    "测试输出", "评测",
}
_PROMPT_SUFFIXES = {".md", ".txt"}


def run_dgm_candidate(
    *,
    source_type: str,
    source_path: str | Path | None = None,
    generate_prompt: str | None = None,
    description: str = "",
    parent_id: str | None = None,
    candidate_id: str | None = None,
    project_root: str | Path | None = None,
    archive_dir: str | Path | None = None,
    suite: EvalSuite | None = None,
    agent_cmd_builder: AgentCommandBuilder | None = None,
    agent_args: Sequence[str] | None = None,
    env: dict[str, str] | None = None,
    require_model_env: bool = True,
    permission: str = "allow",
) -> DgmArchiveEntry:
    project = Path(project_root) if project_root is not None else default_project_root()
    archive = Path(archive_dir) if archive_dir is not None else project / "dgm_archive"
    archive.mkdir(parents=True, exist_ok=True)

    cid = candidate_id or _new_candidate_id(description or source_type)
    candidate_root = archive / "candidates" / cid / "workspace"
    if candidate_root.exists():
        raise DgmConfigError(f"candidate workspace already exists: {candidate_root}")
    _copy_project(project, candidate_root)

    changed_paths = _apply_candidate(
        candidate_root,
        source_type=source_type,
        source_path=Path(source_path) if source_path is not None else None,
        generate_prompt=generate_prompt,
        env=env,
        archive_dir=archive,
        candidate_id=cid,
    )
    if permission != "allow" and any(_is_extension_path(p) for p in changed_paths):
        raise DgmConfigError(
            f"extension candidates cannot run under permission={permission}; "
            "use permission=allow or provide a prompt-only candidate"
        )

    eval_args = list(agent_args or [])
    if "--permission" not in eval_args:
        eval_args.extend(["--permission", permission])
    run = run_eval_suite(
        suite or basic_coding_suite(),
        run_root=archive / "runs" / cid,
        project_root=candidate_root,
        agent_cmd_builder=agent_cmd_builder,
        agent_args=eval_args,
        env=env,
        extra_env={
            "MU_EXT_DIR": str(candidate_root / ".mu" / "extensions"),
            "MU_PROMPT_SNIPPET_DIR": str(candidate_root / ".mu" / "prompts"),
        },
        require_model_env=require_model_env,
    )
    entry = _entry_from_run(
        cid,
        parent_id,
        description,
        source_type,
        str(source_path) if source_path is not None else None,
        changed_paths,
        candidate_root,
        run,
    )
    _append_archive_entry(archive, entry, env or os.environ.copy())
    return entry


def read_archive(archive_dir: str | Path) -> list[DgmArchiveEntry]:
    path = Path(archive_dir) / "archive.jsonl"
    if not path.exists():
        return []
    entries: list[DgmArchiveEntry] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(DgmArchiveEntry(**json.loads(line)))
    best = _best_id(entries)
    for e in entries:
        e.is_best = e.id == best
    return entries


def _apply_candidate(
    candidate_root: Path,
    *,
    source_type: str,
    source_path: Path | None,
    generate_prompt: str | None,
    env: dict[str, str] | None,
    archive_dir: Path,
    candidate_id: str,
) -> list[str]:
    if source_type == "dir":
        if source_path is None:
            raise DgmConfigError("candidate dir source requires source_path")
        return _overlay_candidate_dir(candidate_root, source_path)
    if source_type == "patch":
        if source_path is None:
            raise DgmConfigError("patch source requires source_path")
        return _apply_patch_candidate(candidate_root, source_path)
    if source_type == "generate":
        if not generate_prompt:
            raise DgmConfigError("generate source requires generate_prompt")
        return _generate_candidate(
            candidate_root, generate_prompt, env, archive_dir, candidate_id
        )
    raise DgmConfigError(f"unknown candidate source type: {source_type}")


def _copy_project(project: Path, candidate_root: Path) -> None:
    def ignore(_dir: str, names: list[str]) -> list[str]:
        return [n for n in names if n in _IGNORE_NAMES or n.endswith(".egg-info")]

    shutil.copytree(project, candidate_root, ignore=ignore)


def _overlay_candidate_dir(candidate_root: Path, source_dir: Path) -> list[str]:
    if not source_dir.exists() or not source_dir.is_dir():
        raise DgmConfigError(f"candidate dir not found: {source_dir}")
    changed: list[str] = []
    for p in sorted(source_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = _validate_candidate_path(p.relative_to(source_dir))
        target = candidate_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
        changed.append(rel)
    return changed


def _apply_patch_candidate(candidate_root: Path, patch_file: Path) -> list[str]:
    if not patch_file.exists() or not patch_file.is_file():
        raise DgmConfigError(f"patch file not found: {patch_file}")
    patch_text = patch_file.read_text(encoding="utf-8", errors="replace")
    changed = sorted({_validate_candidate_path(p) for p in _paths_from_patch(patch_text)})
    if not changed:
        raise DgmConfigError("patch does not touch any candidate paths")
    completed = subprocess.run(
        ["git", "apply", str(patch_file)],
        cwd=candidate_root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise DgmConfigError(f"git apply failed: {completed.stderr.strip()}")
    return changed


def _generate_candidate(
    candidate_root: Path,
    prompt: str,
    env: dict[str, str] | None,
    archive_dir: Path,
    candidate_id: str,
) -> list[str]:
    before = _snapshot(candidate_root)
    generation_dir = archive_dir / "candidates" / candidate_id / "generation"
    generation_dir.mkdir(parents=True, exist_ok=True)
    gen_env = build_agent_env(
        candidate_root,
        source_env=env,
        extra_env={"MU_SESSION_DIR": str(generation_dir / "sessions")},
    )
    missing = []
    if not gen_env.get("MU_MODEL"):
        missing.append("MU_MODEL")
    if not (gen_env.get("MU_API_KEY") or gen_env.get("OPENAI_API_KEY")):
        missing.append("MU_API_KEY or OPENAI_API_KEY")
    if missing:
        raise DgmConfigError(f"cannot generate candidate; missing {', '.join(missing)}")
    task = (
        "You are generating a DGM-lite candidate. Only create or edit files under "
        ".mu/extensions, .mu/prompts, or extensions. Do not edit mu core files.\n\n"
        + prompt
    )
    completed = subprocess.run(
        [sys.executable, "-m", "mu", task],
        cwd=candidate_root,
        env=gen_env,
        capture_output=True,
        text=True,
        timeout=360,
    )
    (generation_dir / "stdout.txt").write_text(
        redact_secrets(completed.stdout, gen_env), encoding="utf-8"
    )
    (generation_dir / "stderr.txt").write_text(
        redact_secrets(completed.stderr, gen_env), encoding="utf-8"
    )
    if completed.returncode != 0:
        raise DgmConfigError(f"candidate generation failed with exit code {completed.returncode}")
    after = _snapshot(candidate_root)
    changed = sorted(
        p for p in set(before) | set(after)
        if before.get(p) != after.get(p)
    )
    return [_validate_candidate_path(p) for p in changed]


def _paths_from_patch(text: str) -> list[str]:
    paths: list[str] = []
    for line in text.splitlines():
        if line.startswith("+++ ") or line.startswith("--- "):
            raw = line[4:].strip()
            if raw == "/dev/null":
                continue
            if raw.startswith("a/") or raw.startswith("b/"):
                raw = raw[2:]
            paths.append(raw)
    return paths


def _validate_candidate_path(path: str | Path) -> str:
    p = Path(path)
    if p.is_absolute():
        raise DgmConfigError(f"candidate path must be relative: {path}")
    parts = p.parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise DgmConfigError(f"invalid candidate path: {path}")
    rel = p.as_posix()
    suffix = p.suffix
    if rel.startswith(".mu/extensions/") and suffix == ".py":
        return rel
    if rel.startswith(".mu/prompts/") and suffix in _PROMPT_SUFFIXES:
        return rel
    if rel.startswith("extensions/") and suffix in {".py", ".md", ".txt"}:
        return rel
    raise DgmConfigError(
        f"candidate path is outside the M4.0 scope: {rel}; allowed: "
        ".mu/extensions/*.py, .mu/prompts/*.{md,txt}, extensions/*"
    )


def _is_extension_path(rel: str) -> bool:
    return rel.startswith(".mu/extensions/") or (
        rel.startswith("extensions/") and rel.endswith(".py")
    )


def _snapshot(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in _IGNORE_NAMES for part in p.relative_to(root).parts):
            continue
        rel = p.relative_to(root).as_posix()
        out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _entry_from_run(
    candidate_id: str,
    parent_id: str | None,
    description: str,
    source_type: str,
    source_path: str | None,
    changed_paths: list[str],
    candidate_root: Path,
    run: EvalRun,
) -> DgmArchiveEntry:
    duration = round(sum(r.agent_duration_seconds for r in run.results), 2)
    tokens = sum(int(r.attribution.get("total_tokens", 0)) for r in run.results)
    total = max(run.total, 1)
    return DgmArchiveEntry(
        id=candidate_id,
        parent_id=parent_id,
        description=description,
        source_type=source_type,
        source_path=source_path,
        changed_paths=changed_paths,
        candidate_workspace=str(candidate_root),
        eval_run_dir=run.run_dir,
        passed=run.passed,
        total=run.total,
        score=round(run.passed / total, 4),
        duration_seconds=duration,
        total_tokens=tokens,
        is_best=False,
        created_at=datetime.now().isoformat(timespec="seconds"),
    )


def _append_archive_entry(
    archive_dir: Path,
    entry: DgmArchiveEntry,
    env: dict[str, str],
) -> None:
    archive_dir.mkdir(parents=True, exist_ok=True)
    existing = read_archive(archive_dir)
    best = _best_id([*existing, entry])
    entry.is_best = entry.id == best
    archive_jsonl = archive_dir / "archive.jsonl"
    with archive_jsonl.open("a", encoding="utf-8") as f:
        f.write(redact_secrets(json.dumps(asdict(entry), ensure_ascii=False), env) + "\n")
    write_archive_summary(archive_dir, [*existing, entry], env)


def write_archive_summary(
    archive_dir: Path,
    entries: list[DgmArchiveEntry],
    env: dict[str, str] | None = None,
) -> tuple[Path, Path]:
    best = _best_id(entries)
    current: list[dict] = []
    for e in entries:
        d = asdict(e)
        d["is_best"] = e.id == best
        current.append(d)
    summary_json = archive_dir / "latest-summary.json"
    summary_md = archive_dir / "latest-summary.md"
    data = {"best_candidate_id": best, "total": len(entries), "entries": current}
    source_env = env or os.environ
    summary_json.write_text(
        redact_secrets(json.dumps(data, ensure_ascii=False, indent=2), source_env),
        encoding="utf-8",
    )

    lines = [
        "# μ DGM-lite Archive",
        "",
        f"- 候选数：{len(entries)}",
        f"- 当前 best：`{best or ''}`",
        "",
        "| candidate | parent | score | passed | duration(s) | tokens | best | description |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for d in sorted(current, key=_rank_dict, reverse=True):
        lines.append(
            f"| `{d['id']}` | `{d.get('parent_id') or ''}` | {d['score']:.4f} | "
            f"{d['passed']}/{d['total']} | {d['duration_seconds']} | {d['total_tokens']} | "
            f"{'yes' if d['is_best'] else ''} | {d.get('description') or ''} |"
        )
    summary_md.write_text(redact_secrets("\n".join(lines), source_env), encoding="utf-8")
    return summary_json, summary_md


def _best_id(entries: list[DgmArchiveEntry]) -> str | None:
    if not entries:
        return None
    return sorted(entries, key=lambda e: (e.passed, -e.duration_seconds, -e.total_tokens, e.id), reverse=True)[0].id


def _rank_dict(d: dict) -> tuple:
    return (d["passed"], -d["duration_seconds"], -d["total_tokens"], d["id"])


def _new_candidate_id(description: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in description).strip("-")
    slug = "-".join(part for part in slug.split("-") if part)[:32] or "candidate"
    return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + slug


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m mu.dgm", description="Run a DGM-lite candidate")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--candidate-dir", help="overlay directory containing allowed candidate files")
    g.add_argument("--patch", help="patch file touching only allowed candidate files")
    g.add_argument("--generate", help="ask μ to generate a candidate in the copied workspace")
    p.add_argument("--archive-dir", default="dgm_archive", help="archive output directory")
    p.add_argument("--project-root", default=".", help="source repository root")
    p.add_argument("--candidate-id", help="stable candidate id")
    p.add_argument("--parent", dest="parent_id", help="parent candidate id")
    p.add_argument("--description", default="", help="candidate description")
    p.add_argument("--permission", choices=["allow", "readonly", "workspace"], default="allow")
    p.add_argument("--timeout", type=float, default=360.0, help="per eval task timeout")
    return p.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> int:
    ns = _parse_args(sys.argv[1:] if argv is None else argv)
    if ns.candidate_dir:
        source_type, source_path, generate_prompt = "dir", ns.candidate_dir, None
    elif ns.patch:
        source_type, source_path, generate_prompt = "patch", ns.patch, None
    else:
        source_type, source_path, generate_prompt = "generate", None, ns.generate
    try:
        entry = run_dgm_candidate(
            source_type=source_type,
            source_path=source_path,
            generate_prompt=generate_prompt,
            description=ns.description,
            parent_id=ns.parent_id,
            candidate_id=ns.candidate_id,
            project_root=ns.project_root,
            archive_dir=ns.archive_dir,
            suite=basic_coding_suite(timeout_seconds=ns.timeout),
            permission=ns.permission,
            require_model_env=True,
        )
    except (DgmConfigError, EvalConfigError) as e:
        print(str(e), file=sys.stderr)
        return 2
    print(f"Candidate archived: {entry.id}")
    print(f"Score: {entry.passed}/{entry.total}")
    print(f"Archive: {Path(ns.archive_dir).resolve()}")
    return 0 if entry.passed == entry.total else 1


if __name__ == "__main__":
    raise SystemExit(main())
