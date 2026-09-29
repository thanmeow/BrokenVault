"""End-to-end check of BrokenVault against any source folders.

Usage (from the repo root):
    python scripts/sample_check.py SRC1 [SRC2 ...] [--work DIR]

Starts a real server on a fresh data dir, backs up each SRC in order as a new version,
restores every version into a new empty folder and compares it with its source
(bytes, empty files, empty dirs, mtimes). Then tests --stop-after + server restart +
resume, and a corrupted + deleted chunk with verify. Prints a byte table and exits 1
if anything doesn't match.

Nothing about the data is assumed: expected byte counts are computed here by hashing
the sources in 512 KiB pieces. Sources are only read. All output goes in --work
(default: a new temp folder), which must be empty or from an earlier run of this script.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parent.parent
CHUNK = 512 * 1024
MARKER = ".sample_check"
issues = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        issues.append(msg.splitlines()[0])
    return cond


def indent(text: str) -> str:
    return "        " + text.strip().replace("\n", "\n        ")


# --- independent view of a folder -----------------------------------------


def snapshot(root: Path) -> dict:
    snap = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            p = Path(dirpath) / name
            rel = p.relative_to(root).as_posix()
            st = p.stat()
            if p.is_dir():
                snap[rel] = {"type": "dir", "mtime_ns": st.st_mtime_ns, "empty": not any(p.iterdir())}
            else:
                data = p.read_bytes()
                snap[rel] = {
                    "type": "file",
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "mtime_ns": st.st_mtime_ns,
                }
    return snap


def chunk_sizes(root: Path) -> dict:
    """Unique chunk hash -> size, computed without using BrokenVault's code."""
    sizes = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            with open(p, "rb") as f:
                while data := f.read(CHUNK):
                    sizes[hashlib.sha256(data).hexdigest()] = len(data)
    return sizes


def compare(src: Path, dst: Path) -> list:
    a, b = snapshot(src), snapshot(dst)
    problems = [f"missing in restore: {r}" for r in sorted(set(a) - set(b))]
    problems += [f"extra in restore: {r}" for r in sorted(set(b) - set(a))]
    exact = 0
    for rel in sorted(set(a) & set(b)):
        x, y = a[rel], b[rel]
        if x["type"] != y["type"]:
            problems.append(f"{rel}: type {x['type']} vs {y['type']}")
            continue
        if x["type"] == "file" and (x["size"], x["sha256"]) != (y["size"], y["sha256"]):
            problems.append(f"{rel}: content differs ({x['size']} vs {y['size']} bytes)")
        if x["type"] == "dir" and x["empty"] != y["empty"]:
            problems.append(f"{rel}: empty-dir status differs")
        diff = abs(x["mtime_ns"] - y["mtime_ns"])
        if diff > 1_000_000_000:
            problems.append(f"{rel}: mtime off by {diff / 1e9:.3f}s")
        elif diff == 0:
            exact += 1
    files = [r for r, v in a.items() if v["type"] == "file"]
    print(
        f"        compared {len(files)} files ({sum(a[r]['size'] for r in files)} bytes), "
        f"{len(a) - len(files)} dirs; empty files: {sum(1 for r in files if a[r]['size'] == 0)}, "
        f"empty dirs: {sum(1 for v in a.values() if v['type'] == 'dir' and v['empty'])}; "
        f"mtimes exact to the ns: {exact}/{len(a)}"
    )
    return problems


# --- server and client processes -------------------------------------------


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, work: Path, data_dir: Path):
        self.work, self.data_dir, self.port = work, data_dir, free_port()
        self.url = f"http://127.0.0.1:{self.port}"

    def start(self):
        self.log = open(self.work / f"server_{self.port}.log", "ab")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "server", "--data-dir", str(self.data_dir), "--port", str(self.port)],
            cwd=REPO, stdout=self.log, stderr=subprocess.STDOUT,
        )
        for _ in range(200):
            if self.proc.poll() is not None:
                break
            try:
                if requests.get(self.url + "/versions", timeout=0.5).ok:
                    return
            except requests.RequestException:
                time.sleep(0.1)
        raise RuntimeError(f"server did not start; see {self.log.name}")

    def kill(self):
        self.proc.kill()
        self.proc.wait()
        self.log.close()

    def chunk_path(self, h: str) -> Path:
        return self.data_dir / "chunks" / h[:2] / h


def client(server, cwd: Path, *args):
    cwd.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONPATH=str(REPO))
    return subprocess.run(
        [sys.executable, "-m", "client", "--server", server.url, *map(str, args)],
        cwd=cwd, env=env, capture_output=True, text=True,
    )


def field(out: str, name: str):
    m = re.search(rf"^{re.escape(name)}:\s+(\S+)", out, re.MULTILINE)
    return m.group(1) if m else None


def last_line(text: str) -> str:
    lines = text.strip().splitlines()
    return lines[-1] if lines else "-"


def fresh(path: Path) -> Path:
    shutil.rmtree(path, ignore_errors=True)
    return path


