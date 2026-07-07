"""Legal-grade chain of custody, evidence bundles, and manifest signing."""

from __future__ import annotations

import hashlib
import hmac
import json
import platform
import shutil
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from media_suite import __version__
from media_suite.config import EVIDENCE_DIR, FORENSIC_HMAC_KEY, FORENSIC_ORG_ID, HASH_CHUNK_BYTES
from media_suite.integrity import sha256_file


@dataclass
class ChainOfCustodyRecord:
    record_id: str
    job_id: str
    case_id: str | None
    operator_id: str | None
    organization_id: str
    source: str
    source_sha256: str | None
    source_archive_path: str | None
    output_path: str
    output_sha256: str
    output_format: str
    preservation_policy: dict[str, bool]
    stream_analysis: dict[str, Any]
    probe_snapshot: dict[str, Any]
    ffmpeg_command: list[str]
    tool_versions: dict[str, str]
    encoder_video: str
    encoder_audio: str
    upload_destinations: list[str]
    telemetry: dict[str, str]
    created_at_utc: str
    completed_at_utc: str
    manifest_sha256: str | None = None
    hmac_signature: str | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def tool_versions() -> dict[str, str]:
    versions = {
        "media_suite": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
    }
    for binary in ("ffmpeg", "ffprobe", "yt-dlp"):
        path = shutil.which(binary)
        if not path:
            versions[binary] = "not_found"
            continue
        try:
            proc = subprocess.run(
                [binary, "-version"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            first = (proc.stdout or proc.stderr or "").splitlines()
            versions[binary] = first[0] if first else path
        except (OSError, subprocess.TimeoutExpired):
            versions[binary] = path
    return versions


def hash_source_if_local(path: Path) -> str | None:
    if path.is_file():
        return sha256_file(path)
    return None


def canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def sign_manifest(payload: dict[str, Any], key: str) -> str:
    if not key:
        return ""
    digest = hmac.new(key.encode("utf-8"), canonical_json(payload).encode("utf-8"), hashlib.sha256)
    return digest.hexdigest()


def write_evidence_bundle(record: ChainOfCustodyRecord) -> Path:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    bundle_dir = EVIDENCE_DIR / record.job_id
    bundle_dir.mkdir(parents=True, exist_ok=True)

    payload = asdict(record)
    payload.pop("hmac_signature", None)
    payload.pop("manifest_sha256", None)

    manifest_sha256 = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    record.manifest_sha256 = manifest_sha256
    record.hmac_signature = sign_manifest(payload, FORENSIC_HMAC_KEY)

    manifest_path = bundle_dir / "chain_of_custody.json"
    final_payload = asdict(record)
    manifest_path.write_text(json.dumps(final_payload, indent=2), encoding="utf-8")

    # Append-only legal ledger (JSON Lines)
    ledger = EVIDENCE_DIR / "custody_ledger.jsonl"
    ledger_entry = {
        "record_id": record.record_id,
        "job_id": record.job_id,
        "case_id": record.case_id,
        "source_sha256": record.source_sha256,
        "output_sha256": record.output_sha256,
        "manifest_sha256": record.manifest_sha256,
        "hmac_signature": record.hmac_signature,
        "completed_at_utc": record.completed_at_utc,
    }
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(ledger_entry, separators=(",", ":")) + "\n")

    return manifest_path


def build_custody_record(
    *,
    job_id: str,
    case_id: str | None,
    operator_id: str | None,
    source: str,
    source_sha256: str | None,
    source_archive_path: Path | None,
    output_path: Path,
    output_format: str,
    preservation_policy: dict[str, bool],
    stream_analysis: dict[str, Any],
    probe_snapshot: dict[str, Any],
    ffmpeg_command: list[str],
    encoder_video: str,
    encoder_audio: str,
    upload_destinations: list[str],
    telemetry: dict[str, str],
    created_at_utc: str,
) -> ChainOfCustodyRecord:
    return ChainOfCustodyRecord(
        record_id=str(uuid.uuid4()),
        job_id=job_id,
        case_id=case_id,
        operator_id=operator_id,
        organization_id=FORENSIC_ORG_ID,
        source=source,
        source_sha256=source_sha256,
        source_archive_path=str(source_archive_path) if source_archive_path else None,
        output_path=str(output_path),
        output_sha256=sha256_file(output_path),
        output_format=output_format,
        preservation_policy=preservation_policy,
        stream_analysis=stream_analysis,
        probe_snapshot=probe_snapshot,
        ffmpeg_command=ffmpeg_command,
        tool_versions=tool_versions(),
        encoder_video=encoder_video,
        encoder_audio=encoder_audio,
        upload_destinations=upload_destinations,
        telemetry=telemetry,
        created_at_utc=created_at_utc,
        completed_at_utc=utc_now(),
    )


def archive_remote_source(source: str, dest_dir: Path) -> tuple[Path | None, str | None]:
    """Download remote source to disk for evidence preservation. Returns (path, sha256)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    template = str(dest_dir / "%(id)s_%(title).80s.%(ext)s")
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--format",
        "bestvideo+bestaudio/best",
        "-o",
        template,
        source,
    ]
    try:
        subprocess.run(cmd, capture_output=True, check=True, timeout=3600)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None, None

    files = sorted(dest_dir.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return None, None
    latest = files[0]
    return latest, sha256_file(latest)
