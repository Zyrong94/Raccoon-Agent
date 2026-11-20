"""Start one persistent community Chrome; attach Playwright MCP without owning it."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import portalocker

from raccoon.integrations.browser import community as browser


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "mcp"))
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Project root owning the shared Chrome profile.")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        # The host starts Chrome outside the SDK child's kill-on-close Windows job.
        endpoint = browser.ensure_browser(root) if args.action == "start" else browser._recorded_endpoint(root)
        if args.action == "start":
            print(endpoint)
            return 0
        return browser.run_mcp(root, endpoint)
    except (OSError, RuntimeError, portalocker.exceptions.LockException) as error:
        print(f"Community browser: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
