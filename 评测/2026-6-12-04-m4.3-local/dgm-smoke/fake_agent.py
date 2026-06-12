from pathlib import Path
import re

root = Path.cwd()
if (root / "test_stats_utils.py").exists():
    (root / "stats_utils.py").write_text(
        "def average(nums):\n"
        "    if not nums:\n"
        "        raise ValueError('nums must not be empty')\n"
        "    return sum(nums) / len(nums)\n",
        encoding="utf-8",
    )
elif (root / "test_string_utils.py").exists():
    (root / "string_utils.py").write_text(
        "import re\n\n"
        "def slugify(text: str) -> str:\n"
        "    return re.sub(r'-+', '-', re.sub(r'[^a-z0-9]+', '-', text.lower())).strip('-')\n",
        encoding="utf-8",
    )
else:
    (root / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n\n"
        "def mul(a, b):\n    return a * b\n",
        encoding="utf-8",
    )
    (root / "test_calc.py").write_text(
        "from calc import add, mul\n\n"
        "def test_add():\n    assert add(2, 3) == 5\n\n"
        "def test_mul():\n    assert mul(2, 3) == 6\n",
        encoding="utf-8",
    )
print("fake agent done")
