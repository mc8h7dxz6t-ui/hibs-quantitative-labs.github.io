"""Universal media transcode pipeline — delegates to ConversionEngine via engine_bridge."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from media_suite.config import DEFAULT_PRORES_PROFILE, FORENSIC_MODE, PRESERVE_SOURCE, UPLOAD_AFTER_VERIFY
from media_suite.engine_bridge import resolve_options, run_via_engine
from media_suite.jobs import JobOptions


@dataclass
class TranscodeResult:
    success: bool
    output_path: Path | None = None
    sha256: str | None = None
    source_sha256: str | None = None
    error: str | None = None
    telemetry: object | None = None
    encoder_plan: object | None = None
    upload_destinations: list[str] = field(default_factory=list)
    evidence_bundle: Path | None = None
    ffmpeg_command: list[str] = field(default_factory=list)


def run_transcode(
    source: str,
    output_format: str = "mp4",
    *,
    options: JobOptions | None = None,
    job_id: str | None = None,
    on_status=None,
    # Legacy kwargs
    embed_subtitles: bool | None = None,
    prores_profile: str = DEFAULT_PRORES_PROFILE,
    normalize_lufs: bool | None = None,
    auto_classify: bool | None = None,
    upload_after_verify: bool | None = None,
) -> TranscodeResult:
    opts = resolve_options(options)
    if embed_subtitles is not None:
        opts.embed_subtitles = embed_subtitles
    if normalize_lufs is not None:
        opts.normalize_lufs = normalize_lufs
    if auto_classify is not None:
        opts.auto_classify = auto_classify
    if upload_after_verify is not None:
        opts.upload_after_verify = upload_after_verify

    fmt = "prores" if output_format == "prores" else output_format
    return run_via_engine(
        source,
        fmt,
        options=opts,
        prores_profile=prores_profile,
        job_id=job_id,
        on_status=on_status,
        upload_after_verify=opts.upload_after_verify,
    )


def run_transcode_prores(
    source: str,
    *,
    profile: str = DEFAULT_PRORES_PROFILE,
    options: JobOptions | None = None,
    job_id: str | None = None,
    on_status=None,
    embed_subtitles: bool | None = None,
    upload_after_verify: bool | None = None,
) -> TranscodeResult:
    opts = resolve_options(options)
    if embed_subtitles is not None:
        opts.embed_subtitles = embed_subtitles
    if upload_after_verify is not None:
        opts.upload_after_verify = upload_after_verify

    return run_via_engine(
        source,
        "prores",
        options=opts,
        prores_profile=profile,
        job_id=job_id,
        on_status=on_status,
        upload_after_verify=opts.upload_after_verify,
    )


def run_batch(sources: list[str], output_format: str, **kwargs) -> list[TranscodeResult]:
    results: list[TranscodeResult] = []
    for index, source in enumerate(sources, start=1):
        if "on_status" not in kwargs:
            kwargs["on_status"] = lambda m, i=index, t=len(sources): print(f"[{i}/{t}] {m}")
        results.append(run_transcode(source, output_format, **kwargs))
        time.sleep(0.5)
    return results
