"""Thread-safe queue file operations."""

from __future__ import annotations

import threading
from pathlib import Path

from media_suite.config import DEFAULT_FORMAT, WATCH_FILE

_queue_lock = threading.Lock()


def ensure_watch_file() -> None:
    if not WATCH_FILE.exists():
        WATCH_FILE.write_text(
            "# Drop YouTube URLs or playlists here — one per line.\n"
            "# Optional format suffix: URL | mp3\n"
            "# ProRes master: URL | prores:hq\n",
            encoding="utf-8",
        )


def enqueue_url(url: str, output_format: str | None = None, *, prores_profile: str | None = None) -> Path:
    """Append a URL to the watch queue (format/profile optional)."""
    ensure_watch_file()
    fmt = output_format or DEFAULT_FORMAT
    line = url.strip()
    if not line:
        raise ValueError("URL cannot be empty")

    if fmt == "prores" and prores_profile:
        payload = f"{line} | prores:{prores_profile}"
    elif fmt != DEFAULT_FORMAT:
        payload = f"{line} | {fmt}"
    else:
        payload = line

    with _queue_lock:
        existing = WATCH_FILE.read_text(encoding="utf-8") if WATCH_FILE.exists() else ""
        suffix = "" if existing.endswith("\n") or not existing else "\n"
        with WATCH_FILE.open("a", encoding="utf-8") as handle:
            handle.write(f"{suffix}{payload}\n")

    return WATCH_FILE


def queue_depth() -> int:
    if not WATCH_FILE.exists():
        return 0
    lines = WATCH_FILE.read_text(encoding="utf-8").splitlines()
    return sum(1 for ln in lines if ln.strip() and not ln.strip().startswith("#"))
