from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

from streamlit.runtime import get_instance
from streamlit.web import cli as streamlit_cli


def bundled_resource_path(filename: str) -> Path:
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base_path / filename


def app_data_root() -> Path:
    if os.environ.get("RUNNING_HEATMAP_DATA_DIR"):
        root = Path(os.environ["RUNNING_HEATMAP_DATA_DIR"])
        root.mkdir(parents=True, exist_ok=True)
        return root
    if sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support" / "RunningHeatmap"
    elif sys.platform.startswith("win"):
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "RunningHeatmap"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "RunningHeatmap"
    root.mkdir(parents=True, exist_ok=True)
    return root


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def exit_after_browser_disconnect() -> None:
    seen_browser_session = False
    disconnected_since: float | None = None
    while True:
        time.sleep(1.0)
        try:
            runtime = get_instance()
            active_sessions = runtime._session_mgr.num_active_sessions()
        except RuntimeError:
            continue
        if active_sessions > 0:
            seen_browser_session = True
            disconnected_since = None
            continue
        if not seen_browser_session:
            continue
        disconnected_since = disconnected_since or time.monotonic()
        if time.monotonic() - disconnected_since >= 8.0:
            os._exit(0)


def main() -> None:
    app_path = bundled_resource_path("app.py")
    data_root = app_data_root()
    port = int(os.environ.get("RUNNING_HEATMAP_PORT") or find_free_port())
    os.environ["RUNNING_HEATMAP_DATA_DIR"] = str(data_root)
    os.chdir(data_root)
    local_url = f"http://127.0.0.1:{port}"
    if os.environ.get("RUNNING_HEATMAP_NO_BROWSER") != "1":
        threading.Timer(1.5, lambda: webbrowser.open(local_url)).start()
    threading.Thread(target=exit_after_browser_disconnect, daemon=True).start()
    sys.argv = [
        "streamlit",
        "run",
        str(app_path),
        "--global.developmentMode=false",
        "--server.headless=true",
        "--server.address=127.0.0.1",
        f"--server.port={port}",
        "--server.fileWatcherType=none",
        "--server.runOnSave=false",
        "--browser.gatherUsageStats=false",
        "--client.toolbarMode=minimal",
    ]
    raise SystemExit(streamlit_cli.main())


if __name__ == "__main__":
    main()
