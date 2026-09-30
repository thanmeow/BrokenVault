import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def run(a, b):
    return subprocess.run(
        [sys.executable, "scripts/compare_folders.py", str(a), str(b)],
        cwd=REPO, capture_output=True, text=True, timeout=60,
    )


def make(root: Path):
    (root / "sub" / "empty_dir").mkdir(parents=True)
    (root / "sub" / "a.bin").write_bytes(b"abc")
    (root / "empty.txt").write_bytes(b"")
    for p in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        os.utime(p, (1_600_000_000, 1_600_000_000))


def test_identical_copy(tmp_path):
    make(tmp_path / "src")
    shutil.copytree(tmp_path / "src", tmp_path / "copy")
    for p in sorted((tmp_path / "copy").rglob("*"), key=lambda p: len(p.parts), reverse=True):
        os.utime(p, (1_600_000_000, 1_600_000_000))
    r = run(tmp_path / "src", tmp_path / "copy")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.strip().endswith("identical")


def test_reports_differences(tmp_path):
    make(tmp_path / "src")
    make(tmp_path / "other")
    (tmp_path / "other" / "sub" / "a.bin").write_bytes(b"abX")
    (tmp_path / "other" / "sub" / "empty_dir").rmdir()
    os.utime(tmp_path / "other" / "empty.txt", (1_700_000_000, 1_700_000_000))
    r = run(tmp_path / "src", tmp_path / "other")
    assert r.returncode == 1
    assert "sub/a.bin: content differs" in r.stdout
    assert "missing in restore: sub/empty_dir" in r.stdout
    assert "empty.txt: mtime off by" in r.stdout
