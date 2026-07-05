"""Runtime paths and defaults."""

from __future__ import annotations

import os
from pathlib import Path

# Queue file watched by the daemon; one URL or playlist per line.
WATCH_FILE = Path(os.environ.get("MEDIA_SUITE_WATCH_FILE", "download_queue.txt"))

# Converted assets and forensic manifest land here.
OUTPUT_DIR = Path(os.environ.get("MEDIA_SUITE_OUTPUT_DIR", "forensic_outputs"))

# Default container when the queue does not specify a format.
DEFAULT_FORMAT = os.environ.get("MEDIA_SUITE_DEFAULT_FORMAT", "mp4")

# Subtitle languages to fetch when available (regex prefixes for yt-dlp).
SUBTITLE_LANGS = os.environ.get("MEDIA_SUITE_SUB_LANGS", "en.*,es.*,fr.*,de.*")

# Chunk size for streaming SHA-256 (64 KiB).
HASH_CHUNK_BYTES = 65536
