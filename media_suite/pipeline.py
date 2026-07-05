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
    OUTPUT_DIR,
    PRORES_OUTPUT_DIR,
    UPLOAD_AFTER_VERIFY,
)
from media_suite.encoders import EncoderPlan, build_encoder_plan, build_prores_plan
from media_suite.input import InputKind, is_remote_url, resolve_input
from media_suite.integrity import append_manifest, print_integrity_manifest, sha256_file
from media_suite.notifications import notify
from media_suite.probe import (
    StreamProfile,
    download_subtitles,
    probe_local_file,
    probe_source,
)
from media_suite.telemetry import TelemetryState, start_telemetry_thread
from media_suite.upload import upload_configured, upload_verified_asset


@dataclass
class TranscodeResult:
    success: bool
    output_path: Path | None = None
    sha256: str | None = None
    error: str | None = None
    telemetry: TelemetryState | None = None
    encoder_plan: EncoderPlan | None = None
    upload_destinations: list[str] = field(default_factory=list)


def _output_path(
    profile: StreamProfile,
    output_format: str,
    audio_only_folder: bool,
    extension: str,
) -> Path:
    base = safe_filename_from_profile(profile)
    if output_format == "prores":
        subdir = PRORES_OUTPUT_DIR
    elif audio_only_folder:
        subdir = OUTPUT_DIR / "audio_masters"
    else:
        subdir = OUTPUT_DIR / "video_masters"
    subdir.mkdir(parents=True, exist_ok=True)
    return subdir / f"{base}.{extension}"


def safe_filename_from_profile(profile: StreamProfile) -> str:
    from media_suite.probe import safe_filename

    return safe_filename(profile.title)


def _is_music_classification(profile: StreamProfile, output_format: str) -> bool:
    if output_format in {"mp3", "wav", "m4a", "flac", "ogg"}:
        return True
    if not profile.has_video:
        return True
    if profile.is_local:
        return False
    title = profile.title.lower()
    markers = ("official audio", "audio only", "lyric video", "lyrics")
    if any(m in title for m in markers):
        return True
    if profile.duration and profile.duration < 900:
        for fmt in profile.raw.get("formats") or []:
            if fmt.get("vcodec") in (None, "none") and fmt.get("acodec") not in (None, "none"):
                return True
    return False


