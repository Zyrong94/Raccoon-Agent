"""Minimal launcher for the Raccoon web workbench."""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start the Raccoon web workbench.")
    parser.add_argument("--project", default=".", help="Project root for tools and AGENTS.md.")
    parser.add_argument("--data-root", help="Directory for Raccoon session data.")
    parser.add_argument("--session-id", help="Resume this existing session.")
    parser.add_argument("--model", help="Model reference, for example provider/model.")
    parser.add_argument("--port", type=_port, default=8765, help="Port for the web workbench.")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the workbench in a browser.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        from raccoon.bootstrap.web import run_web_server

        run_web_server(
            project=args.project,
            data_root=args.data_root,
            model=args.model,
            session_id=args.session_id,
            port=args.port,
            open_browser=not args.no_browser,
        )
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


def _port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port
