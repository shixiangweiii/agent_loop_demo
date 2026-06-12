from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path


EVAL_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EVAL_DIR.parents[1]
PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"
if not PYTHON.exists():
    PYTHON = Path(sys.executable)


def _run(cmd: list[str], *, cwd: Path = PROJECT_ROOT, env: dict[str, str] | None = None, timeout: int = 900) -> tuple[int, str]:
    completed = subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    text = (
        "$ " + " ".join(cmd) + "\n\n"
        + "[stdout]\n" + completed.stdout
        + "\n[stderr]\n" + completed.stderr
        + f"\n[exit code] {completed.returncode}\n"
    )
    return completed.returncode, text


def _redact(text: str, env: dict[str, str]) -> str:
    out = text
    for key, value in env.items():
        if not value:
            continue
        upper = key.upper()
        if "API_KEY" in upper or upper.endswith("_TOKEN") or upper.endswith("_SECRET"):
            out = out.replace(value, f"[REDACTED:{key}]")
    return out


def run_pytest() -> dict:
    rc, text = _run([str(PYTHON), "-m", "pytest", "-q"], timeout=900)
    (EVAL_DIR / "pytest-output.txt").write_text(text, encoding="utf-8")
    passed = rc == 0
    return {"name": "pytest", "passed": passed, "returncode": rc, "output_file": "pytest-output.txt"}


def run_real_eval() -> dict:
    env = os.environ.copy()
    required = ["MU_MODEL"]
    has_key = bool(env.get("MU_API_KEY") or env.get("OPENAI_API_KEY"))
    missing = [k for k in required if not env.get(k)]
    if not has_key:
        missing.append("MU_API_KEY or OPENAI_API_KEY")
    output_file = EVAL_DIR / "real-eval-output.txt"
    if missing:
        output_file.write_text("Skipped real model eval; missing: " + ", ".join(missing) + "\n", encoding="utf-8")
        return {
            "name": "real_model_basic_eval",
            "passed": False,
            "skipped": True,
            "missing": missing,
            "output_file": output_file.name,
        }

    run_root = EVAL_DIR / "real-eval-runs"
    cmd = [
        str(PYTHON),
        "-m",
        "mu.eval",
        "--run-root",
        str(run_root),
        "--timeout",
        "360",
    ]
    rc, text = _run(cmd, env=env, timeout=1500)
    safe_text = _redact(text, env)
    output_file.write_text(safe_text, encoding="utf-8")

    summary = {}
    latest = run_root / "latest-summary.json"
    if latest.exists():
        summary = json.loads(latest.read_text(encoding="utf-8"))
    passed = rc == 0 and summary.get("passed") == summary.get("total") and summary.get("total", 0) > 0
    return {
        "name": "real_model_basic_eval",
        "passed": passed,
        "skipped": False,
        "returncode": rc,
        "output_file": output_file.name,
        "run_root": str(run_root.relative_to(EVAL_DIR)),
        "summary": summary,
    }


