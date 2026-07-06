"""Runtime paths and defaults."""

from __future__ import annotations

import os
from pathlib import Path


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name, "")
    if not value:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# Queue file (legacy text queue; SQLite is preferred for production).
WATCH_FILE = Path(os.environ.get("MEDIA_SUITE_WATCH_FILE", "download_queue.txt"))

# Converted assets and forensic bundles.
OUTPUT_DIR = Path(os.environ.get("MEDIA_SUITE_OUTPUT_DIR", "forensic_outputs"))
EVIDENCE_DIR = Path(os.environ.get("MEDIA_SUITE_EVIDENCE_DIR", str(OUTPUT_DIR / "evidence_bundles")))
SOURCE_ARCHIVE_DIR = Path(os.environ.get("MEDIA_SUITE_SOURCE_ARCHIVE_DIR", str(OUTPUT_DIR / "source_archives")))
PRORES_OUTPUT_DIR = Path(os.environ.get("MEDIA_SUITE_PRORES_DIR", str(OUTPUT_DIR / "prores_masters")))

# Persistent job database (24/7 farm).
JOBS_DB = Path(os.environ.get("MEDIA_SUITE_JOBS_DB", "forensic_outputs/jobs.db"))

DEFAULT_FORMAT = os.environ.get("MEDIA_SUITE_DEFAULT_FORMAT", "mp4")
SUBTITLE_LANGS = os.environ.get("MEDIA_SUITE_SUB_LANGS", "en.*,es.*,fr.*,de.*")
HASH_CHUNK_BYTES = 65536

# Worker farm
WORKER_CONCURRENCY = int(os.environ.get("MEDIA_SUITE_WORKER_CONCURRENCY", "2"))
WORKER_POLL_SECONDS = float(os.environ.get("MEDIA_SUITE_WORKER_POLL_SECONDS", "1.0"))
JOB_MAX_RETRIES = int(os.environ.get("MEDIA_SUITE_JOB_MAX_RETRIES", "3"))

# Forensic / legal evidence
FORENSIC_MODE = _env_bool("MEDIA_SUITE_FORENSIC_MODE")
PRESERVE_SOURCE = _env_bool("MEDIA_SUITE_PRESERVE_SOURCE")
FORENSIC_HMAC_KEY = os.environ.get("MEDIA_SUITE_FORENSIC_HMAC_KEY", "")
FORENSIC_OPERATOR_ID = os.environ.get("MEDIA_SUITE_FORENSIC_OPERATOR_ID", "")
FORENSIC_ORG_ID = os.environ.get("MEDIA_SUITE_FORENSIC_ORG_ID", "hibs-media-lab")

# Stream preservation defaults (strict = fail if requirements cannot be met)
STRICT_HDR = _env_bool("MEDIA_SUITE_STRICT_HDR")
STRICT_DOLBY_VISION = _env_bool("MEDIA_SUITE_STRICT_DOLBY_VISION")
STRICT_SURROUND = _env_bool("MEDIA_SUITE_STRICT_SURROUND")

# Upload
UPLOAD_ENABLED = _env_bool("MEDIA_SUITE_UPLOAD_ENABLED")
UPLOAD_AFTER_VERIFY = _env_bool("MEDIA_SUITE_UPLOAD_AFTER_VERIFY", default=True)
S3_BUCKET = os.environ.get("MEDIA_SUITE_S3_BUCKET", "")
S3_PREFIX = os.environ.get("MEDIA_SUITE_S3_PREFIX", "forensic/").rstrip("/") + "/"
S3_REGION = os.environ.get("MEDIA_SUITE_S3_REGION", "")
S3_ENDPOINT_URL = os.environ.get("MEDIA_SUITE_S3_ENDPOINT_URL", "")
NAS_DEST_PATH = Path(os.environ.get("MEDIA_SUITE_NAS_PATH", "")) if os.environ.get("MEDIA_SUITE_NAS_PATH") else None
RSYNC_TARGET = os.environ.get("MEDIA_SUITE_RSYNC_TARGET", "")

# Internet-facing API
API_HOST = os.environ.get("MEDIA_SUITE_API_HOST", "0.0.0.0")
API_PORT = int(os.environ.get("MEDIA_SUITE_API_PORT", "8765"))
API_TOKEN = os.environ.get("MEDIA_SUITE_API_TOKEN", os.environ.get("MEDIA_SUITE_WEBHOOK_TOKEN", ""))
API_RATE_LIMIT = os.environ.get("MEDIA_SUITE_API_RATE_LIMIT", "60/minute")
API_TRUSTED_PROXIES = os.environ.get("MEDIA_SUITE_API_TRUSTED_PROXIES", "127.0.0.1")
API_CORS_ORIGINS = os.environ.get("MEDIA_SUITE_API_CORS_ORIGINS", "*")

# Legacy webhook aliases
WEBHOOK_HOST = API_HOST
WEBHOOK_PORT = API_PORT
WEBHOOK_TOKEN = API_TOKEN

DEFAULT_PRORES_PROFILE = os.environ.get("MEDIA_SUITE_PRORES_PROFILE", "hq")

# Backward-compatible alias
FORENSIC_MODE_DEFAULT = FORENSIC_MODE