def prepare_work(path) -> Path:
    if path is None:
        work = Path(tempfile.mkdtemp(prefix="bv_sample_check_"))
    else:
        work = Path(path).resolve()
        if work.exists() and any(work.iterdir()) and not (work / MARKER).exists():
            sys.exit(f"error: --work {work} is not empty and wasn't made by this script")
        work.mkdir(parents=True, exist_ok=True)
    (work / MARKER).touch()
    return work


# --- the checks ------------------------------------------------------------


def backups(work, sources, before):
    print("== 1. back up each source, in order, on a fresh server")
    server = Server(work, fresh(work / "vault_main"))
    server.start()
    cwd = fresh(work / "client_main")
    seen, rows = {}, []
    for src in sources:
        r = client(server, cwd, "backup", src)
        if not check(r.returncode == 0, f"backup {src.name}: exit code {r.returncode}\n{indent(r.stderr)}"):
            server.kill()
            return None, None, None
        print(f"        progress: {last_line(r.stderr)}")
        sizes = chunk_sizes(src)
        row = {
            "source": src.name,
            "version": field(r.stdout, "version"),
            "state": field(r.stdout, "state"),
            "total": int(field(r.stdout, "total bytes")),
            "uploaded": int(field(r.stdout, "uploaded bytes")),
            "reused": int(field(r.stdout, "reused bytes")),
            "chunks": re.search(r"^chunks:\s+(.*)$", r.stdout, re.M).group(1),
        }
        rows.append(row)
        expect_total = sum(v["size"] for v in before[src].values() if v["type"] == "file")
        expect_uploaded = sum(s for h, s in sizes.items() if h not in seen)
        expect_reused = sum(s for h, s in sizes.items() if h in seen)
        check(row["state"] == "completed", f"{src.name}: state {row['state']}")
        check(row["total"] == expect_total, f"{src.name}: total bytes {row['total']} (expected {expect_total})")
        check(row["uploaded"] == expect_uploaded, f"{src.name}: uploaded bytes {row['uploaded']} (expected {expect_uploaded})")
        check(row["reused"] == expect_reused, f"{src.name}: reused bytes {row['reused']} (expected {expect_reused})")
        seen.update(sizes)

    print("\n== 2. list")
    r = client(server, cwd, "list")
    print(indent(r.stdout))
    for row, src in zip(rows, sources):
        files = sum(1 for v in before[src].values() if v["type"] == "file")
        line = next((l for l in r.stdout.splitlines() if l.startswith(row["version"])), "")
        check(line.split()[-2:] == [str(files), str(row["total"])], f"list row for {src.name}: {files} files, {row['total']} bytes")
    return server, cwd, rows


def restores(work, server, cwd, sources, rows):
    print("\n== 3. restore each version into a new empty folder and compare")
    for i, (row, src) in enumerate(zip(rows, sources), 1):
        dest = fresh(work / f"restore_{i}_{src.name}")
        r = client(server, cwd, "restore", row["version"], dest)
        if not check(r.returncode == 0, f"restore {row['version'][:8]} -> {dest.name}: exit code {r.returncode}\n{indent(r.stderr)}"):
            continue
        print(f"        progress: {last_line(r.stderr)}")
        problems = compare(src, dest)
        check(not problems, f"restore of {src.name} matches its source" + "".join("\n          - " + p for p in problems))


