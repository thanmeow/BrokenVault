# BrokenVault - The lady bugs

A client-server backup tool that saves versions of a folder. It sends only chunks the
server doesn't already have, resumes an interrupted upload after either program
restarts, keeps unfinished versions hidden, restores any completed version exactly,
and reports damaged stored data.

## Team

- Member 1: Thanmai Kancha
- Member 2: Mokshitha Burra

## Supported setup

- Operating system or Docker version: Windows 10 (tested). No Docker needed.
- Programming language and version: Python 3.11
- Required tools: Python 3.11 with `pip`. No internet access is needed after install.

## Install

From the repository root, in PowerShell:

```text
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -r requirements-lock.txt
```

If Python 3.11 isn't on PATH, any Python 3.11 works, e.g. `conda create -p .venv python=3.11`.
Instead of the activate line, put that environment first on PATH (in every terminal
you use), then run the `pip install` line:

```text
$env:PATH = "$PWD\.venv;$PWD\.venv\Scripts;$env:PATH"
```

`requirements-lock.txt` pins the exact tested versions; `requirements.txt` lists the
direct dependencies (FastAPI, uvicorn, requests, httpx, pytest).

## Start the complete system

The server is one command. It keeps running, so use a second terminal (in the repo
root, with the same environment activated) for the client commands:

```text
python -m server
```

It stores data in `.\vault_data` and listens on `http://127.0.0.1:8000` (options:
`--data-dir DIR`, `--port N`; point the client at another port with
`python -m client --server http://127.0.0.1:N ...`).

## Commands

The examples back up the sample folders from `..\bv_materials`; any folder works.

### Back up a folder

```text
python -m client backup ..\bv_materials\brokenvault_sample_v1
```

Prints the version ID, state, file/dir counts, total bytes, uploaded chunk bytes,
reused bytes and chunk counts, with a progress line while uploading.
`--stop-after N` stops after N chunk uploads, to demonstrate resume. Ctrl+C or a lost
server stops it the same way. Running the same command again continues the **same**
upload and sends only chunks the server still lacks.

### List completed versions

```text
python -m client list
```

Shows each completed version's ID, completion time (UTC), file count and total bytes.
Unfinished uploads are never listed.

### Restore a version

```text
python -m client restore <version-id> ..\bv_restore\v1
```

`<version-id>` is printed by `backup` and `list`. The destination must be new or
empty. Restore rebuilds every file, empty file and empty folder, checks every chunk's
hash while reading, and sets file and folder modification times.

### Verify stored data

```text
python -m client verify
```

Re-hashes every chunk used by a completed version. Prints `OK`, or each missing or
corrupt chunk with every affected version and file path (exit code 1). Never repairs.

Exit codes: 0 success, 1 error (or damage found by verify), 3 backup interrupted
(resumable), 130 cancelled with Ctrl+C outside an upload.

## Run tests

```text
python -m pytest
```

132 tests: chunking, manifest and path checks, server API, client unit tests,
end-to-end tests that start a real server process (backup, reuse, interrupt + server
restart + resume, exact restore, damage + verify, error messages), and the two
scripts in `scripts/`.

## Demo steps

Start with no `vault_data`, `.brokenvault` or `..\bv_restore` folder (delete them to
run the demo again). Terminal 1: `python -m server`. Terminal 2, from the repo root:

1. Back up version 1:

   ```text
   python -m client backup ..\bv_materials\brokenvault_sample_v1
   ```

2. Back up version 2, interrupted after one chunk. `list` still shows only version 1:

   ```text
   python -m client backup ..\bv_materials\brokenvault_sample_v2 --stop-after 1
   python -m client list
   ```

3. Restart both programs: press Ctrl+C in terminal 1 and run `python -m server` again.
   (The client is a new process for every command.)

4. Continue version 2 with the same command. It prints `resuming upload <id>`,
   finishes under that same ID, and shows uploaded bytes far below the total, with
   the rest reused. Then restore both versions and compare them with their sources:

   ```text
   python -m client backup ..\bv_materials\brokenvault_sample_v2
   python -m client restore <v1-version-id> ..\bv_restore\v1
   python -m client restore <v2-version-id> ..\bv_restore\v2
   python scripts/compare_folders.py ..\bv_materials\brokenvault_sample_v1 ..\bv_restore\v1
   python scripts/compare_folders.py ..\bv_materials\brokenvault_sample_v2 ..\bv_restore\v2
   ```

5. Change one stored chunk, remove another, and verify:

   ```text
   $chunks = Get-ChildItem vault_data\chunks -Recurse -File
   Set-Content $chunks[0].FullName "damaged"
   Remove-Item $chunks[-1].FullName
   python -m client verify
   ```

`python scripts/sample_check.py ..\bv_materials\brokenvault_sample_v1 ..\bv_materials\brokenvault_sample_v2`
runs all of this automatically against a throwaway server and data folder. It checks
every number and comparison and prints a table of total, uploaded and reused bytes.
It works on any folders.

## Known limits

- Fixed-size 512 KiB chunks: inserting bytes near the start of a large file changes
  every chunk after it (content-defined chunking is a stretch goal, not implemented).
- One user, one backup at a time, no authentication; the server listens on localhost only.
- Symlinks are skipped; permissions and other OS attributes are not saved (out of scope).
- Paths differing only by letter case are not detected as duplicates.
- Nothing is ever deleted: no version deletion or garbage collection. Chunks from an
  abandoned unfinished upload stay stored, and a server crash mid-write can leave a
  stray `.tmp-*` file in the chunk store (harmless, ignored).
- Resume matches on folder + server URL + the exact file list. If files changed since
  the interrupted run, a new upload starts (already stored chunks are still reused).
- A failed restore (for example a damaged chunk) stops and leaves the destination
  incomplete.
- Speed, measured on a 509 MiB random tree (325 files) with client and server on one
  Windows 10 laptop: first backup 24 s, unchanged backup 3 s, restore 14-23 s, verify
  3 s. Windows real-time antivirus adds about 10 ms to the first read of each newly
  written chunk file, which makes the first restore after a backup the slowest.
- Tested on Windows 10 only.

## External and AI-assisted work

- Libraries: FastAPI, Starlette, uvicorn and pydantic (server HTTP), requests (client
  HTTP), SQLite via Python's `sqlite3`, hashlib (SHA-256), pytest and httpx (tests).
  No external services.
- AI tools: Claude Code (Anthropic's AI coding assistant) wrote the code, tests,
  scripts and documentation, and ran the tests, sample-data checks and timing runs.
  The team wrote the specification and rules it worked from (`CLAUDE.md`), gave the
  step-by-step instructions, and committed the work.
