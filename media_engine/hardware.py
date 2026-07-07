"""Hardware encoder detection — engine-owned, no media_suite dependency."""

from __future__ import annotations

import platform
import shutil
import subprocess


def is_macos() -> bool:
    return platform.system() == "Darwin"


def ffmpeg_has_encoder(encoder: str) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return f" {encoder}\n" in proc.stdout or f" {encoder} " in proc.stdout


def pick_h264_encoder() -> str:
    if is_macos() and ffmpeg_has_encoder("h264_videotoolbox"):
        return "h264_videotoolbox"
    return "libx264"


def pick_hevc_encoder() -> str:
    if is_macos() and ffmpeg_has_encoder("hevc_videotoolbox"):
        return "hevc_videotoolbox"
    return "libx265"


def pick_aac_encoder() -> str:
    if is_macos() and ffmpeg_has_encoder("aac_at"):
        return "aac_at"
    return "aac"


def pick_prores_encoder() -> str:
    if is_macos() and ffmpeg_has_encoder("prores_videotoolbox"):
        return "prores_videotoolbox"
    return "prores_ks"
