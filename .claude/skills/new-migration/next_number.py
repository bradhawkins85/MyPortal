"""Print the next free migration number across this branch and origin/main."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_NUMBERED = re.compile(r"^(\d+)_.*\.sql$")


def _highest(names: list[str]) -> int:
    numbers = [int(m.group(1)) for name in names if (m := _NUMBERED.match(name))]
    return max(numbers, default=0)


def _origin_main_names() -> list[str]:
    subprocess.run(["git", "fetch", "origin", "main", "--quiet"], check=False, capture_output=True)
    result = subprocess.run(
        ["git", "ls-tree", "--name-only", "origin/main", "migrations/"],
        check=False,
        capture_output=True,
        text=True,
    )
    return [Path(line).name for line in result.stdout.splitlines()]


def main() -> None:
    local = [path.name for path in Path("migrations").glob("*.sql")]
    print(max(_highest(local), _highest(_origin_main_names())) + 1)


if __name__ == "__main__":
    main()
