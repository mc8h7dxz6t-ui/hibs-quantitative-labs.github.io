"""Zero-copy yt-dlp → FFmpeg transcode pipeline."""

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
from media_suite.integrity import append_manifest, print_integrity_manifest, sha256_file
from media_suite.notifications import notify
from media_suite.probe import StreamProfile, download_subtitles, probe_stream, safe_filename
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
    if output_format in {"mp3", "wav", "m4a"}:
        return True
    title = profile.title.lower()
    markers = ("official audio", "audio only", "lyric video", "lyrics")
    if any(m in title for m in markers):
        return True
    if profile.duration and profile.duration < 900:
        for fmt in profile.raw.get("formats") or []:
            if fmt.get("vcodec") in (None, "none") and fmt.get("acodec") not in (None, "none"):
                return True
    return False


def _finalize_asset(
    *,
    url: str,
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
        url=url,
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


def run_transcode(
    url: str,
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
    if output_format == "prores":
        return run_transcode_prores(
            url,
            profile=prores_profile,
            embed_subtitles=embed_subtitles,
            upload_after_verify=upload_after_verify,
            on_status=on_status,
        )

    if not shutil.which("yt-dlp"):
        return TranscodeResult(success=False, error="yt-dlp not found on PATH")
    if not shutil.which("ffmpeg"):
        return TranscodeResult(success=False, error="ffmpeg not found on PATH")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    def status(msg: str) -> None:
        if on_status:
            on_status(msg)

    status("Probing stream metadata…")
    profile = probe_stream(url)

    audio_only = auto_classify and _is_music_classification(profile, output_format)
    if audio_only and output_format in {"mp4", "mkv"}:
        output_format = "m4a"

    telemetry = TelemetryState()

    subtitle_files: list[Path] = []
    temp_dir: tempfile.TemporaryDirectory[str] | None = None

    if embed_subtitles and profile.has_subtitles and output_format in {"mp4", "mkv"}:
        status("Fetching subtitle tracks…")
        temp_dir = tempfile.TemporaryDirectory(prefix="media_suite_subs_")
        subtitle_files = download_subtitles(url, Path(temp_dir.name))

    status("Building hardware encoder plan…")
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

    is_video = output_format in {"mp4", "mkv", "mov"}
    ydl_format = "bestvideo+bestaudio/best" if is_video else "bestaudio"
    ydl_cmd = [
        "yt-dlp",
        "-o",
        "-",
        "--quiet",
        "--no-warnings",
        "--format",
        ydl_format,
        url,
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

    if subtitle_files:
        ffmpeg_cmd += ["-i", str(subtitle_files[0])]
        sub_codec = "mov_text" if output_format == "mp4" else "srt"
        ffmpeg_cmd += [
            "-map",
            "0:v?",
            "-map",
            "0:a?",
            "-map",
            "1:0",
            "-c:s",
            sub_codec,
        ]

    if normalize_lufs and output_format not in {"mp3", "wav"}:
        ffmpeg_cmd += ["-af", "loudnorm=I=-23:TP=-1.5:LRA=11"]

    ffmpeg_cmd += plan.args
    ffmpeg_cmd.append(str(output_path))

    status("Streaming through memory pipe → FFmpeg…")
    try:
        downloader = subprocess.Popen(
            ydl_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
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
        url=url,
        output_path=output_path,
        output_format=output_format,
        plan=plan,
        telemetry=telemetry,
        profile=profile,
        upload_after_verify=upload_after_verify,
        on_status=on_status,
    )


def run_transcode_prores(
    url: str,
    *,
    profile: str = DEFAULT_PRORES_PROFILE,
    embed_subtitles: bool = True,
    upload_after_verify: bool = UPLOAD_AFTER_VERIFY,
    on_status=None,
) -> TranscodeResult:
    """First-class ProRes mastering workflow → forensic_outputs/prores_masters/*.mov"""
    if not shutil.which("yt-dlp"):
        return TranscodeResult(success=False, error="yt-dlp not found on PATH")
    if not shutil.which("ffmpeg"):
        return TranscodeResult(success=False, error="ffmpeg not found on PATH")

    PRORES_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    def status(msg: str) -> None:
        if on_status:
            on_status(msg)

    status("Probing stream for ProRes master…")
    stream = probe_stream(url)
    telemetry = TelemetryState()

    try:
        plan = build_prores_plan(stream, profile)
    except ValueError as exc:
        return TranscodeResult(success=False, error=str(exc))

    output_path = _output_path(stream, "prores", False, plan.container_extension)

    subtitle_files: list[Path] = []
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    if embed_subtitles and stream.has_subtitles:
        status("Fetching subtitles for ProRes master…")
        temp_dir = tempfile.TemporaryDirectory(prefix="media_suite_subs_")
        subtitle_files = download_subtitles(url, Path(temp_dir.name))

    ydl_cmd = [
        "yt-dlp",
        "-o",
        "-",
        "--quiet",
        "--no-warnings",
        "--format",
        "bestvideo+bestaudio/best",
        url,
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

    if subtitle_files:
        ffmpeg_cmd += ["-i", str(subtitle_files[0]), "-map", "0:v?", "-map", "0:a?", "-map", "1:0", "-c:s", "mov_text"]

    ffmpeg_cmd += plan.args
    ffmpeg_cmd.append(str(output_path))

    status(f"ProRes encode ({profile}) via {plan.video_encoder}…")
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
        url=url,
        output_path=output_path,
        output_format="prores",
        plan=plan,
        telemetry=telemetry,
        profile=stream,
        upload_after_verify=upload_after_verify,
        on_status=on_status,
    )


def run_batch(
    urls: list[str],
    output_format: str,
    **kwargs,
) -> list[TranscodeResult]:
    results: list[TranscodeResult] = []
    for index, url in enumerate(urls, start=1):
        if "on_status" not in kwargs:
            kwargs["on_status"] = lambda m, i=index, t=len(urls): print(f"[{i}/{t}] {m}")
        results.append(run_transcode(url, output_format, **kwargs))
        time.sleep(0.5)
    return results
