"""Runtime paths and defaults."""

from __future__ import annotations

import os
from pathlib import Path


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name, "")
    if not value:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# Queue file watched by the daemon; one URL or playlist per line.
WATCH_FILE = Path(os.environ.get("MEDIA_SUITE_WATCH_FILE", "download_queue.txt"))

# Converted assets and forensic manifest land here.
OUTPUT_DIR = Path(os.environ.get("MEDIA_SUITE_OUTPUT_DIR", "forensic_outputs"))

# Dedicated ProRes mastering output tree.
PRORES_OUTPUT_DIR = Path(os.environ.get("MEDIA_SUITE_PRORES_DIR", str(OUTPUT_DIR / "prores_masters")))

# Default container when the queue does not specify a format.
DEFAULT_FORMAT = os.environ.get("MEDIA_SUITE_DEFAULT_FORMAT", "mp4")

# Subtitle languages to fetch when available (regex prefixes for yt-dlp).
SUBTITLE_LANGS = os.environ.get("MEDIA_SUITE_SUB_LANGS", "en.*,es.*,fr.*,de.*")

# Chunk size for streaming SHA-256 (64 KiB).
HASH_CHUNK_BYTES = 65536

# --- Post-verification upload (S3 / NAS) ---
UPLOAD_ENABLED = _env_bool("MEDIA_SUITE_UPLOAD_ENABLED")
UPLOAD_AFTER_VERIFY = _env_bool("MEDIA_SUITE_UPLOAD_AFTER_VERIFY", default=True)

# AWS S3
S3_BUCKET = os.environ.get("MEDIA_SUITE_S3_BUCKET", "")
S3_PREFIX = os.environ.get("MEDIA_SUITE_S3_PREFIX", "forensic/").rstrip("/") + "/"
S3_REGION = os.environ.get("MEDIA_SUITE_S3_REGION", "")
S3_ENDPOINT_URL = os.environ.get("MEDIA_SUITE_S3_ENDPOINT_URL", "")  # MinIO / compatible

# NAS: local or mounted path (NFS/SMB mount point)
NAS_DEST_PATH = Path(os.environ.get("MEDIA_SUITE_NAS_PATH", "")) if os.environ.get("MEDIA_SUITE_NAS_PATH") else None

# Optional rsync target: user@host:/volume/path
RSYNC_TARGET = os.environ.get("MEDIA_SUITE_RSYNC_TARGET", "")

# --- Remote webhook API ---
WEBHOOK_HOST = os.environ.get("MEDIA_SUITE_WEBHOOK_HOST", "0.0.0.0")
WEBHOOK_PORT = int(os.environ.get("MEDIA_SUITE_WEBHOOK_PORT", "8765"))
WEBHOOK_TOKEN = os.environ.get("MEDIA_SUITE_WEBHOOK_TOKEN", "")

# Default ProRes profile: lt | 422 | hq | 4444
DEFAULT_PRORES_PROFILE = os.environ.get("MEDIA_SUITE_PRORES_PROFILE", "hq")
