from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


EVAL_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EVAL_DIR.parent
RUN_ROOT = EVAL_DIR / "runs"
TASK_TIMEOUT_SECONDS = 360


@dataclass
class TaskResult:
    name: str
    passed: bool
    agent_returncode: int | None
    agent_duration_seconds: float
    agent_timed_out: bool
    validation_returncode: int | None
    notes: list[str]
    workspace: str
    stdout_file: str
    stderr_file: str
    validation_file: str


def main() -> int:
    required = ["MU_BASE_URL", "MU_MODEL", "MU_API_KEY"]
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        print(f"Missing environment variables: {', '.join(missing)}", file=sys.stderr)
        return 2

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = RUN_ROOT / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)

    tasks = [
        ("create_pytest_project", setup_create_pytest_project),
        ("fix_existing_bug", setup_fix_existing_bug),
        ("implement_slugify", setup_implement_slugify),
    ]

    results: list[TaskResult] = []
    for name, setup in tasks:
        task_dir = run_dir / name
        workspace = task_dir / "workspace"
        workspace.mkdir(parents=True)
        prompt, validator = setup(workspace)
        (task_dir / "task_prompt.txt").write_text(prompt, encoding="utf-8")

        result = run_agent_task(name, task_dir, workspace, prompt, validator)
        results.append(result)

    write_summary(run_dir, results)
    print(f"Evaluation run saved to: {run_dir}")
    print(f"Passed: {sum(r.passed for r in results)}/{len(results)}")
    return 0 if all(r.passed for r in results) else 1


def base_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = (
        str(PROJECT_ROOT)
        if not env.get("PYTHONPATH")
        else str(PROJECT_ROOT) + os.pathsep + env["PYTHONPATH"]
    )
    return env


def run_agent_task(name, task_dir: Path, workspace: Path, prompt: str, validator):
    stdout_file = task_dir / "agent_stdout.txt"
    stderr_file = task_dir / "agent_stderr.txt"
    validation_file = task_dir / "validation.txt"

    cmd = [str(PROJECT_ROOT / ".venv" / "bin" / "python"), "-m", "mu", prompt]
    if not Path(cmd[0]).exists():
        cmd = [sys.executable, "-m", "mu", prompt]

    start = time.monotonic()
    timed_out = False
    returncode: int | None
    try:
        completed = subprocess.run(
            cmd,
            cwd=workspace,
            env=base_env(),
            capture_output=True,
            text=True,
            timeout=TASK_TIMEOUT_SECONDS,
        )
        returncode = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        returncode = None
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
    duration = time.monotonic() - start

    stdout_file.write_text(as_text(stdout), encoding="utf-8")
    stderr_file.write_text(as_text(stderr), encoding="utf-8")

    validation_returncode, validation_text, notes = validator(workspace)
    validation_file.write_text(validation_text, encoding="utf-8")
    passed = (not timed_out) and returncode == 0 and validation_returncode == 0 and not notes

    return TaskResult(
        name=name,
        passed=passed,
        agent_returncode=returncode,
        agent_duration_seconds=round(duration, 2),
        agent_timed_out=timed_out,
        validation_returncode=validation_returncode,
        notes=notes,
        workspace=str(workspace),
        stdout_file=str(stdout_file),
        stderr_file=str(stderr_file),
        validation_file=str(validation_file),
    )


def as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def run_pytest(workspace: Path) -> tuple[int, str]:
    cmd = [str(PROJECT_ROOT / ".venv" / "bin" / "python"), "-m", "pytest", "-q"]
    if not Path(cmd[0]).exists():
        cmd = [sys.executable, "-m", "pytest", "-q"]
    completed = subprocess.run(cmd, cwd=workspace, capture_output=True, text=True, timeout=120)
    text = (
        "$ " + " ".join(cmd) + "\n\n"
        + "[stdout]\n" + completed.stdout
        + "\n[stderr]\n" + completed.stderr
        + f"\n[exit code] {completed.returncode}\n"
    )
    return completed.returncode, text


def setup_create_pytest_project(workspace: Path):
    prompt = f"""
    你正在评测目录里的独立工作区：{workspace}
    请完成一个最小 Python 项目：
    1. 创建 calc.py，实现 add(a, b) 和 mul(a, b)。
    2. 创建 test_calc.py，用 pytest 覆盖 add 和 mul。
    3. 运行 pytest -q 确认通过。
    4. 完成后给出简短最终回复，不要继续调用工具。
    请优先使用绝对路径。
    """

    def validate(ws: Path):
        notes: list[str] = []
        if not (ws / "calc.py").exists():
            notes.append("calc.py missing")
        if not (ws / "test_calc.py").exists():
            notes.append("test_calc.py missing")
        rc, text = run_pytest(ws)
        return rc, text, notes

    return clean_prompt(prompt), validate


def setup_fix_existing_bug(workspace: Path):
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

    def validate(ws: Path):
        notes: list[str] = []
        rc, text = run_pytest(ws)
        return rc, text, notes

    return clean_prompt(prompt), validate


def setup_implement_slugify(workspace: Path):
    (workspace / "string_utils.py").write_text(
        textwrap.dedent(
            """
            def slugify(text: str) -> str:
                raise NotImplementedError("TODO")
            """
        ).lstrip(),
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

    def validate(ws: Path):
        notes: list[str] = []
        rc, text = run_pytest(ws)
        return rc, text, notes

    return clean_prompt(prompt), validate


def clean_prompt(text: str) -> str:
    return textwrap.dedent(text).strip()


def write_summary(run_dir: Path, results: list[TaskResult]) -> None:
    summary_json = {
        "run_dir": str(run_dir),
        "model": os.environ.get("MU_MODEL", ""),
        "base_url": os.environ.get("MU_BASE_URL", ""),
        "api_key_recorded": False,
        "passed": sum(r.passed for r in results),
        "total": len(results),
        "results": [asdict(r) for r in results],
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    lines = [
        "# Agent Loop 基础评测结果",
        "",
        f"- 运行目录：`{run_dir}`",
        f"- 模型：`{os.environ.get('MU_MODEL', '')}`",
        f"- Base URL：`{os.environ.get('MU_BASE_URL', '')}`",
        "- API Key：未写入文件，仅运行时环境变量使用",
        f"- 总体：{sum(r.passed for r in results)}/{len(results)} 通过",
        "",
        "| 任务 | 结果 | Agent 退出码 | 耗时(s) | 验证退出码 | 备注 |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for r in results:
        notes = "; ".join(r.notes) if r.notes else ""
        result = "PASS" if r.passed else "FAIL"
        lines.append(
            f"| {r.name} | {result} | {r.agent_returncode} | "
            f"{r.agent_duration_seconds} | {r.validation_returncode} | {notes} |"
        )
    lines.extend(
        [
            "",
            "## 过程文件",
            "",
        ]
    )
    for r in results:
        lines.extend(
            [
                f"### {r.name}",
                "",
                f"- workspace: `{r.workspace}`",
                f"- stdout: `{r.stdout_file}`",
                f"- stderr: `{r.stderr_file}`",
                f"- validation: `{r.validation_file}`",
                "",
            ]
        )
    (run_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    shutil.copyfile(run_dir / "summary.md", EVAL_DIR / "latest-summary.md")
    shutil.copyfile(run_dir / "summary.json", EVAL_DIR / "latest-summary.json")


if __name__ == "__main__":
    raise SystemExit(main())

