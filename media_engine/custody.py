"""Custody ledger — SHA-256 + MD5 (SWGDE legacy) at each boundary."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from media_engine.standards import ENABLE_MD5_DIGEST
from media_engine.types import CustodyEvent, CustodyStage

CHUNK = 65536


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def md5_path(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class CustodyLedger:
    """Append-only boundary log."""

    def __init__(self, case_id: str | None = None, job_id: str | None = None) -> None:
        self.case_id = case_id
        self.job_id = job_id
        self.events: list[CustodyEvent] = []

    def record(
        self,
        stage: CustodyStage,
        *,
        path: Path | None = None,
        metadata: dict | None = None,
    ) -> CustodyEvent:
        sha = md = None
        if path and path.is_file():
            sha = sha256_path(path)
            if ENABLE_MD5_DIGEST:
                md = md5_path(path)

        event = CustodyEvent(
            stage=stage,
            sha256=sha,
            md5=md,
            path=str(path) if path else None,
            metadata=metadata or {},
            timestamp_utc=utc_now(),
        )
        self.events.append(event)
        return event

    def write_bundle(self, output_dir: Path, plan_summary: str, ffmpeg_command: list[str]) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        bundle = {
            "case_id": self.case_id,
            "job_id": self.job_id,
            "plan_summary": plan_summary,
            "ffmpeg_command": ffmpeg_command,
            "standards": {
                "hash_primary": "SHA-256",
                "hash_legacy": "MD5" if ENABLE_MD5_DIGEST else None,
                "loudness": "EBU R128 (-23 LUFS) when requested",
                "isobmff": "faststart for mp4/mov",
            },
            "events": [
                {
                    "stage": e.stage.value,
                    "sha256": e.sha256,
                    "md5": e.md5,
                    "path": e.path,
                    "metadata": e.metadata,
                    "timestamp_utc": e.timestamp_utc,
                }
                for e in self.events
            ],
        }
        path = output_dir / "custody_trace.json"
        path.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
        return path
