"""HIBS Media Studio — installable desktop application."""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start_server(host: str, port: int) -> None:
    import uvicorn

    uvicorn.run(
        "desktop.server:desktop_app",
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
    )


class DesktopApi:
    """JS bridge for native file dialogs when running inside pywebview."""

    def pick_files(self) -> list[str]:
        try:
            import webview
        except ImportError:
            return []

        window = webview.windows[0] if webview.windows else None
        if not window:
            return []

        result = window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=False,
            file_types=("All files (*.*)", "Media (*.mp4;*.mkv;*.mov;*.wav;*.flac;*.mp3)",),
        )
        if not result:
            return []
        if isinstance(result, (list, tuple)):
            return [str(p) for p in result]
        return [str(result)]

    def pick_folder(self) -> str | None:
        try:
            import webview
        except ImportError:
            return None

        window = webview.windows[0] if webview.windows else None
        if not window:
            return None

        result = window.create_file_dialog(webview.FOLDER_DIALOG)
        return str(result) if result else None

    def reveal(self, path: str) -> bool:
        target = Path(path)
        if not target.exists():
            return False
        import platform
        import subprocess

        system = platform.system()
        try:
            if system == "Darwin":
                subprocess.run(["open", "-R", str(target)], check=False)
            elif system == "Windows":
                subprocess.run(["explorer", "/select,", str(target)], check=False)
            else:
                subprocess.run(["xdg-open", str(target.parent)], check=False)
            return True
        except OSError:
            return False


def launch_desktop(host: str = "127.0.0.1", port: int | None = None, browser_fallback: bool = False) -> int:
    port = port or find_free_port()
    url = f"http://{host}:{port}"

    thread = threading.Thread(target=start_server, args=(host, port), daemon=True)
    thread.start()

    # Wait for server readiness
    for _ in range(50):
        try:
            with socket.create_connection((host, port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.1)

    api = DesktopApi()

    try:
        import webview

        window = webview.create_window(
            "HIBS Media Studio",
            url,
            width=1280,
            height=860,
            min_size=(960, 640),
            background_color="#06080f",
            js_api=api,
        )
        webview.start(debug=False)
        return 0
    except Exception as exc:
        if not browser_fallback:
            print(f"Desktop shell unavailable: {exc}", file=sys.stderr)
            print("Tip: pip install pywebview and GTK/Qt, or use --browser", file=sys.stderr)
            return 1
        print(f"Desktop shell unavailable ({exc}) — opening browser at {url}")
        webbrowser.open(url)
        try:
            while thread.is_alive():
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HIBS Media Studio — forensic media desktop")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=None, help="Fixed port (default: random)")
    parser.add_argument("--browser", action="store_true", help="Open in browser if native shell unavailable")
    parser.add_argument("--serve-only", action="store_true", help="Run local API only (no window)")
    args = parser.parse_args(argv)
    if args.serve_only:
        port = args.port or find_free_port()
        print(f"HIBS Media Studio API at http://{args.host}:{port}")
        start_server(args.host, port)
        return 0
    return launch_desktop(host=args.host, port=args.port, browser_fallback=args.browser)


if __name__ == "__main__":
    raise SystemExit(main())
