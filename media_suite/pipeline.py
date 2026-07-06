"""Universal media transcode pipeline — local files, folders, and remote URLs."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from media_suite.config import (
    DEFAULT_PRORES_PROFILE,
    FORENSIC_MODE,
    OUTPUT_DIR,
    PRESERVE_SOURCE,
    PRORES_OUTPUT_DIR,
    SOURCE_ARCHIVE_DIR,
    STRICT_DOLBY_VISION,
    STRICT_HDR,
    STRICT_SURROUND,
    UPLOAD_AFTER_VERIFY,
)
from media_suite.encoders import EncoderPlan, build_encoder_plan, build_prores_plan
from media_suite.forensics import (
    archive_remote_source,
    build_custody_record,
    hash_source_if_local,
    utc_now,
    write_evidence_bundle,
)
from media_suite.input import is_remote_url
from media_suite.integrity import append_manifest, print_integrity_manifest, sha256_file
from media_suite.jobs import JobOptions
from media_suite.notifications import notify
from media_suite.platform import videotoolbox_h264_available
from media_suite.probe import (
    StreamProfile,
    download_subtitles,
    probe_local_file,
    probe_source,
)
from media_suite.streams import (
    PreservationPolicy,
    analyze_profile,
    preservation_audio_args,
    preservation_video_args,
    validate_preservation,
)
from media_suite.telemetry import TelemetryState, start_telemetry_thread
from media_suite.upload import upload_configured, upload_verified_asset


@dataclass
class TranscodeResult:
    success: bool
    output_path: Path | None = None
    sha256: str | None = None
    source_sha256: str | None = None
    error: str | None = None
    telemetry: TelemetryState | None = None
    encoder_plan: EncoderPlan | None = None
    upload_destinations: list[str] = field(default_factory=list)
    evidence_bundle: Path | None = None
    ffmpeg_command: list[str] = field(default_factory=list)


def _resolve_options(options: JobOptions | None) -> JobOptions:
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


def _output_path(
    profile: StreamProfile,
    output_format: str,
    audio_only_folder: bool,
    extension: str,
) -> Path:
    from media_suite.probe import safe_filename

    base = safe_filename(profile.title)
    if output_format == "prores":
        subdir = PRORES_OUTPUT_DIR
    elif audio_only_folder:
        subdir = OUTPUT_DIR / "audio_masters"
    else:
        subdir = OUTPUT_DIR / "video_masters"
    subdir.mkdir(parents=True, exist_ok=True)
    return subdir / f"{base}.{extension}"


def _is_music_classification(profile: StreamProfile, output_format: str) -> bool:
    if output_format in {"mp3", "wav", "m4a", "flac", "ogg"}:
        return True
    if not profile.has_video:
        return True
    if profile.is_local:
        return False
    title = profile.title.lower()
    markers = ("official audio", "audio only", "lyric video", "lyrics")
    return any(m in title for m in markers)


def _coerce_output_format(profile: StreamProfile, output_format: str, auto_classify: bool) -> str:
    video_formats = {"mp4", "mkv", "mov", "webm", "prores"}
    if not profile.has_video and output_format in video_formats:
        if auto_classify:
            return "m4a" if output_format in {"mp4", "mov", "prores"} else "mp3"
        raise ValueError("Input has no video stream; choose an audio format.")
    if auto_classify and _is_music_classification(profile, output_format):
        if output_format in {"mp4", "mkv", "mov", "webm"}:
            return "m4a"
    return output_format


def _finalize_asset(
    *,
    source: str,
    output_path: Path,
    output_format: str,
    plan: EncoderPlan,
    telemetry: TelemetryState,
    profile: StreamProfile,
    upload_after_verify: bool,
    options: JobOptions,
    job_id: str | None,
    source_sha256: str | None,
    source_archive: Path | None,
    ffmpeg_command: list[str],
    stream_analysis: dict,
    created_at: str,
    on_status,
) -> TranscodeResult:
    def status(msg: str) -> None:
        if on_status:
            on_status(msg)

    status("Computing SHA-256 integrity signature…")
    digest = sha256_file(output_path)

    upload_dests: list[str] = ["local-only"]
    if upload_after_verify and upload_configured():
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
                encoder_plan=plan,
                ffmpeg_command=ffmpeg_command,
            )

    append_manifest(
        url=source,
        output_path=output_path,
        file_hash=digest,
        end_speed=telemetry.speed,
        end_fps=telemetry.fps,
        encoder_video=plan.video_encoder,
        encoder_audio=plan.audio_encoder,
        upload_destinations=upload_dests,
        source_hash=source_sha256,
        job_id=job_id,
    )
    print_integrity_manifest(output_path, output_format, digest, source_hash=source_sha256)

    evidence_path: Path | None = None
    if options.forensic_mode or job_id:
        policy = {
            "strict_hdr": options.strict_hdr,
            "strict_dolby_vision": options.strict_dolby_vision,
            "strict_surround": options.strict_surround,
        }
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
            probe_snapshot={"title": profile.title, "raw_keys": list(profile.raw.keys())},
            ffmpeg_command=ffmpeg_command,
            encoder_video=plan.video_encoder,
            encoder_audio=plan.audio_encoder,
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
        encoder_plan=plan,
        upload_destinations=upload_dests,
        evidence_bundle=evidence_path,
        ffmpeg_command=ffmpeg_command,
    )


def _subtitle_codec(output_format: str) -> str:
    if output_format in {"mp4", "mov", "prores"}:
        return "mov_text"
    return "srt"


def _ffmpeg_maps_and_subs(
    *,
    embed_subtitles: bool,
    output_format: str,
    profile: StreamProfile,
    sidecar: Path | None,
) -> list[str]:
    if sidecar:
        return [
            "-i", str(sidecar),
            "-map", "0:v?", "-map", "0:a?", "-map", "1:0",
            "-c:s", _subtitle_codec(output_format),
        ]
    if embed_subtitles and profile.has_subtitles and profile.is_local:
        return [
            "-map", "0:v?", "-map", "0:a?", "-map", "0:s?",
            "-c:s", _subtitle_codec(output_format),
        ]
    return ["-map", "0:v?", "-map", "0:a?"]


def _execute_ffmpeg(ffmpeg_cmd: list[str], telemetry: TelemetryState) -> tuple[bool, str | None]:
    try:
        proc = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        start_telemetry_thread(proc, telemetry)
        _, stderr = proc.communicate()
        if proc.returncode != 0:
            err = (stderr or b"").decode("utf-8", errors="ignore").strip()
            return False, err or f"FFmpeg exited {proc.returncode}"
        return True, None
    except OSError as exc:
        return False, str(exc)


def _apply_preservation_to_plan(
    plan: EncoderPlan,
    analysis,
    policy: PreservationPolicy,
    output_format: str,
) -> tuple[EncoderPlan, bool]:
    """Merge strict preservation args. Returns (plan, bitstream_copy)."""
    try:
        video_args, venc, bitstream_copy = preservation_video_args(
            analysis, policy, output_format, hardware_h264=videotoolbox_h264_available()
        )
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    if bitstream_copy:
        plan = EncoderPlan(
            args=["-c:v", "copy", "-c:a", "copy"],
            video_encoder="copy",
            audio_encoder="copy",
            container_extension=plan.container_extension,
        )
        return plan, True

    if policy.strict_hdr and video_args:
        audio_part = [a for a in plan.args if a.startswith("-c:a") or a.startswith("-b:a") or a.startswith("-ac")]
        plan = EncoderPlan(
            args=video_args + (audio_part or plan.args),
            video_encoder=venc,
            audio_encoder=plan.audio_encoder,
            container_extension=plan.container_extension,
        )
    return plan, False


def _prepare_source(
    source: str,
    profile: StreamProfile,
    options: JobOptions,
) -> tuple[str | None, Path | None, StreamProfile]:
    """Returns (source_sha256, archive_path, possibly updated profile)."""
    source_sha: str | None = None
    archive: Path | None = None

    if profile.is_local and profile.local_path:
        source_sha = hash_source_if_local(profile.local_path)
        return source_sha, None, profile

    if options.preserve_source or options.forensic_mode:
        archive_dir = SOURCE_ARCHIVE_DIR / (profile.title[:40] or "remote")
        archive, source_sha = archive_remote_source(source, archive_dir)
        if archive:
            profile = probe_local_file(archive)
            profile.source = source
    return source_sha, archive, profile


def _transcode_local_file(
    source: str,
    path: Path,
    output_format: str,
    *,
    profile: StreamProfile,
    plan: EncoderPlan,
    output_path: Path,
    options: JobOptions,
    policy: PreservationPolicy,
    analysis,
    telemetry: TelemetryState,
    upload_after_verify: bool,
    job_id: str | None,
    source_sha256: str | None,
    source_archive: Path | None,
    created_at: str,
    on_status,
) -> TranscodeResult:
    def status(msg: str) -> None:
        if on_status:
            on_status(msg)

    try:
        plan, _ = _apply_preservation_to_plan(plan, analysis, policy, output_format)
    except ValueError as exc:
        return TranscodeResult(success=False, error=str(exc))

    ffmpeg_cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-stats",
        "-fflags", "+genpts+igndts", "-i", str(path),
    ]
    ffmpeg_cmd += _ffmpeg_maps_and_subs(
        embed_subtitles=options.embed_subtitles,
        output_format=output_format,
        profile=profile,
        sidecar=None,
    )
    ffmpeg_cmd += preservation_audio_args(analysis, policy)
    if options.normalize_lufs and output_format not in {"mp3", "wav", "flac", "ogg"}:
        ffmpeg_cmd += ["-af", "loudnorm=I=-23:TP=-1.5:LRA=11"]
    ffmpeg_cmd += plan.args
    ffmpeg_cmd.append(str(output_path))

    status("Transcoding local file…")
    ok, err = _execute_ffmpeg(ffmpeg_cmd, telemetry)
    if not ok:
        return TranscodeResult(success=False, error=f"FFmpeg failed: {err}", telemetry=telemetry, encoder_plan=plan)
    if not output_path.exists():
        return TranscodeResult(success=False, error="Output file was not created")

    return _finalize_asset(
        source=source, output_path=output_path, output_format=output_format,
        plan=plan, telemetry=telemetry, profile=profile,
        upload_after_verify=upload_after_verify, options=options, job_id=job_id,
        source_sha256=source_sha256, source_archive=source_archive,
        ffmpeg_command=ffmpeg_cmd,
        stream_analysis={"video": analysis.video.__dict__, "audio": analysis.audio.__dict__},
        created_at=created_at, on_status=on_status,
    )


def _transcode_remote_url(
    source: str,
    output_format: str,
    *,
    profile: StreamProfile,
    plan: EncoderPlan,
    output_path: Path,
    options: JobOptions,
    policy: PreservationPolicy,
    analysis,
    telemetry: TelemetryState,
    upload_after_verify: bool,
    job_id: str | None,
    source_sha256: str | None,
    source_archive: Path | None,
    created_at: str,
    on_status,
) -> TranscodeResult:
    def status(msg: str) -> None:
        if on_status:
            on_status(msg)

    # If source was archived, transcode from disk (forensic path)
    if source_archive and source_archive.exists():
        local_profile = probe_local_file(source_archive)
        local_profile.source = source
        return _transcode_local_file(
            source, source_archive, output_format,
            profile=local_profile, plan=plan, output_path=output_path,
            options=options, policy=policy, analysis=analyze_profile(local_profile),
            telemetry=telemetry, upload_after_verify=upload_after_verify,
            job_id=job_id, source_sha256=source_sha256, source_archive=source_archive,
            created_at=created_at, on_status=on_status,
        )

    subtitle_files: list[Path] = []
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    if options.embed_subtitles and profile.has_subtitles and output_format in {"mp4", "mkv", "mov", "webm"}:
        status("Fetching subtitles…")
        temp_dir = tempfile.TemporaryDirectory(prefix="media_suite_subs_")
        subtitle_files = download_subtitles(source, Path(temp_dir.name))

    try:
        plan, _ = _apply_preservation_to_plan(plan, analysis, policy, output_format)
    except ValueError as exc:
        return TranscodeResult(success=False, error=str(exc))

    is_video = output_format in {"mp4", "mkv", "mov", "webm", "prores"}
    ydl_cmd = [
        "yt-dlp", "-o", "-", "--quiet", "--no-warnings",
        "--format", "bestvideo+bestaudio/best" if is_video else "bestaudio",
        source,
    ]
    ffmpeg_cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-stats",
        "-fflags", "+genpts+igndts", "-i", "pipe:0",
    ]
    ffmpeg_cmd += _ffmpeg_maps_and_subs(
        embed_subtitles=options.embed_subtitles,
        output_format=output_format,
        profile=profile,
        sidecar=subtitle_files[0] if subtitle_files else None,
    )
    ffmpeg_cmd += preservation_audio_args(analysis, policy)
    if options.normalize_lufs and output_format not in {"mp3", "wav", "flac", "ogg"}:
        ffmpeg_cmd += ["-af", "loudnorm=I=-23:TP=-1.5:LRA=11"]
    ffmpeg_cmd += plan.args
    ffmpeg_cmd.append(str(output_path))

    status("Remote stream → FFmpeg pipe…")
    try:
        downloader = subprocess.Popen(ydl_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        converter = subprocess.Popen(ffmpeg_cmd, stdin=downloader.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if downloader.stdout:
            downloader.stdout.close()
        start_telemetry_thread(converter, telemetry)
        _, conv_err = converter.communicate()
        _, ydl_err = downloader.communicate()
        if downloader.returncode != 0:
            err = (ydl_err or b"").decode("utf-8", errors="ignore").strip()
            return TranscodeResult(success=False, error=f"yt-dlp failed: {err}", telemetry=telemetry)
        if converter.returncode != 0:
            err = (conv_err or b"").decode("utf-8", errors="ignore").strip()
            return TranscodeResult(success=False, error=f"FFmpeg failed: {err}", telemetry=telemetry, encoder_plan=plan)
    except OSError as exc:
        return TranscodeResult(success=False, error=str(exc))
    finally:
        if temp_dir:
            temp_dir.cleanup()

    if not output_path.exists():
        return TranscodeResult(success=False, error="Output file was not created")

    return _finalize_asset(
        source=source, output_path=output_path, output_format=output_format,
        plan=plan, telemetry=telemetry, profile=profile,
        upload_after_verify=upload_after_verify, options=options, job_id=job_id,
        source_sha256=source_sha256, source_archive=source_archive,
        ffmpeg_command=ffmpeg_cmd,
        stream_analysis={"video": analysis.video.__dict__, "audio": analysis.audio.__dict__},
        created_at=created_at, on_status=on_status,
    )


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
    opts = _resolve_options(options)
    if embed_subtitles is not None:
        opts.embed_subtitles = embed_subtitles
    if normalize_lufs is not None:
        opts.normalize_lufs = normalize_lufs
    if auto_classify is not None:
        opts.auto_classify = auto_classify
    if upload_after_verify is not None:
        opts.upload_after_verify = upload_after_verify

    if output_format == "prores":
        return run_transcode_prores(source, profile=prores_profile, options=opts, job_id=job_id, on_status=on_status)

    if not shutil.which("ffmpeg"):
        return TranscodeResult(success=False, error="ffmpeg not found on PATH")
    if is_remote_url(source) and not shutil.which("yt-dlp"):
        return TranscodeResult(success=False, error="yt-dlp required for remote URLs")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    created_at = utc_now()

    def status(msg: str) -> None:
        if on_status:
            on_status(msg)

    status("Probing input…")
    profile = probe_source(source)
    try:
        output_format = _coerce_output_format(profile, output_format, opts.auto_classify)
    except ValueError as exc:
        return TranscodeResult(success=False, error=str(exc))

    analysis = analyze_profile(profile)
    policy = PreservationPolicy(
        strict_hdr=opts.strict_hdr,
        strict_dolby_vision=opts.strict_dolby_vision,
        strict_surround=opts.strict_surround,
    )
    errors = validate_preservation(analysis, policy, output_format)
    if errors:
        return TranscodeResult(success=False, error="; ".join(errors))

    source_sha, source_archive, profile = _prepare_source(source, profile, opts)
    analysis = analyze_profile(profile)

    audio_only = opts.auto_classify and _is_music_classification(profile, output_format)
    telemetry = TelemetryState()

    status("Building encoder plan…")
    try:
        plan = build_encoder_plan(output_format, profile, prores_profile=prores_profile, normalize_lufs=opts.normalize_lufs)
    except ValueError as exc:
        return TranscodeResult(success=False, error=str(exc))

    output_path = _output_path(profile, output_format, audio_only, plan.container_extension)

    if profile.is_local and profile.local_path:
        return _transcode_local_file(
            source, profile.local_path, output_format,
            profile=profile, plan=plan, output_path=output_path, options=opts,
            policy=policy, analysis=analysis, telemetry=telemetry,
            upload_after_verify=opts.upload_after_verify, job_id=job_id,
            source_sha256=source_sha, source_archive=source_archive,
            created_at=created_at, on_status=on_status,
        )

    return _transcode_remote_url(
        source, output_format, profile=profile, plan=plan, output_path=output_path,
        options=opts, policy=policy, analysis=analysis, telemetry=telemetry,
        upload_after_verify=opts.upload_after_verify, job_id=job_id,
        source_sha256=source_sha, source_archive=source_archive,
        created_at=created_at, on_status=on_status,
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
    opts = _resolve_options(options)
    if embed_subtitles is not None:
        opts.embed_subtitles = embed_subtitles
    if upload_after_verify is not None:
        opts.upload_after_verify = upload_after_verify

    if not shutil.which("ffmpeg"):
        return TranscodeResult(success=False, error="ffmpeg not found on PATH")
    if is_remote_url(source) and not shutil.which("yt-dlp"):
        return TranscodeResult(success=False, error="yt-dlp required for remote URLs")

    PRORES_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    created_at = utc_now()

    def status(msg: str) -> None:
        if on_status:
            on_status(msg)

    status("Probing for ProRes…")
    stream = probe_source(source)
    if not stream.has_video:
        return TranscodeResult(success=False, error="ProRes requires video")

    analysis = analyze_profile(stream)
    policy = PreservationPolicy(
        strict_hdr=opts.strict_hdr,
        strict_dolby_vision=opts.strict_dolby_vision,
        strict_surround=opts.strict_surround,
    )
    errors = validate_preservation(analysis, policy, "prores")
    if errors:
        return TranscodeResult(success=False, error="; ".join(errors))

    source_sha, source_archive, stream = _prepare_source(source, stream, opts)
    telemetry = TelemetryState()

    try:
        plan = build_prores_plan(stream, profile)
    except ValueError as exc:
        return TranscodeResult(success=False, error=str(exc))

    output_path = _output_path(stream, "prores", False, plan.container_extension)
    input_path = source_archive or (stream.local_path if stream.is_local else None)

    if input_path:
        ffmpeg_cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-stats",
            "-fflags", "+genpts+igndts", "-i", str(input_path),
        ]
        ffmpeg_cmd += _ffmpeg_maps_and_subs(
            embed_subtitles=opts.embed_subtitles, output_format="prores", profile=stream, sidecar=None,
        )
        ffmpeg_cmd += plan.args
        ffmpeg_cmd.append(str(output_path))
        status(f"ProRes encode ({profile})…")
        ok, err = _execute_ffmpeg(ffmpeg_cmd, telemetry)
        if not ok:
            return TranscodeResult(success=False, error=f"FFmpeg ProRes failed: {err}", telemetry=telemetry)
    else:
        return _transcode_remote_url(
            source, "prores", profile=stream, plan=plan, output_path=output_path,
            options=opts, policy=policy, analysis=analysis, telemetry=telemetry,
            upload_after_verify=opts.upload_after_verify, job_id=job_id,
            source_sha256=source_sha, source_archive=source_archive,
            created_at=created_at, on_status=on_status,
        )

    if not output_path.exists():
        return TranscodeResult(success=False, error="ProRes output not created")

    return _finalize_asset(
        source=source, output_path=output_path, output_format="prores",
        plan=plan, telemetry=telemetry, profile=stream,
        upload_after_verify=opts.upload_after_verify, options=opts, job_id=job_id,
        source_sha256=source_sha, source_archive=source_archive,
        ffmpeg_command=ffmpeg_cmd,
        stream_analysis={"video": analysis.video.__dict__, "audio": analysis.audio.__dict__},
        created_at=created_at, on_status=on_status,
    )


def run_batch(sources: list[str], output_format: str, **kwargs) -> list[TranscodeResult]:
    results: list[TranscodeResult] = []
    for index, source in enumerate(sources, start=1):
        if "on_status" not in kwargs:
            kwargs["on_status"] = lambda m, i=index, t=len(sources): print(f"[{i}/{t}] {m}")
        results.append(run_transcode(source, output_format, **kwargs))
        time.sleep(0.5)
    return results
