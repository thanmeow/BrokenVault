"""Compare a restored folder with its source: paths, types, bytes, empty items, mtimes.

Usage (from the repo root):
    python scripts/compare_folders.py SOURCE RESTORED

Exits 0 and prints "identical" if they match (mtimes within 1 s), else lists every
difference and exits 1.
"""

import sys
from pathlib import Path

from sample_check import compare


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    source, restored = Path(sys.argv[1]), Path(sys.argv[2])
    for p in (source, restored):
        if not p.is_dir():
            print(f"error: not a folder: {p}", file=sys.stderr)
            return 2
    problems = compare(source, restored)
    if problems:
        for p in problems:
            print(f"DIFFERENT: {p}")
        return 1
    print("identical")
    return 0


if __name__ == "__main__":
    sys.exit(main())
