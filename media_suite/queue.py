"""Thread-safe queue file operations."""

from __future__ import annotations

import threading
from pathlib import Path

from media_suite.config import DEFAULT_FORMAT, WATCH_FILE

_queue_lock = threading.Lock()


def ensure_watch_file() -> None:
    if not WATCH_FILE.exists():
        WATCH_FILE.write_text(
            "# One job per line: local file, folder, or URL (YouTube etc.)\n"
            "# Optional format: /path/to/video.mkv | mp4\n"
            "# ProRes: /path/to/clip.mov | prores:hq\n"
            "# Folder batch: /path/to/inbox/ | mp3\n",
            encoding="utf-8",
        )


def enqueue_source(
    source: str,
    output_format: str | None = None,
    *,
    prores_profile: str | None = None,
) -> Path:
    """Append a file path, folder, or URL to the watch queue."""
    ensure_watch_file()
    fmt = output_format or DEFAULT_FORMAT
    line = source.strip()
    if not line:
        raise ValueError("Input cannot be empty")

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


def enqueue_url(url: str, output_format: str | None = None, *, prores_profile: str | None = None) -> Path:
    """Backward-compatible alias."""
    return enqueue_source(url, output_format, prores_profile=prores_profile)


def queue_depth() -> int:
    if not WATCH_FILE.exists():
        return 0
    lines = WATCH_FILE.read_text(encoding="utf-8").splitlines()
    return sum(1 for ln in lines if ln.strip() and not ln.strip().startswith("#"))