def _coerce_output_format(profile: StreamProfile, output_format: str, auto_classify: bool) -> str:
    video_formats = {"mp4", "mkv", "mov", "webm", "prores"}
    if not profile.has_video and output_format in video_formats:
        if auto_classify:
            return "m4a" if output_format in {"mp4", "mov", "prores"} else "mp3"
        raise ValueError(
            f"Input has no video stream; choose an audio format (mp3, wav, m4a, flac, ogg)."
        )
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
    on_status,
) -> TranscodeResult:
    def status(msg: str) -> None:
        if on_status:
            on_status(msg)

    status("Computing SHA-256 integrity signature…")
    digest = sha256_file(output_path)

    upload_dests: list[str] = ["local-only"]
    if upload_after_verify and upload_configured():
        status("Uploading verified asset to remote storage…")
        upload_result = upload_verified_asset(output_path, digest)
        if upload_result.success:
            upload_dests = upload_result.destinations or ["local-only"]
        else:
            return TranscodeResult(
                success=False,
                error="; ".join(upload_result.errors) or "Upload failed",
                output_path=output_path,
                sha256=digest,
                telemetry=telemetry,
                encoder_plan=plan,
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
    )
    print_integrity_manifest(output_path, output_format, digest)
    if upload_dests != ["local-only"]:
        print(f"[+] Uploaded to: {', '.join(upload_dests)}")

    notify(
        "Forensic Media Suite",
        "Conversion complete",
        f"{profile.title[:48]} → {output_path.name}",
    )

    return TranscodeResult(
        success=True,
        output_path=output_path,
        sha256=digest,
        telemetry=telemetry,
        encoder_plan=plan,
        upload_destinations=upload_dests,
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
    args: list[str] = []
    if sidecar:
        args += [
            "-i",
            str(sidecar),
            "-map",
            "0:v?",
            "-map",
            "0:a?",
            "-map",
            "1:0",
            "-c:s",
            _subtitle_codec(output_format),
        ]
    elif embed_subtitles and profile.has_subtitles and profile.is_local:
        args += [
            "-map",
            "0:v?",
            "-map",
            "0:a?",
            "-map",
            "0:s?",
            "-c:s",
            _subtitle_codec(output_format),
        ]
    else:
        args += ["-map", "0:v?", "-map", "0:a?"]
    return args


def _execute_ffmpeg(
    ffmpeg_cmd: list[str],
    telemetry: TelemetryState,
) -> tuple[bool, str | None]:
    try:
        proc = subprocess.Popen(
            ffmpeg_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        start_telemetry_thread(proc, telemetry)
        _, stderr = proc.communicate()
        if proc.returncode != 0:
            err = (stderr or b"").decode("utf-8", errors="ignore").strip()
            return False, err or f"FFmpeg exited {proc.returncode}"
        return True, None
    except OSError as exc:
        return False, str(exc)


def _transcode_local_file(
    source: str,
    path: Path,
    output_format: str,
    *,
    profile: StreamProfile,
    plan: EncoderPlan,
    output_path: Path,
    embed_subtitles: bool,
    normalize_lufs: bool,
    telemetry: TelemetryState,
    upload_after_verify: bool,
    on_status,
) -> TranscodeResult:
    def status(msg: str) -> None:
        if on_status:
            on_status(msg)

    ffmpeg_cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-stats",
        "-fflags",
        "+genpts+igndts",
        "-i",
        str(path),
    ]
    ffmpeg_cmd += _ffmpeg_maps_and_subs(
        embed_subtitles=embed_subtitles,
        output_format=output_format,
        profile=profile,
        sidecar=None,
    )
    if normalize_lufs and output_format not in {"mp3", "wav", "flac", "ogg"}:
        ffmpeg_cmd += ["-af", "loudnorm=I=-23:TP=-1.5:LRA=11"]
    ffmpeg_cmd += plan.args
    ffmpeg_cmd.append(str(output_path))

    status("Transcoding local file with FFmpeg…")
    ok, err = _execute_ffmpeg(ffmpeg_cmd, telemetry)
    if not ok:
        return TranscodeResult(
            success=False,
            error=f"FFmpeg failed: {err}",
            telemetry=telemetry,
            encoder_plan=plan,
        )
    if not output_path.exists():
        return TranscodeResult(success=False, error="Output file was not created")

    return _finalize_asset(
        source=source,
        output_path=output_path,
        output_format=output_format,
        plan=plan,
        telemetry=telemetry,
        profile=profile,
        upload_after_verify=upload_after_verify,
        on_status=on_status,
    )


def _transcode_remote_url(
    source: str,
    output_format: str,
    *,
    profile: StreamProfile,
    plan: EncoderPlan,
    output_path: Path,
    embed_subtitles: bool,
    normalize_lufs: bool,
    telemetry: TelemetryState,
    on_status,
    upload_after_verify: bool,
) -> TranscodeResult:
    def status(msg: str) -> None:
        if on_status:
            on_status(msg)

    subtitle_files: list[Path] = []
    temp_dir: tempfile.TemporaryDirectory[str] | None = None

    if embed_subtitles and profile.has_subtitles and output_format in {"mp4", "mkv", "mov", "webm"}:
        status("Fetching subtitle tracks…")
        temp_dir = tempfile.TemporaryDirectory(prefix="media_suite_subs_")
        subtitle_files = download_subtitles(source, Path(temp_dir.name))

    is_video = output_format in {"mp4", "mkv", "mov", "webm", "prores"}
    ydl_format = "bestvideo+bestaudio/best" if is_video else "bestaudio"
    ydl_cmd = [
        "yt-dlp",
        "-o",
        "-",
        "--quiet",
        "--no-warnings",
        "--format",
        ydl_format,
        source,
    ]

    ffmpeg_cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-stats",
        "-fflags",
        "+genpts+igndts",
        "-i",
        "pipe:0",
    ]
    sidecar = subtitle_files[0] if subtitle_files else None
    ffmpeg_cmd += _ffmpeg_maps_and_subs(
        embed_subtitles=embed_subtitles,
        output_format=output_format,
        profile=profile,
        sidecar=sidecar,
    )

    if normalize_lufs and output_format not in {"mp3", "wav", "flac", "ogg"}:
        ffmpeg_cmd += ["-af", "loudnorm=I=-23:TP=-1.5:LRA=11"]
    ffmpeg_cmd += plan.args
    ffmpeg_cmd.append(str(output_path))

    status("Streaming remote URL → FFmpeg memory pipe…")
    try:
        downloader = subprocess.Popen(ydl_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        converter = subprocess.Popen(
            ffmpeg_cmd,
            stdin=downloader.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if downloader.stdout:
            downloader.stdout.close()

        start_telemetry_thread(converter, telemetry)
        _, conv_err = converter.communicate()
        _, ydl_err = downloader.communicate()

        if downloader.returncode != 0:
            err = (ydl_err or b"").decode("utf-8", errors="ignore").strip()
            return TranscodeResult(
                success=False,
                error=f"yt-dlp failed: {err or downloader.returncode}",
                telemetry=telemetry,
            )
        if converter.returncode != 0:
            err = (conv_err or b"").decode("utf-8", errors="ignore").strip()
            return TranscodeResult(
                success=False,
                error=f"FFmpeg failed: {err or converter.returncode}",
                telemetry=telemetry,
                encoder_plan=plan,
            )
    except OSError as exc:
        return TranscodeResult(success=False, error=str(exc))
    finally:
        if temp_dir:
            temp_dir.cleanup()

    if not output_path.exists():
        return TranscodeResult(success=False, error="Output file was not created")

    return _finalize_asset(
        source=source,
        output_path=output_path,
        output_format=output_format,
        plan=plan,
        telemetry=telemetry,
        profile=profile,
        upload_after_verify=upload_after_verify,
        on_status=on_status,
    )


def run_transcode(
    source: str,
    output_format: str = "mp4",
    *,
    embed_subtitles: bool = True,
    prores_profile: str = DEFAULT_PRORES_PROFILE,
    prores_archive: bool = False,
    normalize_lufs: bool = False,
    auto_classify: bool = True,
    upload_after_verify: bool = UPLOAD_AFTER_VERIFY,
    on_status=None,
) -> TranscodeResult:
    """Transcode any local media file or remote URL to the target format."""
    if not shutil.which("ffmpeg"):
        return TranscodeResult(success=False, error="ffmpeg not found on PATH")

    if output_format == "prores":
        return run_transcode_prores(
            source,
            profile=prores_profile,
            embed_subtitles=embed_subtitles,
            upload_after_verify=upload_after_verify,
            on_status=on_status,
        )

    if is_remote_url(source) and not shutil.which("yt-dlp"):
        return TranscodeResult(success=False, error="yt-dlp not found on PATH (required for remote URLs)")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    def status(msg: str) -> None:
        if on_status:
            on_status(msg)

    status("Probing input…")
    profile = probe_source(source)

    try:
        output_format = _coerce_output_format(profile, output_format, auto_classify)
    except ValueError as exc:
        return TranscodeResult(success=False, error=str(exc))

    audio_only = auto_classify and _is_music_classification(profile, output_format)
    telemetry = TelemetryState()

    status("Building encoder plan…")
    try:
        plan = build_encoder_plan(
            output_format,
            profile,
            prores_profile=prores_profile,
            prores_archive=prores_archive,
            normalize_lufs=normalize_lufs,
        )
    except ValueError as exc:
        return TranscodeResult(success=False, error=str(exc))

    output_path = _output_path(profile, output_format, audio_only, plan.container_extension)

    if profile.is_local and profile.local_path:
        return _transcode_local_file(
            source,
            profile.local_path,
            output_format,
            profile=profile,
            plan=plan,
            output_path=output_path,
            embed_subtitles=embed_subtitles,
            normalize_lufs=normalize_lufs,
            telemetry=telemetry,
            upload_after_verify=upload_after_verify,
            on_status=on_status,
        )

    return _transcode_remote_url(
        source,
        output_format,
        profile=profile,
        plan=plan,
        output_path=output_path,
        embed_subtitles=embed_subtitles,
        normalize_lufs=normalize_lufs,
        telemetry=telemetry,
        on_status=on_status,
        upload_after_verify=upload_after_verify,
    )


def run_transcode_prores(
    source: str,
    *,
    profile: str = DEFAULT_PRORES_PROFILE,
    embed_subtitles: bool = True,
    upload_after_verify: bool = UPLOAD_AFTER_VERIFY,
    on_status=None,
) -> TranscodeResult:
    """ProRes mastering for local files or remote URLs."""
    if not shutil.which("ffmpeg"):
        return TranscodeResult(success=False, error="ffmpeg not found on PATH")
    if is_remote_url(source) and not shutil.which("yt-dlp"):
        return TranscodeResult(success=False, error="yt-dlp not found on PATH (required for remote URLs)")

    PRORES_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    def status(msg: str) -> None:
        if on_status:
            on_status(msg)

    status("Probing input for ProRes master…")
    stream = probe_source(source)
    if not stream.has_video:
        return TranscodeResult(success=False, error="ProRes requires a video source")

    telemetry = TelemetryState()
    try:
        plan = build_prores_plan(stream, profile)
    except ValueError as exc:
        return TranscodeResult(success=False, error=str(exc))

    output_path = _output_path(stream, "prores", False, plan.container_extension)

    if stream.is_local and stream.local_path:
        ffmpeg_cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-stats",
            "-fflags",
            "+genpts+igndts",
            "-i",
            str(stream.local_path),
        ]
        ffmpeg_cmd += _ffmpeg_maps_and_subs(
            embed_subtitles=embed_subtitles,
            output_format="prores",
            profile=stream,
            sidecar=None,
        )
        ffmpeg_cmd += plan.args
        ffmpeg_cmd.append(str(output_path))
        status(f"ProRes local encode ({profile})…")
        ok, err = _execute_ffmpeg(ffmpeg_cmd, telemetry)
        if not ok:
            return TranscodeResult(
                success=False,
                error=f"FFmpeg ProRes failed: {err}",
                telemetry=telemetry,
                encoder_plan=plan,
            )
    else:
        subtitle_files: list[Path] = []
        temp_dir: tempfile.TemporaryDirectory[str] | None = None
        if embed_subtitles and stream.has_subtitles:
            status("Fetching subtitles…")
            temp_dir = tempfile.TemporaryDirectory(prefix="media_suite_subs_")
            subtitle_files = download_subtitles(source, Path(temp_dir.name))

        ydl_cmd = [
            "yt-dlp",
            "-o",
            "-",
            "--quiet",
            "--no-warnings",
            "--format",
            "bestvideo+bestaudio/best",
            source,
        ]
        ffmpeg_cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-stats",
            "-fflags",
            "+genpts+igndts",
            "-i",
            "pipe:0",
        ]
        ffmpeg_cmd += _ffmpeg_maps_and_subs(
            embed_subtitles=embed_subtitles,
            output_format="prores",
            profile=stream,
            sidecar=subtitle_files[0] if subtitle_files else None,
        )
        ffmpeg_cmd += plan.args
        ffmpeg_cmd.append(str(output_path))

        status(f"ProRes remote encode ({profile})…")
        try:
            downloader = subprocess.Popen(ydl_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            converter = subprocess.Popen(
                ffmpeg_cmd,
                stdin=downloader.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
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
                return TranscodeResult(
                    success=False,
                    error=f"FFmpeg ProRes failed: {err}",
                    telemetry=telemetry,
                    encoder_plan=plan,
                )
        except OSError as exc:
            return TranscodeResult(success=False, error=str(exc))
        finally:
            if temp_dir:
                temp_dir.cleanup()

    if not output_path.exists():
        return TranscodeResult(success=False, error="ProRes output was not created")

    return _finalize_asset(
        source=source,
        output_path=output_path,
        output_format="prores",
        plan=plan,
        telemetry=telemetry,
        profile=stream,
        upload_after_verify=upload_after_verify,
        on_status=on_status,
    )


def run_batch(
    sources: list[str],
    output_format: str,
    **kwargs,
) -> list[TranscodeResult]:
    results: list[TranscodeResult] = []
    for index, source in enumerate(sources, start=1):
        if "on_status" not in kwargs:
            kwargs["on_status"] = lambda m, i=index, t=len(sources): print(f"[{i}/{t}] {m}")
        results.append(run_transcode(source, output_format, **kwargs))
        time.sleep(0.5)
    return results
