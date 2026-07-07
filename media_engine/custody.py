"""Custody ledger — hash at every boundary we control."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from media_engine.types import CustodyEvent, CustodyStage

CHUNK = 65536


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class CustodyLedger:
    """Append-only boundary log — our owned forensic trace."""

    def __init__(self, case_id: str | None = None) -> None:
        self.case_id = case_id
        self.events: list[CustodyEvent] = []

    def record(
        self,
        stage: CustodyStage,
        *,
        path: Path | None = None,
        data: bytes | None = None,
        metadata: dict | None = None,
    ) -> CustodyEvent:
        digest = None
        if path and path.is_file():
            digest = sha256_path(path)
        elif data is not None:
            digest = sha256_bytes(data)

        event = CustodyEvent(
            stage=stage,
            sha256=digest,
            path=str(path) if path else None,
            metadata=metadata or {},
            timestamp_utc=utc_now(),
        )
        self.events.append(event)
        return event

    def write_bundle(self, output_dir: Path, plan_summary: str) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        bundle = {
            "case_id": self.case_id,
            "plan_summary": plan_summary,
            "events": [
                {
                    "stage": e.stage.value,
                    "sha256": e.sha256,
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
