"""Bridge media_suite workflow → media_engine ConversionEngine (single integration point)."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from media_engine.engine import ConversionEngine
from media_engine.types import ConversionMode, ConversionPlan, ConversionRequest, StreamKind
from media_suite.config import (
    DEFAULT_PRORES_PROFILE,
    EVIDENCE_DIR,
    FORENSIC_MODE,
    OUTPUT_DIR,
    PRESERVE_SOURCE,
    PRORES_OUTPUT_DIR,
    SOURCE_ARCHIVE_DIR,
    STRICT_DOLBY_VISION,
    STRICT_HDR,
    STRICT_SURROUND,
)
from media_suite.encoders import EncoderPlan
from media_suite.forensics import (
    archive_remote_source,
    build_custody_record,
    hash_source_if_local,
    utc_now,
    write_evidence_bundle,
)
from media_suite.input import is_remote_url
from media_suite.integrity import append_manifest, print_integrity_manifest
from media_suite.jobs import JobOptions
from media_suite.notifications import notify
from media_suite.probe import StreamProfile, probe_source, safe_filename
from media_suite.telemetry import TelemetryState
from media_suite.upload import upload_configured, upload_verified_asset


def resolve_options(options: JobOptions | None) -> JobOptions:
    base = options or JobOptions()
    if FORENSIC_MODE and not base.forensic_mode:
        base.forensic_mode = True
    if PRESERVE_SOURCE and not base.preserve_source:
        base.preserve_source = True
    if STRICT_HDR:
        base.strict_hdr = True
    if STRICT_DOLBY_VISION:
        base.strict_dolby_vision = True
    if STRICT_SURROUND:
        base.strict_surround = True
    return base


def is_music_classification(profile: StreamProfile, output_format: str) -> bool:
    if output_format in {"mp3", "wav", "m4a", "flac", "ogg"}:
        return True
    if not profile.has_video:
        return True
    if profile.is_local:
        return False
    title = profile.title.lower()
    markers = ("official audio", "audio only", "lyric video", "lyrics")
    return any(m in title for m in markers)


def coerce_output_format(profile: StreamProfile, output_format: str, auto_classify: bool) -> str:
    video_formats = {"mp4", "mkv", "mov", "webm", "prores"}
    if not profile.has_video and output_format in video_formats:
        if auto_classify:
            return "m4a" if output_format in {"mp4", "mov", "prores"} else "mp3"
        raise ValueError("Input has no video stream; choose an audio format.")
    if auto_classify and is_music_classification(profile, output_format):
        if output_format in {"mp4", "mkv", "mov", "webm"}:
            return "m4a"
    return output_format


def resolve_output_path(
    profile: StreamProfile,
    output_format: str,
    *,
    audio_only_folder: bool,
    extension: str | None = None,
) -> Path:
    base = safe_filename(profile.title)
    ext = extension or output_format
    if output_format == "prores":
        subdir = PRORES_OUTPUT_DIR
        ext = "mov"
    elif audio_only_folder:
        subdir = OUTPUT_DIR / "audio_masters"
    else:
        subdir = OUTPUT_DIR / "video_masters"
    subdir.mkdir(parents=True, exist_ok=True)
    return subdir / f"{base}.{ext}"


def materialize_source(
    source: str,
    profile: StreamProfile,
    options: JobOptions,
    *,
    on_status=None,
) -> tuple[Path, str | None, Path | None]:
    """
    Ensure a local file for the engine. Returns (local_path, source_sha256, archive_path).
    Remote URLs are always materialized — the engine requires a file on disk.
    """
    def status(msg: str) -> None:
        if on_status:
            on_status(msg)

    if profile.is_local and profile.local_path:
        return profile.local_path, hash_source_if_local(profile.local_path), None

    if not shutil.which("yt-dlp"):
        raise RuntimeError("yt-dlp required for remote URLs")

    if options.preserve_source or options.forensic_mode:
        status("Archiving remote source for custody…")
        archive_dir = SOURCE_ARCHIVE_DIR / (safe_filename(profile.title)[:40] or "remote")
        archive, source_sha = archive_remote_source(source, archive_dir)
        if archive:
            return archive, source_sha, archive

    status("Downloading remote source…")
    temp_dir = tempfile.mkdtemp(prefix="media_suite_src_")
    template = str(Path(temp_dir) / "%(id)s.%(ext)s")
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--format",
        "bestvideo+bestaudio/best",
        "-o",
        template,
        source,
    ]
    subprocess.run(cmd, capture_output=True, check=True, timeout=3600)
    files = sorted(Path(temp_dir).glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise RuntimeError("yt-dlp produced no output file")
    latest = files[0]
    return latest, hash_source_if_local(latest), None


def options_to_request(
    *,
    local_path: Path,
    output_format: str,
    output_path: Path,
    options: JobOptions,
    prores_profile: str,
    job_id: str | None,
) -> ConversionRequest:
    return ConversionRequest(
        input_path=local_path,
        output_format=output_format,
        output_path=output_path,
        require_hdr_metadata=options.strict_hdr,
        require_surround_audio=options.strict_surround,
        require_dolby_vision_copy=options.strict_dolby_vision,
        embed_subtitles=options.embed_subtitles,
        normalize_lufs=options.normalize_lufs,
        prores_profile=prores_profile,
        case_id=options.case_id,
        job_id=job_id,
    )


def encoders_from_plan(plan: ConversionPlan) -> EncoderPlan:
    video_enc = "copy"
    audio_enc = "copy"
    for mapping in plan.mappings:
        if mapping.kind == StreamKind.VIDEO:
            if mapping.mode == ConversionMode.BITSTREAM_COPY:
                video_enc = "copy"
            elif mapping.output_codec == "prores":
                video_enc = mapping.output_codec
            else:
                video_enc = mapping.output_codec or video_enc
        elif mapping.kind == StreamKind.AUDIO:
            if mapping.mode == ConversionMode.BITSTREAM_COPY:
                audio_enc = "copy"
            else:
                audio_enc = mapping.output_codec or audio_enc

    ext = plan.output_path.suffix.lstrip(".") or plan.output_format
    return EncoderPlan(
        args=plan.ffmpeg_args,
        video_encoder=video_enc,
        audio_encoder=audio_enc,
        container_extension=ext,
    )


def stream_analysis_from_plan(plan: ConversionPlan) -> dict:
    catalog = plan.catalog
    video = catalog.primary_video
    audio = catalog.primary_audio
    return {
        "video": {
            "codec": video.codec if video else None,
            "color_science": video.color_science.value if video else None,
            "width": video.width if video else 0,
            "height": video.height if video else 0,
        },
        "audio": {
            "codec": audio.codec if audio else None,
            "channels": audio.channels if audio else 0,
            "channel_layout": audio.channel_layout if audio else "",
        },
        "plan_summary": plan.summary(),
        "preservation_notes": plan.preservation_notes,
        "warnings": plan.warnings,
    }


def finalize_engine_result(
    *,
    source: str,
    output_format: str,
    profile: StreamProfile,
    options: JobOptions,
    job_id: str | None,
    source_sha256: str | None,
    source_archive: Path | None,
    engine_result,
    telemetry: TelemetryState,
    upload_after_verify: bool,
    created_at: str,
    on_status=None,
) -> "TranscodeResult":
    from media_suite.pipeline import TranscodeResult

    def status(msg: str) -> None:
        if on_status:
            on_status(msg)

    if not engine_result.success:
        return TranscodeResult(
            success=False,
            error=engine_result.error,
            telemetry=telemetry,
            ffmpeg_command=engine_result.ffmpeg_command,
        )

    output_path = engine_result.output_path
    digest = engine_result.output_sha256
    plan = engine_result.plan
    encoder_plan = encoders_from_plan(plan) if plan else None

    upload_dests: list[str] = ["local-only"]
    if upload_after_verify and upload_configured() and output_path and digest:
        status("Uploading verified asset…")
        upload_result = upload_verified_asset(output_path, digest)
        if upload_result.success:
            upload_dests = upload_result.destinations or ["local-only"]
        else:
            return TranscodeResult(
                success=False,
                error="; ".join(upload_result.errors) or "Upload failed",
                output_path=output_path,
                sha256=digest,
                source_sha256=source_sha256,
                telemetry=telemetry,
                encoder_plan=encoder_plan,
                ffmpeg_command=engine_result.ffmpeg_command,
            )

    append_manifest(
        url=source,
        output_path=output_path,
        file_hash=digest,
        end_speed=telemetry.speed,
        end_fps=telemetry.fps,
        encoder_video=encoder_plan.video_encoder if encoder_plan else "unknown",
        encoder_audio=encoder_plan.audio_encoder if encoder_plan else "unknown",
        upload_destinations=upload_dests,
        source_hash=source_sha256,
        job_id=job_id,
    )
    print_integrity_manifest(output_path, output_format, digest, source_hash=source_sha256)

    evidence_path: Path | None = engine_result.custody_bundle
    if options.forensic_mode or job_id:
        policy = {
            "strict_hdr": options.strict_hdr,
            "strict_dolby_vision": options.strict_dolby_vision,
            "strict_surround": options.strict_surround,
        }
        stream_analysis = stream_analysis_from_plan(plan) if plan else {}
        record = build_custody_record(
            job_id=job_id or f"adhoc-{int(time.time())}",
            case_id=options.case_id,
            operator_id=options.operator_id,
            source=source,
            source_sha256=source_sha256,
            source_archive_path=source_archive,
            output_path=output_path,
            output_format=output_format,
            preservation_policy=policy,
            stream_analysis=stream_analysis,
            probe_snapshot={"title": profile.title, "format": plan.catalog.format_name if plan else None},
            ffmpeg_command=engine_result.ffmpeg_command,
            encoder_video=encoder_plan.video_encoder if encoder_plan else "unknown",
            encoder_audio=encoder_plan.audio_encoder if encoder_plan else "unknown",
            upload_destinations=upload_dests,
            telemetry={"fps": telemetry.fps, "speed": telemetry.speed},
            created_at_utc=created_at,
        )
        evidence_path = write_evidence_bundle(record)
        status(f"Evidence bundle written: {evidence_path}")

    notify("Forensic Media Suite", "Conversion complete", f"{profile.title[:48]} → {output_path.name}")

    return TranscodeResult(
        success=True,
        output_path=output_path,
        sha256=digest,
        source_sha256=source_sha256,
        telemetry=telemetry,
        encoder_plan=encoder_plan,
        upload_destinations=upload_dests,
        evidence_bundle=evidence_path,
        ffmpeg_command=engine_result.ffmpeg_command,
    )


def run_via_engine(
    source: str,
    output_format: str,
    *,
    options: JobOptions,
    prores_profile: str = DEFAULT_PRORES_PROFILE,
    job_id: str | None = None,
    on_status=None,
    upload_after_verify: bool = True,
) -> "TranscodeResult":
    """Single integration point: materialize → ConversionEngine → finalize."""
    from media_suite.pipeline import TranscodeResult

    if not shutil.which("ffmpeg"):
        return TranscodeResult(success=False, error="ffmpeg not found on PATH")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    created_at = utc_now()
    telemetry = TelemetryState()

    def status(msg: str) -> None:
        if on_status:
            on_status(msg)

    status("Probing input…")
    profile = probe_source(source)
    try:
        output_format = coerce_output_format(profile, output_format, options.auto_classify)
    except ValueError as exc:
        return TranscodeResult(success=False, error=str(exc))

    try:
        local_path, source_sha, source_archive = materialize_source(
            source, profile, options, on_status=on_status
        )
    except (RuntimeError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return TranscodeResult(success=False, error=str(exc))

    audio_only = options.auto_classify and is_music_classification(profile, output_format)
    ext = "mov" if output_format == "prores" else output_format
    output_path = resolve_output_path(profile, output_format, audio_only_folder=audio_only, extension=ext)

    request = options_to_request(
        local_path=local_path,
        output_format=output_format,
        output_path=output_path,
        options=options,
        prores_profile=prores_profile,
        job_id=job_id,
    )

    custody_dir = EVIDENCE_DIR / (job_id or f"adhoc-{int(time.time())}")
    engine = ConversionEngine(custody_dir=custody_dir)
    engine_result = engine.convert(request, telemetry=telemetry, on_status=on_status)

    return finalize_engine_result(
        source=source,
        output_format=output_format,
        profile=profile,
        options=options,
        job_id=job_id,
        source_sha256=source_sha,
        source_archive=source_archive,
        engine_result=engine_result,
        telemetry=telemetry,
        upload_after_verify=upload_after_verify,
        created_at=created_at,
        on_status=on_status,
    )
