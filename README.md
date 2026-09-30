# BrokenVault - <Team Name>

A client-server backup tool that saves versions of a folder. It sends only chunks the
server doesn't already have, resumes an interrupted upload after either program
restarts, keeps unfinished versions hidden, restores any completed version exactly,
and reports damaged stored data.

## Team

- <Member 1>
- <Member 2>

## Supported setup

- Operating system: Windows 10 (tested). Nothing is Windows-specific, but other systems are untested.
- Language: Python 3.11
- Required tools: Python 3.11 with `pip`. No internet access is needed after install.

## Install

From the repository root, in PowerShell:

```text
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -r requirements-lock.txt
```

`requirements-lock.txt` pins the exact tested versions; `requirements.txt` lists the
direct dependencies (FastAPI, uvicorn, requests, httpx, pytest).

## Start the complete system

The server is one command (it keeps running; use a second terminal for the client):

```text
python -m server
```

Options: `--data-dir DIR` (default `./vault_data`) and `--port N` (default 8000).
The server listens on 127.0.0.1 only. The client talks to it over HTTP; use
`python -m client --server http://127.0.0.1:N ...` if you change the port.

## Commands

### Back up a folder

```text
python -m client backup <folder>
```

Prints the version ID, state, file/dir counts, total bytes, uploaded chunk bytes,
reused bytes and chunk counts, with a progress line while uploading.
`--stop-after N` stops after N chunk uploads (to demonstrate resume). Ctrl+C or a
lost server also stops it the same way. Running the same command again continues
the **same** upload and sends only chunks the server still lacks.

### List completed versions

```text
python -m client list
```

Shows each completed version's ID, completion time (UTC), file count and total bytes.
Unfinished uploads are never listed.

### Restore a version

```text
python -m client restore <version-id> <empty-or-new-folder>
```

Rebuilds every file, empty file and empty folder, checks every chunk's hash while
reading, and sets file and folder modification times. Refuses a non-empty destination.

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

130 tests: chunking, manifest and path checks, server API, client unit tests,
and end-to-end tests that start a real server process (backup, reuse, interrupt +
server restart + resume, exact restore, damage + verify, error messages).

## Demo steps

With the server running in one terminal (`python -m server --data-dir demo_data`):

1. Back up version 1: `python -m client backup <v1-folder>`
2. Back up version 2 and show reused and uploaded bytes: `python -m client backup <v2-folder>`
3. Interrupt another upload: `python -m client backup <v3-folder> --stop-after 5`
   (or press Ctrl+C), show `python -m client list` has no new version, then stop the
   server (Ctrl+C) and start it again with the same `--data-dir`.
4. Continue with the same command: `python -m client backup <v3-folder>`. It reports
   `resuming upload <id>`, completes under that same ID, then restore it:
   `python -m client restore <id> restored_v3`.
5. Change or remove one stored chunk (any file under `demo_data\chunks\`) and run
   `python -m client verify`.

`python scripts/sample_check.py <v1-folder> <v2-folder>` runs all of the above
automatically against a throwaway server and data folder, compares each restore with
its source (bytes, empty items, mtimes), and prints a table of total, uploaded and
reused bytes. It works on any folders.

## Performance

Measured on the development laptop (Windows 10, SSD) with a 509 MiB tree of random
data (325 files, nested folders), client and server on the same machine:

| Operation | Time |
|---|---:|
| First backup (all 1,260 chunks uploaded) | 24 s |
| Second backup, folder unchanged (0 bytes uploaded) | 3 s |
| Restore | 14-23 s |
| Verify | 3 s |

Windows real-time antivirus scanning adds about 10 ms to the first read of each newly
written chunk file, which is why the first restore after a backup is the slowest.
Excluding the data folder from scanning speeds that up.

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
- Tested on Windows 10 only.

## External and AI-assisted work

- Libraries: FastAPI, Starlette, uvicorn and pydantic (server HTTP), requests (client
  HTTP), SQLite via Python's `sqlite3`, hashlib (SHA-256), pytest and httpx (tests).
  No external services.
- AI tools: Claude Code (Anthropic) was used to write the code, tests and
  documentation under the team's direction; the team reviewed and ran everything.
