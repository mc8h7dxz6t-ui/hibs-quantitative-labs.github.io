"""Post-verification upload to S3, NAS mount, or rsync target."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from media_suite.config import (
    NAS_DEST_PATH,
    RSYNC_TARGET,
    S3_BUCKET,
    S3_ENDPOINT_URL,
    S3_PREFIX,
    S3_REGION,
    UPLOAD_ENABLED,
)


@dataclass
class UploadResult:
    success: bool
    destinations: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def upload_configured() -> bool:
    if not UPLOAD_ENABLED:
        return False
    return bool(S3_BUCKET or NAS_DEST_PATH or RSYNC_TARGET)


def upload_verified_asset(local_path: Path, sha256: str) -> UploadResult:
    """Upload a verified asset to all configured destinations."""
    if not upload_configured():
        return UploadResult(success=True, destinations=["local-only"])

    destinations: list[str] = []
    errors: list[str] = []

    if S3_BUCKET:
        ok, dest, err = _upload_s3(local_path, sha256)
        if ok:
            destinations.append(dest)
        elif err:
            errors.append(err)

    if NAS_DEST_PATH:
        ok, dest, err = _upload_nas(local_path)
        if ok:
            destinations.append(dest)
        elif err:
            errors.append(err)

    if RSYNC_TARGET:
        ok, dest, err = _upload_rsync(local_path)
        if ok:
            destinations.append(dest)
        elif err:
            errors.append(err)

    return UploadResult(success=not errors, destinations=destinations, errors=errors)


def _upload_s3(local_path: Path, sha256: str) -> tuple[bool, str, str | None]:
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError:
        return False, "", "boto3 not installed — pip install boto3"

    key = f"{S3_PREFIX}{local_path.name}"
    extra_args = {
        "Metadata": {"sha256": sha256},
        "ContentType": _guess_content_type(local_path),
    }

    try:
        client_kwargs: dict = {}
        if S3_REGION:
            client_kwargs["region_name"] = S3_REGION
        if S3_ENDPOINT_URL:
            client_kwargs["endpoint_url"] = S3_ENDPOINT_URL

        client = boto3.client("s3", **client_kwargs)
        client.upload_file(str(local_path), S3_BUCKET, key, ExtraArgs=extra_args)
        return True, f"s3://{S3_BUCKET}/{key}", None
    except (BotoCoreError, ClientError, OSError) as exc:
        return False, "", f"S3 upload failed: {exc}"


def _upload_nas(local_path: Path) -> tuple[bool, str, str | None]:
    if NAS_DEST_PATH is None:
        return False, "", "NAS path not configured"
    try:
        NAS_DEST_PATH.mkdir(parents=True, exist_ok=True)
        dest = NAS_DEST_PATH / local_path.name
        shutil.copy2(local_path, dest)
        return True, str(dest), None
    except OSError as exc:
        return False, "", f"NAS copy failed: {exc}"


def _upload_rsync(local_path: Path) -> tuple[bool, str, str | None]:
    if not shutil.which("rsync"):
        return False, "", "rsync not found on PATH"

    target = RSYNC_TARGET.rstrip("/") + "/"
    cmd = [
        "rsync",
        "-av",
        "--checksum",
        str(local_path),
        target,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=3600)
        return True, f"rsync:{target}{local_path.name}", None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        return False, "", f"rsync failed: {detail}"


def _guess_content_type(path: Path) -> str:
    mapping = {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".mkv": "video/x-matroska",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
    }
    return mapping.get(path.suffix.lower(), "application/octet-stream")
