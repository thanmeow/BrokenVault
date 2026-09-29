"""Run the server: python -m server [--data-dir DIR] [--port PORT]"""

import argparse
from pathlib import Path

import uvicorn

from server.app import create_app


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="python -m server", description="BrokenVault server")
    parser.add_argument("--data-dir", default="./vault_data", help="where chunks and the database live")
    parser.add_argument("--port", type=int, default=8000)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    app = create_app(args.data_dir)
    print(f"BrokenVault data dir: {Path(args.data_dir).resolve()}")
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
