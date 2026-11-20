"""Local web workbench command-line arguments."""
import argparse
from raccoon.bootstrap.web import run_web_server

def main():
    parser = argparse.ArgumentParser(description="Start the local Raccoon Web workbench.")
    parser.add_argument("--project", default=".")
    parser.add_argument("--data-root")
    parser.add_argument("--model")
    parser.add_argument("--session-id", help="Resume this existing session.")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--open-browser", action="store_true", help="Open the workbench in the default browser.")
    args = parser.parse_args()
    run_web_server(
        project=args.project,
        data_root=args.data_root,
        model=args.model,
        session_id=args.session_id,
        port=args.port,
        open_browser=args.open_browser,
    )

if __name__ == "__main__":
    main()
