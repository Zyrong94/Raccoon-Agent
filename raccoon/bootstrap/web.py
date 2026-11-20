"""python -m raccoon.interfaces.web --project . --port 8765"""

from pathlib import Path

from raccoon.bootstrap.web_app import create_web_app
from raccoon.config import load_config


def run_web_server(
    *,
    project: str | Path = ".",
    data_root: str | Path | None = None,
    model: str | None = None,
    session_id: str | None = None,
    port: int = 8765,
    open_browser: bool = False,
) -> None:
    """Start the local web workbench and block until the server stops."""

    import uvicorn

    url = f"http://127.0.0.1:{port}/"
    app = create_web_app(
        project_path=project, data_root=data_root, session_id=session_id,
        app_config=load_config(project_root=project), model_ref=model,
        initial_projectless=session_id is None,
    )
    if open_browser:
        print(f"Raccoon web workbench: {url}")
        _open_browser_when_ready(url)
    uvicorn.run(app, host="127.0.0.1", port=port, access_log=False)

def _open_browser_when_ready(url: str) -> None:
    import threading
    import time
    import urllib.request
    import webbrowser

    def wait_and_open() -> None:
        for _ in range(50):
            try:
                urllib.request.urlopen(url, timeout=1).close()
                break
            except Exception:
                time.sleep(0.2)
        webbrowser.open(url)

    threading.Thread(target=wait_and_open, daemon=True).start()
