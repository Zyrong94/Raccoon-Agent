"""Official QQ bot command-line entry."""
import argparse
import asyncio
import logging
from pathlib import Path
from raccoon.bootstrap.qq import serve
from raccoon.interfaces.qq.session import QQSessionConflict

def main() -> None:
    parser = argparse.ArgumentParser(description="Connect Raccoon to the official QQ bot gateway")
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--new-session", action="store_true", help="明确放弃旧会话的排队工作，保留记录并新建主会话")
    args = parser.parse_args()
    project = args.project.resolve()
    data_root = args.data_root.resolve() if args.data_root else project / ".raccoon"
    env_file = args.env_file.resolve() if args.env_file else project / ".env.qq"
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        asyncio.run(serve(project, data_root, env_file, new_session=args.new_session))
    except KeyboardInterrupt:
        pass
    except QQSessionConflict as error:
        logging.error("%s", error)
        raise SystemExit(1) from None
    except Exception as error:
        # Keep credentials and raw provider responses out of host logs.
        logging.error("QQ host stopped (%s)", type(error).__name__)
        raise SystemExit(1) from None

if __name__ == "__main__":
    main()
