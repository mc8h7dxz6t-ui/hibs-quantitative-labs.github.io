"""Cryptographic integrity and forensic manifest logging."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from media_suite.config import HASH_CHUNK_BYTES, OUTPUT_DIR


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def append_manifest(
    *,
    url: str,
    output_path: Path,
    file_hash: str,
    end_speed: str,
    end_fps: str,
    encoder_video: str,
    encoder_audio: str,
    upload_destinations: list[str] | None = None,
    source_hash: str | None = None,
    job_id: str | None = None,
) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = OUTPUT_DIR / "forensic_manifest.log"
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    upload_field = ",".join(upload_destinations) if upload_destinations else "local-only"
    line = (
        f"TIMESTAMP={stamp} | JOB={job_id or '-'} | SOURCE={url} | FILE={output_path.name} | "
        f"SOURCE_SHA256={source_hash or '-'} | OUTPUT_SHA256={file_hash} | "
        f"FPS={end_fps} | SPEED={end_speed} | VENC={encoder_video} | AENC={encoder_audio} | "
        f"UPLOAD={upload_field}\n"
    )
    with manifest.open("a", encoding="utf-8") as handle:
        handle.write(line)


def print_integrity_manifest(
    path: Path,
    container: str,
    file_hash: str,
    *,
    source_hash: str | None = None,
) -> None:
    bar = "=" * 80
    print(f"\n{bar}")
    print("                FORENSIC DIGITAL MEDIA INTEGRITY MANIFEST                ")
    print(bar)
    print(f" Target File Asset : {path}")
    print(f" Target Container  : {container.upper()}")
    if source_hash:
        print(f" Source SHA-256    : {source_hash}")
    print(f" Output SHA-256    : {file_hash}")
    print(" Status Flag       : CHAIN OF CUSTODY RECORDED (source + output hashed)")
    print(f"{bar}\n")
