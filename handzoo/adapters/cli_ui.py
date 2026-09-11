"""`handzoo-ui` — open the review surface on a project, or start a new one."""

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
    parser.add_argument("out_dir", type=Path,
                        help="a project folder, or a new one to ingest a PDF into")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true", help="do not launch a browser")
    args = parser.parse_args(argv)

    out = args.out_dir
    is_project = (out / "manifest.jsonl").exists() or (out / "source").is_dir()
    is_new = not out.exists() or (out.is_dir() and not any(out.iterdir()))
    if not (is_project or is_new):
        # Most likely the folder that *holds* the projects. Making it a project would put a
        # run's pages beside the author's corrected corpora.
        print(f"error: {out} is not a project — it has no manifest.jsonl and is not empty.\n"
              "  To start a new project, name a new folder: handzoo-ui <new folder>",
              file=stream)
        return 2

    serve(args.out_dir, port=args.port, open_browser=not args.no_open)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
