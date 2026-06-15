"""Automatic DGM-lite candidate generation (M4.4).

This module keeps automation narrow: generate prompt-snippet candidates, run
the existing DGM eval/archive path for each candidate, and write an auto-run
summary. Promotion remains explicit through `mu.dgm_promote`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from .dgm import (
    DgmArchiveEntry,
    DgmConfigError,
    GenerateCommandBuilder,
    read_archive,
    run_dgm_candidate,
)
from .eval import (
    AgentCommandBuilder,
    EvalSuite,
    basic_coding_suite,
    build_agent_env,
    default_project_root,
    missing_model_env,
    redact_secrets,
    scan_eval_artifacts_for_secrets,
)


@dataclass
class DgmAutoCandidate:
    index: int
    candidate_id: str
    description: str
    generated: bool
    passed: int = 0
    total: int = 0
    score: float = 0.0
    changed_paths: list[str] = field(default_factory=list)
    candidate_workspace: str | None = None
    eval_run_dir: str | None = None
    error: str | None = None


@dataclass
class DgmAutoRun:
    id: str
    archive_dir: str
    project_root: str
    description: str
    requested_count: int
    generated_count: int
    passed_count: int
    best_candidate_id: str | None
    candidates: list[DgmAutoCandidate]
    summary_json_file: str
    summary_md_file: str
    secret_scan: dict[str, Any] = field(default_factory=dict)

    @property
    def secret_scan_passed(self) -> bool:
        return bool(self.secret_scan.get("passed", True))


def run_dgm_auto(
    *,
    description: str,
    count: int = 3,
    project_root: str | Path | None = None,
    archive_dir: str | Path | None = None,
    suite: EvalSuite | None = None,
    agent_cmd_builder: AgentCommandBuilder | None = None,
    generate_cmd_builder: GenerateCommandBuilder | None = None,
    agent_args: Sequence[str] | None = None,
    env: dict[str, str] | None = None,
    require_model_env: bool = True,
    permission: str = "allow",
) -> DgmAutoRun:
    """Generate `count` prompt-snippet candidates and archive their evals."""
    if count < 1:
        raise DgmConfigError("count must be >= 1")
    project = (Path(project_root) if project_root is not None else default_project_root()).resolve()
    archive = (Path(archive_dir) if archive_dir is not None else project / "dgm_archive").resolve()
    source_env = env or os.environ.copy()
    agent_env = build_agent_env(project, source_env=source_env)
    if require_model_env:
        missing = missing_model_env(agent_env)
        if missing:
            raise DgmConfigError(f"Missing environment variables: {', '.join(missing)}")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    slug = _slug(description)
    run_id = f"auto-{timestamp}-{slug}"
    run_dir = archive / "auto-runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    candidates: list[DgmAutoCandidate] = []
    eval_suite = suite or basic_coding_suite()
    for index in range(1, count + 1):
        candidate_id = f"auto-{timestamp}-{index:02d}-{slug}"
        candidate_description = f"{description} (auto {index}/{count})"
        try:
            entry = run_dgm_candidate(
                source_type="generate",
                generate_prompt=_generation_prompt(description, index, count),
                description=candidate_description,
                candidate_id=candidate_id,
                project_root=project,
                archive_dir=archive,
                suite=eval_suite,
                agent_cmd_builder=agent_cmd_builder,
                generate_cmd_builder=generate_cmd_builder,
                agent_args=agent_args,
                env=source_env,
                require_model_env=require_model_env,
                permission=permission,
                candidate_scope="prompt",
            )
        except DgmConfigError as e:
            candidates.append(
                DgmAutoCandidate(
                    index=index,
                    candidate_id=candidate_id,
                    description=candidate_description,
                    generated=False,
                    error=str(e),
                )
            )
            continue
        candidates.append(_candidate_from_entry(index, candidate_description, entry))

    run = _build_run(
        run_id,
        archive,
        project,
        description,
        count,
        candidates,
        run_dir,
        source_env,
    )
    _write_auto_summary(run, source_env)
    run.secret_scan = scan_eval_artifacts_for_secrets(run_dir, source_env)
    _write_auto_summary(run, source_env)
    run.secret_scan = scan_eval_artifacts_for_secrets(run_dir, source_env)
    _write_auto_summary(run, source_env)
    return run


def _candidate_from_entry(
    index: int,
    description: str,
    entry: DgmArchiveEntry,
) -> DgmAutoCandidate:
    return DgmAutoCandidate(
        index=index,
        candidate_id=entry.id,
        description=description,
        generated=True,
        passed=entry.passed,
        total=entry.total,
        score=entry.score,
        changed_paths=list(entry.changed_paths),
        candidate_workspace=entry.candidate_workspace,
        eval_run_dir=entry.eval_run_dir,
    )


def _build_run(
    run_id: str,
    archive: Path,
    project: Path,
    description: str,
    requested_count: int,
    candidates: list[DgmAutoCandidate],
    run_dir: Path,
    env: dict[str, str],
) -> DgmAutoRun:
    entries = read_archive(archive)
    best = next((e.id for e in entries if e.is_best), None)
    generated = [c for c in candidates if c.generated]
    passed = [
        c for c in generated
        if c.total > 0 and c.passed == c.total
    ]
    summary_json = run_dir / "summary.json"
    summary_md = run_dir / "summary.md"
    return DgmAutoRun(
        id=run_id,
        archive_dir=str(archive),
        project_root=str(project),
        description=description,
        requested_count=requested_count,
        generated_count=len(generated),
        passed_count=len(passed),
        best_candidate_id=best,
        candidates=candidates,
        summary_json_file=str(summary_json),
        summary_md_file=str(summary_md),
        secret_scan={},
    )


def _write_auto_summary(run: DgmAutoRun, env: dict[str, str]) -> None:
    json_file = Path(run.summary_json_file)
    md_file = Path(run.summary_md_file)
    json_file.parent.mkdir(parents=True, exist_ok=True)
    json_file.write_text(
        redact_secrets(json.dumps(asdict(run), ensure_ascii=False, indent=2), env),
        encoding="utf-8",
    )
    lines = [
        "# μ DGM Auto Run",
        "",
        f"- Run id: `{run.id}`",
        f"- Archive: `{run.archive_dir}`",
        f"- Project root: `{run.project_root}`",
        f"- Description: {run.description}",
        f"- Generated: {run.generated_count}/{run.requested_count}",
        f"- Passed: {run.passed_count}/{run.generated_count}",
        f"- Best candidate: `{run.best_candidate_id or ''}`",
        f"- Secret scan: {'PASS' if not run.secret_scan or run.secret_scan.get('passed') else 'FAIL'}",
        "",
        "| index | candidate | generated | passed | score | eval run | error |",
        "|---:|---|---:|---:|---:|---|---|",
    ]
    for c in run.candidates:
        lines.append(
            f"| {c.index} | `{c.candidate_id}` | {'yes' if c.generated else 'no'} | "
            f"{c.passed}/{c.total} | {c.score:.4f} | `{c.eval_run_dir or ''}` | "
            f"{(c.error or '').replace('|', '/')} |"
        )
    if run.secret_scan and not run.secret_scan.get("passed", False):
        lines.extend(["", "## Secret scan findings", ""])
        for hit in run.secret_scan.get("hits", []):
            lines.append(f"- `{hit.get('path')}` ({hit.get('kind')})")
    md_file.write_text(redact_secrets("\n".join(lines) + "\n", env), encoding="utf-8")


def _generation_prompt(description: str, index: int, count: int) -> str:
    goal = description.strip() or "improve μ on the basic coding eval suite"
    return (
        f"Generate prompt-snippet candidate {index}/{count}.\n"
        f"Goal: {goal}\n\n"
        "Create or edit a concise Markdown or text file under `.mu/prompts/` only. "
        "The snippet should give general, reusable guidance for coding-agent behavior "
        "and should not mention secrets, API keys, paths outside the candidate workspace, "
        "or one-off eval answers. Do not modify core code, tests, extensions, meta-tools, "
        "README, or docs."
    )


def _slug(text: str) -> str:
    raw = text.strip() or "candidate"
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in raw).strip("-")
    return "-".join(part for part in slug.split("-") if part)[:32] or "candidate"


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m mu.dgm_auto",
        description="Generate and evaluate automatic prompt-snippet DGM candidates",
    )
    parser.add_argument("--archive-dir", default="dgm_archive", help="archive output directory")
    parser.add_argument("--project-root", default=".", help="source repository root")
    parser.add_argument("--count", type=int, default=3, help="number of candidates to generate")
    parser.add_argument("--description", required=True, help="candidate generation goal")
    parser.add_argument("--permission", choices=["allow", "readonly", "workspace"], default="allow")
    parser.add_argument("--timeout", type=float, default=360.0, help="per eval task timeout")
    return parser.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> int:
    ns = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        run = run_dgm_auto(
            description=ns.description,
            count=ns.count,
            project_root=ns.project_root,
            archive_dir=ns.archive_dir,
            suite=basic_coding_suite(timeout_seconds=ns.timeout),
            permission=ns.permission,
            require_model_env=True,
        )
    except DgmConfigError as e:
        print(str(e), file=sys.stderr)
        return 2
    print(f"Auto run: {run.id}")
    print(f"Generated: {run.generated_count}/{run.requested_count}")
    print(f"Passed: {run.passed_count}/{run.generated_count}")
    print(f"Best candidate: {run.best_candidate_id or ''}")
    print(f"Summary: {run.summary_md_file}")
    print(f"Secret scan: {'PASS' if run.secret_scan_passed else 'FAIL'}")
    return 0 if run.generated_count > 0 and run.secret_scan_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