def interrupt_and_resume(work, src):
    print(f"\n== 4. --stop-after, kill + restart server, resume ({src.name})")
    n = len(chunk_sizes(src))
    if n < 2:
        print(f"        SKIP: {src.name} has only {n} chunk(s), nothing to interrupt")
        return
    k = max(1, n // 3)
    server = Server(work, fresh(work / "vault_resume"))
    server.start()
    cwd = fresh(work / "client_resume")
    r = client(server, cwd, "backup", src, "--stop-after", k)
    check(r.returncode == 3 and "interrupted" in r.stdout, f"--stop-after {k} of {n} chunks: exit code {r.returncode}, interrupted message")
    print(indent(r.stdout))
    state = json.loads((cwd / ".brokenvault" / "state.json").read_text())
    upload_id = next(iter(state.values()))["upload_id"]
    check(upload_id not in client(server, cwd, "list").stdout, "unfinished upload not in list")

    server.kill()
    print("        server killed; restarting on the same port and data dir")
    server.start()
    r = client(server, cwd, "backup", src)
    print(indent(r.stdout))
    check(r.returncode == 0, f"resumed backup: exit code {r.returncode}")
    check(field(r.stdout, "version") == upload_id, f"same upload ID {upload_id}")
    check(f"{n - k} uploaded this run" in r.stdout, f"only the remaining {n - k} chunks were sent")
    check(f"{k} already on server" in r.stdout, f"the {k} chunks sent before the restart were not re-sent")
    check(field(r.stdout, "uploaded bytes") == str(sum(chunk_sizes(src).values())), "uploaded bytes == all unique chunk bytes")
    check(json.loads((cwd / ".brokenvault" / "state.json").read_text()) == {}, "state entry cleared after commit")
    dest = fresh(work / "restore_resumed")
    client(server, cwd, "restore", upload_id, dest)
    problems = compare(src, dest)
    check(not problems, "resumed version restores exactly" + "".join("\n          - " + p for p in problems))
    server.kill()


def damage_and_verify(work, server, cwd, rows):
    print("\n== 5. corrupt one chunk, delete another, verify")
    r = client(server, cwd, "verify")
    check(r.returncode == 0 and r.stdout.startswith("OK"), f"verify before damage: {r.stdout.strip()}")

    usage = {}  # chunk -> {(version, path)}
    manifests = {}
    for row in rows:
        manifest = requests.get(f"{server.url}/versions/{row['version']}/manifest").json()["manifest"]
        manifests[row["version"]] = manifest
        for e in manifest:
            for h in e["chunks"]:
                usage.setdefault(h, set()).add((row["version"], e["path"]))
    if len(usage) < 2:
        print(f"        SKIP: only {len(usage)} chunk(s) stored, need 2")
        return
    ranked = sorted(usage, key=lambda h: (-len({v for v, _ in usage[h]}), h))
    corrupt_h, delete_h = ranked[0], ranked[-1]  # most-shared and least-shared chunk

    p = server.chunk_path(corrupt_h)
    damaged = bytearray(p.read_bytes())
    damaged[0] ^= 0xFF
    p.write_bytes(bytes(damaged))
    server.chunk_path(delete_h).unlink()
    print(f"        corrupted {corrupt_h[:12]}.. used by {sorted(x[1] for x in usage[corrupt_h])}")
    print(f"        deleted   {delete_h[:12]}.. used by {sorted(x[1] for x in usage[delete_h])}")

    r = client(server, cwd, "verify")
    print(indent(r.stdout))
    check(r.returncode == 1, f"verify exit code {r.returncode}")
    reported, current = {}, None
    for line in r.stdout.splitlines():
        m = re.match(r"(CORRUPT|MISSING) chunk ([0-9a-f]{64})", line)
        if m:
            current = m.group(2)
            reported[current] = (m.group(1), set())
        m = re.match(r"\s+version (\S+): (.+)$", line)
        if m and current:
            reported[current][1].add((m.group(1), m.group(2)))
    none = ("", set())
    check(set(reported) == {corrupt_h, delete_h}, "exactly the two damaged chunks reported")
    check(reported.get(corrupt_h, none)[0] == "CORRUPT", "corrupted chunk labelled CORRUPT")
    check(reported.get(delete_h, none)[0] == "MISSING", "deleted chunk labelled MISSING")
    check(reported.get(corrupt_h, none)[1] == usage[corrupt_h], "corrupt chunk: every affected version + path")
    check(reported.get(delete_h, none)[1] == usage[delete_h], "missing chunk: every affected version + path")
    check(p.read_bytes() == bytes(damaged), "verify did not repair anything")

    # Restore stops at whichever damaged chunk it reaches first (files in manifest order).
    victim = sorted(usage[corrupt_h])[0][0]
    first_bad = next(h for e in manifests[victim] for h in e["chunks"] if h in (corrupt_h, delete_h))
    expected = "hash mismatch" if first_bad == corrupt_h else "missing on the server"
    r = client(server, cwd, "restore", victim, fresh(work / "restore_damaged"))
    check(r.returncode == 1 and expected in r.stderr, f"restore of damaged version stops with '{expected}': {last_line(r.stderr)}")


def byte_table(rows):
    print("\n== backup byte table")
    print(f"| {'backup':<24} | {'version':<32} | {'total bytes':>12} | {'uploaded bytes':>14} | {'reused bytes':>12} | {'uploaded':>8} |")
    print(f"|{'-' * 26}|{'-' * 34}|{'-' * 13}:|{'-' * 15}:|{'-' * 13}:|{'-' * 9}:|")
    for row in rows:
        pct = 100 * row["uploaded"] / row["total"] if row["total"] else 0.0
        print(f"| {row['source']:<24} | {row['version']:<32} | {row['total']:>12} | {row['uploaded']:>14} | {row['reused']:>12} | {pct:>7.2f}% |")
    for row in rows:
        print(f"  {row['source']} chunks: {row['chunks']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("sources", nargs="+", help="folders to back up, in order (each becomes a version)")
    parser.add_argument("--work", help="folder for server data, restores and logs (default: new temp folder)")
    args = parser.parse_args()

    sources = [Path(s).resolve() for s in args.sources]
    for s in sources:
        if not s.is_dir():
            sys.exit(f"error: not a folder: {s}")
    work = prepare_work(args.work)
    print(f"work folder: {work}\n")

    before = {s: snapshot(s) for s in sources}
    server, cwd, rows = backups(work, sources, before)
    if rows is not None:
        restores(work, server, cwd, sources, rows)
        interrupt_and_resume(work, sources[0])
        damage_and_verify(work, server, cwd, rows)
        server.kill()

    print("\n== 6. sources untouched")
    for s in sources:
        check(snapshot(s) == before[s], f"{s.name} unchanged by the whole run")

    if rows:
        byte_table(rows)
    print(f"\n== {len(issues)} issue(s)")
    for i in issues:
        print("  - " + i)
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
