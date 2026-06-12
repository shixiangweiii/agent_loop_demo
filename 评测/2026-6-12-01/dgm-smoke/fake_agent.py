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
