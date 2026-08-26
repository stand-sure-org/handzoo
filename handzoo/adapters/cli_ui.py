"""`handzoo-ui` — open the review surface on a run directory."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .ui_server import serve


def main(argv: list[str] | None = None, *, stream=None) -> int:
    stream = stream or sys.stdout
    parser = argparse.ArgumentParser(
        prog="handzoo-ui",
        description="Review a run in the browser: page image and emitted text, side by side.")
    parser.add_argument("out_dir", type=Path, help="the directory `handzoo` wrote to")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true", help="do not launch a browser")
    args = parser.parse_args(argv)

    if not (args.out_dir / "manifest.jsonl").exists():
        print(f"error: no manifest.jsonl in {args.out_dir} — run `handzoo` on a PDF first.",
              file=stream)
        return 2

    serve(args.out_dir, port=args.port, open_browser=not args.no_open)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
