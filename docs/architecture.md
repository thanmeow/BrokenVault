# Architecture Note

## Main parts

- **Client** (`client/`, `python -m client`): scans the folder, builds the file list,
  asks the server which chunks are missing, uploads them, commits, and handles
  list / restore / verify. It keeps the current upload ID in `.brokenvault/state.json`
  in the directory it's run from.
- **Server** (`server/`, `python -m server`): a FastAPI app over HTTP on 127.0.0.1.
  It re-hashes and stores chunks, tracks uploads and versions, and verifies stored data.
- **Shared code** (`common/`): chunking, chunk IDs, file list format and path checks,
  used by both sides so they agree exactly.
- **Stored data** (`--data-dir`, default `./vault_data`): `vault.db` (SQLite, WAL mode)
  holds uploads, versions and the chunk index; `chunks/ab/<sha256>` holds one file per
  unique chunk.

## File list and chunks

- A version's file list has, per item: relative path with `/` separators, type
  (`file` or `dir`), size, modification time in nanoseconds, and the file's chunk IDs
  in order. Empty files have no chunks; empty folders are listed like any folder.
- Files are split into fixed 512 KiB pieces (the last one may be shorter).
- Chunk ID = lowercase hex SHA-256 of the chunk's bytes.
- Paths are checked on both client and server: no absolute or drive paths, no `..`,
  `.` or empty parts, no backslashes, no duplicates, and no file used as a folder.
- No duplicate storage: the chunk's hash is its file name and its primary key in the
  `chunks` table. When the server receives a chunk it already has, it does nothing.

## Safe completion

- `POST /uploads` stores the file list with state `unfinished` and records the set of
  chunk IDs it needs (`upload_chunks` table). It returns the upload ID and the chunks
  the server doesn't have yet.
- Each `PUT /chunks/<id>` is hashed in memory, written to a temp file in the target
  folder, fsynced, read back and hashed again, then renamed into place with
  `os.replace` (atomic). Only then is the chunk added to the `chunks` table.
- `POST /uploads/<id>/commit` runs in one SQLite transaction (`BEGIN IMMEDIATE`). It
  checks that every needed chunk is in the table and on disk, and only then sets the
  state to `completed`. If anything is missing it answers 409 with the list.
- Listing, restore and verify only look at `completed` uploads, so an unfinished one
  can't be seen or restored. Everything is in SQLite and on disk, so it survives a
  server restart.

## Continue after a stop

- The client saves the upload ID before sending any chunk, keyed by absolute folder
  path + server URL + a hash of the file list. Rerunning the same backup finds it.
- It then calls `GET /uploads/<id>/missing`, which the server answers from its own
  records: chunks it has verified and stored don't appear, so they aren't sent again.
- Repeating a request is safe. Resending a stored chunk returns `stored: false` and
  changes nothing. Committing twice returns the same result. A network error is retried
  up to 5 times (about 5 s in total), which rides out a quick server restart.
- `--stop-after N`, Ctrl+C and a lost server all end the run the same way: the upload
  ID stays saved and the client prints how to resume. If the server no longer knows
  the upload (for example after its data was wiped), the client starts a new one.

## Restore and verification

- Restore fetches the version's file list, checks every path again, creates all
  folders, then rebuilds each file chunk by chunk in list order. Every chunk's
  SHA-256 is checked as it arrives. Each file is written to a temp file and renamed
  into place, so a failed restore never leaves a half-written file under its real name.
- File modification times are set after each file is written. Folder times are set
  last, deepest folder first, because creating files changes a folder's time.
- `POST /verify` re-hashes every chunk used by a completed version, reports each one
  as `missing` or `corrupt`, and maps it back through the stored file lists to every
  affected (version, file path). It only reads; nothing is repaired.

## Important choices and limits

- **Fixed-size chunks:** simple and predictable, and they handle in-place edits well
  (only the changed 512 KiB piece is re-sent). An insertion shifts all later chunks;
  content-defined chunking would fix that but was left out to keep the core simple.
- **SQLite:** gives one-transaction commit and survives restarts without a separate
  database server. The server keeps one connection behind a lock. Opening a connection
  per request cost about 5 ms per chunk.
- **One HTTP request per chunk:** simple to resume and retry. About 21 MiB/s for a
  first backup and 150+ MiB/s for an unchanged folder on the development laptop.
- **Not implemented** (out of scope): version deletion, garbage collection, multiple
  users, concurrent backups, symlinks, permissions.
