"""Command line: python -m client [--server URL] backup|list|restore|verify ..."""

import argparse
import sys

from client.api import Api, ClientError
from client.backup import Interrupted, run_backup
from client.restore import run_restore
from client.state import STATE_DIR, STATE_FILE, State

DEFAULT_SERVER = "http://127.0.0.1:8000"
EXIT_OK, EXIT_ERROR, EXIT_INTERRUPTED = 0, 1, 3


def cmd_backup(api, args) -> int:
    r = run_backup(api, args.folder, State(), stop_after=args.stop_after, progress_stream=sys.stderr)
    print(f"version:        {r['id']}")
    print(f"state:          {r['state']}")
    print(f"files:          {r['files']} files, {r['dirs']} dirs")
    print(f"total bytes:    {r['total_bytes']}")
    print(f"uploaded bytes: {r['uploaded_bytes']}")
    print(f"reused bytes:   {r['reused_bytes']}")
    print(
        f"chunks:         {r['unique_chunks']} unique, {r['chunks_already_on_server']} "
        f"already on server, {r['chunks_uploaded']} uploaded this run"
    )
    return EXIT_OK


def cmd_list(api, args) -> int:
    versions = api.versions()
    if not versions:
        print("no completed versions")
        return EXIT_OK
    print(f"{'VERSION':<32}  {'COMPLETED (UTC)':<19}  {'FILES':>6}  {'TOTAL BYTES':>12}")
    for v in versions:
        manifest = api.manifest(v["id"])["manifest"]
        files = sum(1 for e in manifest if e["type"] == "file")
        when = v["completed_at"][:19].replace("T", " ")
        print(f"{v['id']:<32}  {when:<19}  {files:>6}  {v['total_bytes']:>12}")
    return EXIT_OK


def cmd_restore(api, args) -> int:
    r = run_restore(api, args.version, args.dest, progress_stream=sys.stderr)
    print(f"restored version {r['id']} to {args.dest}: {r['files']} files, {r['dirs']} dirs, {r['bytes']} bytes")
    return EXIT_OK


def cmd_verify(api, args) -> int:
    report = api.verify()
    if report["ok"]:
        print(f"OK: {report['checked_chunks']} chunk(s) checked, no damage found")
        return EXIT_OK
    print(f"DAMAGED: {len(report['problems'])} of {report['checked_chunks']} chunk(s) are bad")
    for p in report["problems"]:
        print(f"{p['problem'].upper()} chunk {p['hash']}")
        for a in p["affected"]:
            print(f"  version {a['version_id']}: {a['path']}")
    print(f"affected versions: {', '.join(report['affected_versions'])}")
    return EXIT_ERROR


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m client", description="BrokenVault client")
    parser.add_argument("--server", default=DEFAULT_SERVER, help=f"server URL (default {DEFAULT_SERVER})")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("backup", help="back up a folder as a new version")
    p.add_argument("folder")
    p.add_argument("--stop-after", type=int, metavar="N", help="stop after N chunk uploads (for testing resume)")
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("list", help="list completed versions")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("restore", help="restore a version into an empty folder")
    p.add_argument("version")
    p.add_argument("dest")
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("verify", help="check every stored chunk used by completed versions")
    p.set_defaults(func=cmd_verify)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    api = Api(args.server)
    try:
        return args.func(api, args)
    except Interrupted as e:
        print(
            f"interrupted after {e.sent} chunk upload(s); {e.remaining} chunk(s) still missing.\n"
            f"Upload {e.upload_id} is saved in {STATE_DIR}/{STATE_FILE}; "
            "run the same backup command again to resume."
        )
        return EXIT_INTERRUPTED
    except ClientError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR
