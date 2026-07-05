"""Cross-platform completion alerts."""

from __future__ import annotations

import shutil
import subprocess
import sys

from media_suite.platform import is_macos


def notify(title: str, subtitle: str, message: str) -> None:
    if is_macos():
        _notify_macos(title, subtitle, message)
        return
    if sys.platform.startswith("linux") and shutil.which("notify-send"):
        _notify_linux(title, message)
        return
    print(f"[notify] {title}: {subtitle} — {message}")


def _notify_macos(title: str, subtitle: str, message: str) -> None:
    safe = lambda s: s.replace('"', '\\"')
    script = (
        f'display notification "{safe(message)}" '
        f'with title "{safe(title)}" subtitle "{safe(subtitle)}" sound name "Glass"'
    )
    subprocess.run(
        ["osascript", "-e", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def _notify_linux(title: str, message: str) -> None:
    subprocess.run(
        ["notify-send", title, message],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
