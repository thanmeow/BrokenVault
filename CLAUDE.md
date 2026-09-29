# BrokenVault — one-day backup system challenge


## What we're building
A client-server backup tool that saves versions of a folder.
- Files split into fixed 512 KiB chunks; chunk ID = lowercase SHA-256 of chunk bytes.
- Manifest per version: relative paths ('/' separators), type (file/dir),
  size, mtime, ordered chunk IDs. Empty files have zero chunks. Empty dirs included.
- Client asks server which chunks are missing and uploads only those.
- Server re-hashes every chunk before storing; identical chunks stored once.
- Upload is "unfinished" until all chunks are present and verified, then
  committed to "completed" in ONE SQLite transaction. Unfinished never shown in list.
- Upload ID persisted on both client (state file) and server (SQLite) so
  backup resumes after either program restarts. Re-sending a chunk is harmless.
- Restore: any completed version into an EMPTY folder; verify each chunk hash
  while reading; recreate empty files and dirs; set mtimes.
- Verify: re-hash all chunks used by completed versions; report every
  affected version + file path. Never auto-repair.
- Reject absolute paths, '..' escapes, and duplicate paths.

## Stack
Python 3.11, FastAPI + uvicorn (server), requests (client), SQLite,
argparse CLI, pytest. Communication over HTTP on localhost.

## Commands
- `python -m server` — start server
- `python -m client backup <folder>` / `list` / `restore <version> <dest>` / `verify`
- CLI shows version ID, state, total bytes, uploaded chunk bytes, reused bytes.

## Rules
- Never add "Co-Authored-By" or any Claude/AI attribution to commit messages or PRs.
- NOT allowed: git/restic/borg/kopia as storage.
- Chunk writes: temp file -> hash check -> atomic rename.
- Keep code simple and readable. Write tests alongside each feature.
- Never commit backup data, .venv, or __pycache__.

## Progress
Dev env: Python 3.11 in project-local `.venv/` (conda). Run tests with
`.venv\python.exe -m pytest`.

Done:
- [x] Step 1 — `common/`: chunking + chunk IDs (`chunks.py`), path safety
  (`paths.py`: `check_rel_path`, `safe_join`), manifests (`manifest.py`:
  `Entry`, `scan_folder`, `validate_manifest`, list (de)serialisation).
  mtime stored as `mtime_ns`; symlinks skipped.
- [x] Steps 2+3 — `server/`: `python -m server --data-dir ./vault_data --port 8000`.
  `vault.py` = SQLite (WAL; tables `chunks`, `uploads`, `upload_chunks`) + logic,
  `chunkstore.py` = `data_dir/chunks/ab/<hash>` (temp -> fsync -> re-hash -> os.replace),
  `app.py` = FastAPI routes. Errors are JSON `{"error": ...}`.
  Endpoints: `POST /uploads`, `GET /uploads/{id}/missing`,
  `PUT /chunks/{hash}?upload_id=`, `POST /uploads/{id}/commit` (409 + `missing`),
  `GET /versions`, `GET /versions/{id}/manifest`, `GET /chunks/{hash}`, `POST /verify`.
  uploaded_bytes counts only chunks this upload actually had to store.

Next:
- [ ] Step 4 — `client/`: `backup` (state file with upload ID, resume after
  client or server restart), `list`, `restore` into an empty folder
  (verify chunk hashes, `safe_join`, empty files/dirs, set mtimes).
- [ ] Step 5 — client `verify` command + end-to-end tests against a real
  uvicorn server (including resume after restart).
