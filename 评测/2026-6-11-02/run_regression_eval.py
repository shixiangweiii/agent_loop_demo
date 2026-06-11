from __future__ import annotations

import runpy
from pathlib import Path


EVAL_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EVAL_DIR.parents[1]
PREVIOUS_RUNNER = PROJECT_ROOT / "评测" / "2026-6-11-01" / "run_basic_eval.py"


def main() -> int:
    namespace = runpy.run_path(str(PREVIOUS_RUNNER), run_name="mu_regression_eval_runner")
    runner_main = namespace["main"]
    runner_main.__globals__.update(
        {
            "EVAL_DIR": EVAL_DIR,
            "PROJECT_ROOT": PROJECT_ROOT,
            "RUN_ROOT": EVAL_DIR / "runs",
        }
    )
    return runner_main()


if __name__ == "__main__":
    raise SystemExit(main())
