"""Start one persistent community Chrome; attach Playwright MCP without owning it."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

import portalocker


CDP_ENDPOINT = "http://127.0.0.1:9224"


def _cdp_request(version: dict, method: str, params: dict) -> dict:
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("Shared Chrome needs Node.js >=20.18.1; install Node.js before starting the browser.")
    # OpenCLI already requires Node >=20.18.1, which includes native WebSocket.
    script = """
const ws = new WebSocket(process.argv[1]);
const timer = setTimeout(() => { console.error('CDP request timed out'); ws.close(); process.exitCode = 1; }, 10000);
ws.onopen = () => ws.send(JSON.stringify({id: 1, method: process.argv[2], params: JSON.parse(process.argv[3])}));
ws.onmessage = event => {
  const message = JSON.parse(event.data);
  if (message.id !== 1) return;
  clearTimeout(timer); ws.close();
  if (message.error) { console.error(message.error.message); process.exitCode = 1; }
  else console.log(JSON.stringify(message.result));
};
ws.onerror = () => { clearTimeout(timer); ws.close(); console.error('CDP WebSocket failed'); process.exitCode = 1; };
"""
    try:
        result = subprocess.run(
            [node, "--input-type=module", "-e", script, version["webSocketDebuggerUrl"], method, json.dumps(params)],
            capture_output=True, text=True, encoding="utf-8", timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("Chrome CDP request failed; check Node.js and restart shared Chrome.") from error
    if result.returncode:
        raise RuntimeError(f"Chrome CDP request failed: {result.stderr.strip()[:400]}")
    try:
        response = json.loads(result.stdout)
    except ValueError as error:
        raise RuntimeError("Chrome CDP returned an invalid response.") from error
    if not isinstance(response, dict):
        raise RuntimeError("Chrome CDP returned an invalid response.")
    return response


def _browser_command_line(version: dict) -> list[str]:
    arguments = _cdp_request(version, "Browser.getBrowserCommandLine", {}).get("arguments")
    if not isinstance(arguments, list) or not all(isinstance(arg, str) for arg in arguments):
        raise RuntimeError("Cannot verify shared Chrome profile; Browser.getBrowserCommandLine returned no arguments.")
    return arguments


def _restore_bridge(root: Path, version: dict) -> None:
    extension = root.resolve() / ".raccoon/tools/opencli/extension"
    if not (extension / "manifest.json").is_file():
        return
    # CDP-installed unpacked extensions do not reload after Chrome restarts.
    try:
        _cdp_request(version, "Extensions.loadUnpacked", {"path": str(extension)})
    except RuntimeError as error:
        raise RuntimeError(f"Browser Bridge could not load: {error}") from error


def _version() -> dict | None:
    try:
        # Local browser control must not pass through an environment HTTP proxy.
        with build_opener(ProxyHandler({})).open(f"{CDP_ENDPOINT}/json/version", timeout=1) as response:
            data = json.load(response)
        if isinstance(data, dict) and isinstance(data.get("webSocketDebuggerUrl"), str):
            return data
    except (OSError, URLError, ValueError):
        pass
    return None


def _chrome_executable(profile: Path) -> Path:
    last_browser = profile / "Last Browser"
    if last_browser.is_file():
        raw = last_browser.read_bytes()
        try:
            path = Path(raw.decode("utf-16-le" if b"\x00" in raw else "utf-8").strip().lstrip("\ufeff"))
            if path.is_file():
                return path
        except (UnicodeError, ValueError):
            pass
    candidates = []
    if sys.platform == "win32":
        candidates = [Path(os.environ.get(key, "")) / "Google/Chrome/Application/chrome.exe" for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
    elif sys.platform == "darwin":
        candidates = [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    for name in ("google-chrome", "chromium", "chromium-browser"):
        if path := shutil.which(name):
            return Path(path)
    raise RuntimeError("Chrome executable not found; install Chrome or open the dedicated profile with Chrome first.")


def _recorded_endpoint(root: Path, version: dict | None = None) -> str:
    version = version if version is not None else _version()
    if version is None:
        raise RuntimeError("Start shared Chrome in the host first: python -m raccoon.interfaces.cli.community_browser start")
    try:
        recorded = json.loads((root / ".raccoon/browser/shared-session.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        recorded = {}
    if not isinstance(recorded, dict) or recorded.get("webSocketDebuggerUrl") != version["webSocketDebuggerUrl"]:
        raise RuntimeError("CDP port 9224 belongs to another browser; close that browser or release the port before retrying.")
    return CDP_ENDPOINT


def ensure_browser(root: Path) -> str:
    """Reuse only our recorded browser identity; never clear or duplicate login data."""
    folder = root.resolve() / ".raccoon/browser"
    folder.mkdir(parents=True, exist_ok=True)
    state_file = folder / "shared-session.json"
    with portalocker.Lock(str(folder / "shared-session.lock"), timeout=35):
        version = _version()
        if version is not None:
            endpoint = _recorded_endpoint(root, version)
            _restore_bridge(root, version)
            return endpoint

        profile = folder / "playwright"
        executable = _chrome_executable(profile)
        profile.mkdir(exist_ok=True)
        command = [
            str(executable), f"--user-data-dir={profile}",
            "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=9224",
            "--enable-automation", "--enable-unsafe-extension-debugging",
            "--no-first-run", "--no-default-browser-check", "about:blank",
        ]
        with (folder / "chrome.log").open("ab") as log:
            subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                start_new_session=os.name != "nt",
            )
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            version = _version()
            if version is not None:
                arguments = _browser_command_line(version)
                profiles = [arg.split("=", 1)[1] for arg in arguments if arg.startswith("--user-data-dir=")]
                if len(profiles) != 1 or Path(profiles[0]).resolve() != profile:
                    raise RuntimeError("CDP port 9224 belongs to another browser; its profile does not match this project.")
                state_file.write_text(json.dumps(version), encoding="utf-8")
                _restore_bridge(root, version)
                return CDP_ENDPOINT
            time.sleep(0.25)
        raise RuntimeError(
            "Chrome did not start with CDP. If the dedicated profile is open elsewhere, close that instance and retry; "
            "login data was preserved. See .raccoon/browser/chrome.log."
        )


def run_mcp(root: Path, endpoint: str) -> int:
    """Attach Playwright MCP without owning the shared Chrome lifetime."""
    npx = shutil.which("npx.cmd" if os.name == "nt" else "npx")
    if npx is None:
        raise RuntimeError("npx not found; install Node.js before starting Playwright MCP.")
    return subprocess.run([
        npx, "--yes", "@playwright/mcp@0.0.83", "--cdp-endpoint", endpoint,
        "--output-dir", str(root / ".raccoon/browser/artifacts"),
    ], stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0).returncode
