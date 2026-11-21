"""Launch the local Raccoon workbench from a source checkout."""

import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web-next"


def main() -> int:
    node = shutil.which("node")
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if sys.version_info < (3, 11) or not node or not npm:
        raise SystemExit("请先安装 Python 3.11+、Node.js 20.9+ 和 npm。")
    version = subprocess.check_output([node, "-p", "process.versions.node"], text=True)
    if tuple(map(int, version.split(".")[:2])) < (20, 9):
        raise SystemExit("工作台需要 Node.js 20.9 或更高版本。")

    environment = ROOT / ".venv"
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        subprocess.run([sys.executable, "-m", "venv", environment], check=True)
    # A checkout may already have a virtual environment without the web extras.
    probe = subprocess.run(
        [python, "-c", "import importlib.metadata as m; m.version('raccoon'); import uvicorn, fastapi, httpx, websockets"],
        cwd=ROOT, stderr=subprocess.DEVNULL,
    )
    if probe.returncode:
        subprocess.run([python, "-m", "pip", "install", "-e", ".[web]"], cwd=ROOT, check=True)
    if not (WEB / "node_modules/next/dist/bin/next").is_file():
        subprocess.run([npm, "ci"], cwd=WEB, check=True)

    args = [node, str(WEB / "scripts/start.mjs"), "--dev", "--open", *sys.argv[1:]]
    child = subprocess.Popen(args)
    try:
        return child.wait()
    except KeyboardInterrupt:
        # The launcher receives the same console interrupt and stops both servers.
        return child.wait()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.returncode)
    except KeyboardInterrupt:
        raise SystemExit(130)
