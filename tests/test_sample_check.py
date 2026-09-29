"""Smoke test: scripts/sample_check.py passes on a small generated two-version dataset."""

import os
import subprocess
import sys
from pathlib import Path

from common.chunks import CHUNK_SIZE

REPO = Path(__file__).resolve().parent.parent


def test_sample_check_passes_on_generated_data(tmp_path):
    v1, v2 = tmp_path / "v1", tmp_path / "v2"
    for root in (v1, v2):
        (root / "nested" / "deeper").mkdir(parents=True)
        (root / "empty_dir").mkdir()
        (root / "note.txt").write_text("hello\n")
        (root / "nested" / "empty.dat").write_bytes(b"")
    big = bytearray(os.urandom(3 * CHUNK_SIZE + 10))
    (v1 / "nested" / "deeper" / "big.bin").write_bytes(bytes(big))
    big[CHUNK_SIZE + 5] ^= 0xFF  # v2: one chunk changes, plus a new file
    (v2 / "nested" / "deeper" / "big.bin").write_bytes(bytes(big))
    (v2 / "new.txt").write_text("added in v2\n")

    work = tmp_path / "work"
    r = subprocess.run(
        [sys.executable, "scripts/sample_check.py", str(v1), str(v2), "--work", str(work)],
        cwd=REPO, capture_output=True, text=True, timeout=300,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "== 0 issue(s)" in r.stdout
    assert "FAIL" not in r.stdout


def test_sample_check_refuses_foreign_work_folder(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    (work / "important.txt").write_text("keep me")
    r = subprocess.run(
        [sys.executable, "scripts/sample_check.py", str(src), "--work", str(work)],
        cwd=REPO, capture_output=True, text=True, timeout=60,
    )
    assert r.returncode != 0
    assert "not empty" in r.stderr
    assert (work / "important.txt").read_text() == "keep me"
