"""Reusable eval runner for μ (M4.0).

This module turns the historical one-off scripts under `评测/` into a small
library API plus `python -m mu.eval`. It intentionally keeps validation outside
the agent: the agent may claim success, but validators decide pass/fail.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

Validator = Callable[[Path], tuple[int, str, list[str]]]
SetupFunc = Callable[[Path], tuple[str, Validator]]
AgentCommandBuilder = Callable[[Path, str], list[str]]


class EvalConfigError(RuntimeError):
    """Eval cannot run with the requested configuration."""


@dataclass
class EvalTask:
    name: str
    setup: SetupFunc
    timeout_seconds: float = 360.0


@dataclass
class EvalResult:
    name: str
    passed: bool
    agent_returncode: int | None
    agent_duration_seconds: float
    agent_timed_out: bool
    validation_returncode: int | None
    notes: list[str]
    workspace: str
    prompt_file: str
    stdout_file: str
    stderr_file: str
    validation_file: str
    attribution: dict[str, int | float] = field(default_factory=dict)


@dataclass
class EvalSuite:
    name: str
    tasks: list[EvalTask]


@dataclass
class EvalRun:
    suite_name: str
    run_dir: str
    model: str
    base_url: str
    api_key_recorded: bool
    results: list[EvalResult]
    summary_json_file: str
    summary_md_file: str
    secret_scan: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> int:
        return sum(r.passed for r in self.results)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def secret_scan_passed(self) -> bool:
        return bool(self.secret_scan.get("passed", True))


def default_project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _preferred_python(project_root: Path) -> str:
    venv_python = project_root / ".venv" / "bin" / "python"
    return str(venv_python) if venv_python.exists() else sys.executable


def default_agent_cmd_builder(
    project_root: Path,
    *,
    agent_args: Sequence[str] | None = None,
) -> AgentCommandBuilder:
    args = list(agent_args or [])
    python = _preferred_python(project_root)

    def build(_workspace: Path, prompt: str) -> list[str]:
        return [python, "-m", "mu", *args, prompt]

    return build


def redact_secrets(text: str, env: dict[str, str] | None = None) -> str:
    """Redact API keys from any text before it is persisted."""
    out = text
    source = env or os.environ
    for name, value in source.items():
        if _is_secret_env_value(name, value):
            out = out.replace(value, f"[REDACTED:{name}]")
    return out


_MIN_SECRET_VALUE_LENGTH = 8
_SECRET_PATTERN = re.compile(r"sk-[A-Za-z0-9][A-Za-z0-9_-]{8,}")
_WORKSPACE_IGNORE_REASON = "workspace source fixture is not a process artifact"
_CACHE_IGNORE_REASON = "bytecode/cache artifact"


def scan_eval_artifacts_for_secrets(
    root: str | Path,
    env: dict[str, str] | None = None,
    *,
    ignore_copied_fixtures: bool = True,
) -> dict[str, Any]:
    """Scan eval/DGM process artifacts for leaked secrets.

    By default this skips copied candidate/task workspaces, because those may
    contain source fixtures such as fake redaction keys. The scan is aimed at
    persisted process artifacts: stdout/stderr, validation logs, summaries and
    archive metadata.
    """
    root_path = Path(root).resolve()
    source = env or os.environ
    secret_values = [
        (name, value)
        for name, value in source.items()
        if _is_secret_env_value(name, value)
    ]
    hits: list[dict[str, str]] = []
    ignored: list[dict[str, str]] = []
    files = (
        [root_path]
        if root_path.is_file()
        else sorted(p for p in root_path.rglob("*") if p.is_file())
    )
    for path in files:
        rel = path.relative_to(root_path).as_posix() if path != root_path else path.name
        ignore_reason = _secret_scan_ignore_reason(path, root_path) if ignore_copied_fixtures else None
        if ignore_reason == _CACHE_IGNORE_REASON:
            ignored.append({"path": rel, "reason": ignore_reason})
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if ignore_reason == _WORKSPACE_IGNORE_REASON:
            found_env_secret = False
            for name, value in secret_values:
                if value in text:
                    hits.append({"path": rel, "kind": f"env:{name}"})
                    found_env_secret = True
                    break
            if not found_env_secret:
                ignored.append({"path": rel, "reason": ignore_reason})
            continue
        for name, value in secret_values:
            if value in text:
                hits.append({"path": rel, "kind": f"env:{name}"})
                break
        if _SECRET_PATTERN.search(text):
            hits.append({"path": rel, "kind": "pattern:sk"})
    return {
        "root": str(root_path),
        "passed": not hits,
        "hits": hits,
        "ignored": ignored,
    }


def _is_secret_env_name(name: str) -> bool:
    upper = name.upper()
    return "API_KEY" in upper or "TOKEN" in upper or "SECRET" in upper


def _is_secret_env_value(name: str, value: str) -> bool:
    return bool(value) and len(value) >= _MIN_SECRET_VALUE_LENGTH and _is_secret_env_name(name)


def _secret_scan_ignore_reason(path: Path, root: Path) -> str | None:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return None
    if "workspace" in parts:
        return _WORKSPACE_IGNORE_REASON
    if "__pycache__" in parts or path.suffix in {".pyc", ".pyo"}:
        return _CACHE_IGNORE_REASON
    return None


def build_agent_env(
    project_root: Path,
    *,
    source_env: dict[str, str] | None = None,
    extra_env: dict[str, str | None] | None = None,
) -> dict[str, str]:
    """Build a minimal subprocess env and point imports at project_root."""
    source = source_env or os.environ
    keep = {
        "PATH", "HOME", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL",
        "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE",
        "MU_BASE_URL", "MU_MODEL", "MU_API_KEY", "OPENAI_API_KEY",
        "MU_CODE_ACTION", "MU_PERMISSION", "MU_SANDBOX", "MU_DOCKER_IMAGE",
        "MU_EXT_DIR", "MU_PROMPT_SNIPPET_DIR", "MU_SESSION_DIR",
    }
    env = {k: v for k, v in source.items() if k in keep and v}
    py_parts = [str(project_root)]
    if source.get("PYTHONPATH"):
        py_parts.append(source["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(py_parts)

    ext_dir = project_root / ".mu" / "extensions"
    prompt_dir = project_root / ".mu" / "prompts"
    if ext_dir.exists() and "MU_EXT_DIR" not in env:
        env["MU_EXT_DIR"] = str(ext_dir)
    if prompt_dir.exists() and "MU_PROMPT_SNIPPET_DIR" not in env:
        env["MU_PROMPT_SNIPPET_DIR"] = str(prompt_dir)

    for key, value in (extra_env or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


def missing_model_env(env: dict[str, str]) -> list[str]:
    missing: list[str] = []
    if not env.get("MU_MODEL"):
        missing.append("MU_MODEL")
    if not (env.get("MU_API_KEY") or env.get("OPENAI_API_KEY")):
        missing.append("MU_API_KEY or OPENAI_API_KEY")
    return missing


def run_eval_suite(
    suite: EvalSuite,
    *,
    run_root: str | Path | None = None,
    project_root: str | Path | None = None,
    agent_cmd_builder: AgentCommandBuilder | None = None,
    agent_args: Sequence[str] | None = None,
    env: dict[str, str] | None = None,
    extra_env: dict[str, str | None] | None = None,
    selected_tasks: Sequence[str] | None = None,
    require_model_env: bool = True,
) -> EvalRun:
    project = (Path(project_root) if project_root is not None else default_project_root()).resolve()
    root = (Path(run_root) if run_root is not None else Path.cwd() / "eval_runs").resolve()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = (root / timestamp).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)

    agent_env = build_agent_env(project, source_env=env, extra_env=extra_env)
    agent_env.setdefault("MU_SESSION_DIR", str(run_dir / "sessions"))
    if require_model_env:
        missing = missing_model_env(agent_env)
        if missing:
            raise EvalConfigError(f"Missing environment variables: {', '.join(missing)}")
    builder = agent_cmd_builder or default_agent_cmd_builder(project, agent_args=agent_args)

    allowed = set(selected_tasks or [])
    tasks = [t for t in suite.tasks if not allowed or t.name in allowed]
    if not tasks:
        raise EvalConfigError("No eval tasks selected.")

    results = [
        run_eval_task(task, run_dir, builder, agent_env)
        for task in tasks
    ]
    # Write once before scanning so summary files themselves are included.
    summary_json, summary_md = write_eval_summary(run_dir, suite.name, results, agent_env)
    secret_scan = scan_eval_artifacts_for_secrets(run_dir, agent_env)
    summary_json, summary_md = write_eval_summary(
        run_dir, suite.name, results, agent_env, secret_scan=secret_scan
    )
    return EvalRun(
        suite_name=suite.name,
        run_dir=str(run_dir),
        model=agent_env.get("MU_MODEL", ""),
        base_url=agent_env.get("MU_BASE_URL", ""),
        api_key_recorded=False,
        results=results,
        summary_json_file=str(summary_json),
        summary_md_file=str(summary_md),
        secret_scan=secret_scan,
    )


def run_eval_task(
    task: EvalTask,
    run_dir: Path,
    agent_cmd_builder: AgentCommandBuilder,
    agent_env: dict[str, str],
) -> EvalResult:
    run_dir = run_dir.resolve()
    task_dir = (run_dir / task.name).resolve()
    workspace = (task_dir / "workspace").resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    prompt, validator = task.setup(workspace)

    prompt_file = task_dir / "task_prompt.txt"
    stdout_file = task_dir / "agent_stdout.txt"
    stderr_file = task_dir / "agent_stderr.txt"
    validation_file = task_dir / "validation.txt"
    prompt_file.write_text(redact_secrets(prompt, agent_env), encoding="utf-8")

    cmd = agent_cmd_builder(workspace, prompt)
    start = time.monotonic()
    timed_out = False
    try:
        completed = subprocess.run(
            cmd,
            cwd=workspace,
            env=agent_env,
            capture_output=True,
            text=True,
            timeout=task.timeout_seconds,
        )
        returncode: int | None = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        returncode = None
        stdout = _as_text(exc.stdout)
        stderr = _as_text(exc.stderr)
    duration = round(time.monotonic() - start, 2)

    safe_stdout = redact_secrets(_as_text(stdout), agent_env)
    safe_stderr = redact_secrets(_as_text(stderr), agent_env)
    stdout_file.write_text(safe_stdout, encoding="utf-8")
    stderr_file.write_text(safe_stderr, encoding="utf-8")

    try:
        validation_returncode, validation_text, notes = validator(workspace)
    except Exception as e:  # noqa: BLE001 - validator failures are eval failures
        validation_returncode = 1
        validation_text = f"Validator error: {type(e).__name__}: {e}"
        notes = ["validator error"]
    validation_file.write_text(redact_secrets(validation_text, agent_env), encoding="utf-8")
    passed = (
        not timed_out
        and returncode == 0
        and validation_returncode == 0
        and not notes
    )
    return EvalResult(
        name=task.name,
        passed=passed,
        agent_returncode=returncode,
        agent_duration_seconds=duration,
        agent_timed_out=timed_out,
        validation_returncode=validation_returncode,
        notes=notes,
        workspace=str(workspace),
        prompt_file=str(prompt_file),
        stdout_file=str(stdout_file),
        stderr_file=str(stderr_file),
        validation_file=str(validation_file),
        attribution=extract_attribution(safe_stdout),
    )


def extract_attribution(stdout: str) -> dict[str, int | float]:
    """Best-effort parser for StdoutRenderer + AttributionCollector output."""
    out: dict[str, int | float] = {}
    patterns = {
        "turns": r"轮数\s*:\s*(\d+)",
        "wall_seconds": r"墙钟总耗时\s*:\s*([0-9.]+)s",
        "llm_seconds": r"LLM 总耗时\s*:\s*([0-9.]+)s",
        "tool_seconds": r"工具总耗时\s*:\s*([0-9.]+)s",
    }
    for key, pattern in patterns.items():
        m = re.search(pattern, stdout)
        if m:
            out[key] = float(m.group(1)) if "." in m.group(1) else int(m.group(1))
    m = re.search(r"tokens\s*:\s*prompt=(\d+)\s+completion=(\d+)\s+total=(\d+)", stdout)
    if m:
        out["prompt_tokens"] = int(m.group(1))
        out["completion_tokens"] = int(m.group(2))
        out["total_tokens"] = int(m.group(3))
    return out


def write_eval_summary(
    run_dir: Path,
    suite_name: str,
    results: list[EvalResult],
    env: dict[str, str],
    *,
    secret_scan: dict[str, Any] | None = None,
) -> tuple[Path, Path]:
    run_dir = Path(run_dir).resolve()
    summary_json = run_dir / "summary.json"
    summary_md = run_dir / "summary.md"
    passed = sum(r.passed for r in results)
    data = {
        "suite": suite_name,
        "run_dir": str(run_dir),
        "model": env.get("MU_MODEL", ""),
        "base_url": env.get("MU_BASE_URL", ""),
        "api_key_recorded": False,
        "passed": passed,
        "total": len(results),
        "secret_scan": secret_scan or {},
        "results": [asdict(r) for r in results],
    }
    summary_json.write_text(
        redact_secrets(json.dumps(data, ensure_ascii=False, indent=2), env),
        encoding="utf-8",
    )

    lines = [
        f"# μ Eval 结果：{suite_name}",
        "",
        f"- 运行目录：`{run_dir}`",
        f"- 模型：`{env.get('MU_MODEL', '')}`",
        f"- Base URL：`{env.get('MU_BASE_URL', '')}`",
        "- API Key：未写入文件，仅运行时环境变量使用",
        f"- 总体：{passed}/{len(results)} 通过",
        f"- Secret scan：{'PASS' if not secret_scan or secret_scan.get('passed') else 'FAIL'}",
        "",
        "| 任务 | 结果 | 失败阶段 | Agent 退出码 | 耗时(s) | 验证退出码 | 备注 |",
        "|---|---:|---|---:|---:|---:|---|",
    ]
    for r in results:
        notes = "; ".join(r.notes) if r.notes else ""
        lines.append(
            f"| {r.name} | {'PASS' if r.passed else 'FAIL'} | {_failure_stage(r)} | "
            f"{r.agent_returncode} | {r.agent_duration_seconds} | {r.validation_returncode} | {notes} |"
        )
    if secret_scan and not secret_scan.get("passed"):
        lines.extend(["", "## Secret scan findings", ""])
        for hit in secret_scan.get("hits", []):
            lines.append(f"- `{hit.get('path')}` ({hit.get('kind')})")
    lines.extend(["", "## 过程文件", ""])
    for r in results:
        lines.extend(
            [
                f"### {r.name}",
                "",
                f"- workspace: `{r.workspace}`",
                f"- prompt: `{r.prompt_file}`",
                f"- stdout: `{r.stdout_file}`",
                f"- stderr: `{r.stderr_file}`",
                f"- validation: `{r.validation_file}`",
                "",
            ]
        )
    summary_md.write_text(redact_secrets("\n".join(lines), env), encoding="utf-8")
    shutil.copyfile(summary_json, run_dir.parent / "latest-summary.json")
    shutil.copyfile(summary_md, run_dir.parent / "latest-summary.md")
    return summary_json, summary_md


def _failure_stage(result: EvalResult) -> str:
    if result.passed:
        return ""
    if result.agent_timed_out:
        return "agent_timeout"
    if result.agent_returncode != 0:
        return "agent"
    return "validator"


def basic_coding_suite(timeout_seconds: float = 360.0) -> EvalSuite:
    return EvalSuite(
        name="basic-coding",
        tasks=[
            EvalTask("create_pytest_project", setup_create_pytest_project, timeout_seconds),
            EvalTask("fix_existing_bug", setup_fix_existing_bug, timeout_seconds),
            EvalTask("implement_slugify", setup_implement_slugify, timeout_seconds),
        ],
    )


def setup_create_pytest_project(workspace: Path) -> tuple[str, Validator]:
    prompt = f"""
    你正在评测目录里的独立工作区：{workspace}
    请完成一个最小 Python 项目：
    1. 创建 calc.py，实现 add(a, b) 和 mul(a, b)。
    2. 创建 test_calc.py，用 pytest 覆盖 add 和 mul。
    3. 运行 pytest -q 确认通过。
    4. 完成后给出简短最终回复，不要继续调用工具。
    请优先使用绝对路径。
    """

    def validate(ws: Path) -> tuple[int, str, list[str]]:
        notes: list[str] = []
        if not (ws / "calc.py").exists():
            notes.append("calc.py missing")
        if not (ws / "test_calc.py").exists():
            notes.append("test_calc.py missing")
        rc, text = run_pytest(ws, ["test_calc.py"])
        return rc, text, notes

    return _clean_prompt(prompt), validate


def setup_fix_existing_bug(workspace: Path) -> tuple[str, Validator]:
    (workspace / "stats_utils.py").write_text(
        textwrap.dedent(
            """
            def average(nums):
                if not nums:
                    raise ValueError("nums must not be empty")
                return sum(nums) / (len(nums) - 1)
            """
        ).lstrip(),
        encoding="utf-8",
    )
    (workspace / "test_stats_utils.py").write_text(
        textwrap.dedent(
            """
            import pytest

            from stats_utils import average


            def test_average_values():
                assert average([2, 4, 6]) == 4


            def test_average_single_value():
                assert average([10]) == 10


            def test_average_empty_list():
                with pytest.raises(ValueError):
                    average([])
            """
        ).lstrip(),
        encoding="utf-8",
    )
    prompt = f"""
    你正在评测目录里的独立工作区：{workspace}
    这里已有一个小 Python 项目，测试目前失败。
    请先运行 pytest -q 观察失败，再读取相关源码，修复 bug，最后再次运行 pytest -q 确认通过。
    完成后给出简短最终回复，不要继续调用工具。
    请优先使用绝对路径。
    """

    def validate(ws: Path) -> tuple[int, str, list[str]]:
        rc, text = run_pytest(ws, ["test_stats_utils.py"])
        return rc, text, []

    return _clean_prompt(prompt), validate


def setup_implement_slugify(workspace: Path) -> tuple[str, Validator]:
    (workspace / "string_utils.py").write_text(
        'def slugify(text: str) -> str:\n    raise NotImplementedError("TODO")\n',
        encoding="utf-8",
    )
    (workspace / "test_string_utils.py").write_text(
        textwrap.dedent(
            """
            from string_utils import slugify


            def test_slugify_basic_words():
                assert slugify("Hello World") == "hello-world"


            def test_slugify_punctuation_and_spaces():
                assert slugify("  Agent Loop: Demo!!  ") == "agent-loop-demo"


            def test_slugify_collapses_runs():
                assert slugify("a---b___c") == "a-b-c"


            def test_slugify_empty_result():
                assert slugify("!!!") == ""
            """
        ).lstrip(),
        encoding="utf-8",
    )
    prompt = f"""
    你正在评测目录里的独立工作区：{workspace}
    这里已有 string_utils.py 和 pytest 测试。
    请先运行 pytest -q 观察失败，再实现 slugify(text)，要求：
    - 转小写；
    - 非字母数字字符视为分隔符；
    - 多个分隔符合并成一个连字符；
    - 去掉首尾连字符。
    最后再次运行 pytest -q 确认通过。
    完成后给出简短最终回复，不要继续调用工具。
    请优先使用绝对路径。
    """

    def validate(ws: Path) -> tuple[int, str, list[str]]:
        rc, text = run_pytest(ws, ["test_string_utils.py"])
        return rc, text, []

    return _clean_prompt(prompt), validate


def run_pytest(workspace: Path, test_files: Sequence[str] | None = None) -> tuple[int, str]:
    ws = Path(workspace).resolve()
    cmd = [sys.executable, "-m", "pytest", "-q", "--rootdir", str(ws), *(test_files or [])]
    completed = subprocess.run(
        cmd, cwd=ws, capture_output=True, text=True, timeout=120
    )
    text = (
        "$ " + " ".join(cmd) + "\n\n"
        + "[stdout]\n" + completed.stdout
        + "\n[stderr]\n" + completed.stderr
        + f"\n[exit code] {completed.returncode}\n"
    )
    return completed.returncode, text


def _clean_prompt(text: str) -> str:
    return textwrap.dedent(text).strip()


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m mu.eval", description="Run μ eval suites")
    p.add_argument("--suite", default="basic", choices=["basic"], help="eval suite")
    p.add_argument("--run-root", default="eval_runs", help="directory for eval run artifacts")
    p.add_argument("--task", action="append", help="task name to run; repeatable")
    p.add_argument("--timeout", type=float, default=360.0, help="per-task timeout seconds")
    p.add_argument("--permission", choices=["allow", "readonly", "workspace"], default=None)
    p.add_argument("--sandbox", choices=["local", "docker"], default=None)
    p.add_argument("--code", action="store_true", help="pass --code to the agent")
    return p.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> int:
    ns = _parse_args(sys.argv[1:] if argv is None else argv)
    suite = basic_coding_suite(timeout_seconds=ns.timeout)
    agent_args: list[str] = []
    if ns.code:
        agent_args.append("--code")
    if ns.permission:
        agent_args.extend(["--permission", ns.permission])
    if ns.sandbox:
        agent_args.extend(["--sandbox", ns.sandbox])
    try:
        run = run_eval_suite(
            suite,
            run_root=ns.run_root,
            agent_args=agent_args,
            selected_tasks=ns.task,
            require_model_env=True,
        )
    except EvalConfigError as e:
        print(str(e), file=sys.stderr)
        return 2
    print(f"Evaluation run saved to: {run.run_dir}")
    print(f"Passed: {run.passed}/{run.total}")
    print(f"Secret scan: {'PASS' if run.secret_scan_passed else 'FAIL'}")
    return 0 if run.passed == run.total and run.secret_scan_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