def write_fake_agent(path: Path) -> None:
    path.write_text(
        r'''
from __future__ import annotations

import re
import sys
from pathlib import Path


def main() -> int:
    ws = Path(sys.argv[1])
    if (ws / "stats_utils.py").exists():
        p = ws / "stats_utils.py"
        p.write_text(
            "def average(nums):\n"
            "    if not nums:\n"
            "        raise ValueError(\"nums must not be empty\")\n"
            "    return sum(nums) / len(nums)\n",
            encoding="utf-8",
        )
        print("fixed average")
        return 0
    if (ws / "string_utils.py").exists():
        p = ws / "string_utils.py"
        p.write_text(
            "import re\n\n"
            "def slugify(text: str) -> str:\n"
            "    s = re.sub(r\"[^A-Za-z0-9]+\", \"-\", text.lower())\n"
            "    return s.strip(\"-\")\n",
            encoding="utf-8",
        )
        print("implemented slugify")
        return 0
    (ws / "calc.py").write_text(
        "def add(a, b):\n"
        "    return a + b\n\n"
        "def mul(a, b):\n"
        "    return a * b\n",
        encoding="utf-8",
    )
    (ws / "test_calc.py").write_text(
        "from calc import add, mul\n\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n\n"
        "def test_mul():\n"
        "    assert mul(2, 3) == 6\n",
        encoding="utf-8",
    )
    print("created calc project")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''.lstrip(),
        encoding="utf-8",
    )


def run_dgm_smoke() -> dict:
    sys.path.insert(0, str(PROJECT_ROOT))
    from mu.dgm import run_dgm_candidate
    from mu.eval import basic_coding_suite

    smoke_dir = EVAL_DIR / "dgm-smoke"
    candidate_dir = smoke_dir / "candidate"
    archive_dir = smoke_dir / "archive"
    fake_agent = smoke_dir / "fake_agent.py"
    candidate_prompt = candidate_dir / ".mu" / "prompts" / "smoke.md"
    candidate_prompt.parent.mkdir(parents=True, exist_ok=True)
    candidate_prompt.write_text(
        "# Smoke prompt candidate\n\nKeep answers short during eval.\n",
        encoding="utf-8",
    )
    fake_agent.parent.mkdir(parents=True, exist_ok=True)
    write_fake_agent(fake_agent)

    def builder(workspace: Path, _prompt: str) -> list[str]:
        return [str(PYTHON), str(fake_agent), str(workspace)]

    entry = run_dgm_candidate(
        source_type="dir",
        source_path=candidate_dir,
        description="m4 regression smoke prompt candidate",
        candidate_id="m4-regression-smoke",
        project_root=PROJECT_ROOT,
        archive_dir=archive_dir,
        suite=basic_coding_suite(timeout_seconds=120),
        agent_cmd_builder=builder,
        require_model_env=False,
        env={"SECRET_DGM_KEY": "SECRET_DGM_KEY"},
    )
    result = asdict(entry)
    return {
        "name": "dgm_lite_archive_smoke",
        "passed": entry.passed == entry.total,
        "archive_dir": str(archive_dir.relative_to(EVAL_DIR)),
        "entry": result,
    }


def scan_for_secret_patterns() -> dict:
    pattern = re.compile(r"sk-[A-Za-z0-9][A-Za-z0-9_-]{8,}")
    hits: list[str] = []
    for p in EVAL_DIR.rglob("*"):
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if pattern.search(text):
            hits.append(str(p.relative_to(EVAL_DIR)))
    return {"name": "secret_scan", "passed": not hits, "hits": hits}


def write_report(results: list[dict]) -> None:
    passed = sum(1 for r in results if r.get("passed"))
    total = len(results)
    summary = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "eval_dir": str(EVAL_DIR),
        "passed": passed,
        "total": total,
        "results": results,
    }
    (EVAL_DIR / "latest-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    lines = [
        "# M4.0 完整回归评测报告",
        "",
        f"- 时间：{summary['created_at']}",
        f"- 目录：`{EVAL_DIR}`",
        f"- 总体：{passed}/{total} 项通过",
        "",
        "| 项目 | 结果 | 备注 |",
        "|---|---:|---|",
    ]
    for r in results:
        if r.get("skipped"):
            status = "SKIP"
        else:
            status = "PASS" if r.get("passed") else "FAIL"
        note_parts = []
        if r.get("output_file"):
            note_parts.append(f"output=`{r['output_file']}`")
        if r.get("run_root"):
            note_parts.append(f"run_root=`{r['run_root']}`")
        if r.get("archive_dir"):
            note_parts.append(f"archive=`{r['archive_dir']}`")
        if r.get("missing"):
            note_parts.append("missing=" + ", ".join(r["missing"]))
        if r.get("hits"):
            note_parts.append("secret_hits=" + ", ".join(r["hits"]))
        if r.get("summary"):
            note_parts.append(f"eval={r['summary'].get('passed')}/{r['summary'].get('total')}")
        lines.append(f"| {r['name']} | {status} | {'; '.join(note_parts)} |")
    lines.extend(
        [
            "",
            "## 结论",
            "",
            "- 离线 pytest 覆盖 M0-M4 基座。",
            "- 真实模型 eval 使用环境变量注入模型配置；API key 未写入报告。",
            "- DGM-lite smoke 验证候选隔离、eval run 与 archive summary 写入。",
            "- secret scan 使用 `sk-...` 形式检查本目录，命中为失败。",
        ]
    )
    report = "\n".join(lines)
    (EVAL_DIR / "m4-regression-eval-report.md").write_text(report, encoding="utf-8")
    (EVAL_DIR / "latest-summary.md").write_text(report, encoding="utf-8")


def main() -> int:
    results = [run_pytest(), run_real_eval(), run_dgm_smoke()]
    results.append(scan_for_secret_patterns())
    write_report(results)
    strict = [r for r in results if not r.get("skipped")]
    return 0 if all(r.get("passed") for r in strict) else 1


if __name__ == "__main__":
    raise SystemExit(main())
