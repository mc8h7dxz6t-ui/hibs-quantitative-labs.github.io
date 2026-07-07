"""Host capability detection for encoder selection."""

from __future__ import annotations

import platform
import shutil
import subprocess


def is_macos() -> bool:
    return platform.system() == "Darwin"


def is_apple_silicon() -> bool:
    if not is_macos():
        return False
    machine = platform.machine().lower()
    return machine in {"arm64", "aarch64"}


def ffmpeg_has_encoder(encoder: str) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    needle = f" {encoder}\n"
    return needle in proc.stdout or f" {encoder} " in proc.stdout


def videotoolbox_h264_available() -> bool:
    return is_macos() and ffmpeg_has_encoder("h264_videotoolbox")


def videotoolbox_hevc_available() -> bool:
    return is_macos() and ffmpeg_has_encoder("hevc_videotoolbox")


def aac_at_available() -> bool:
    return is_macos() and ffmpeg_has_encoder("aac_at")


def prores_videotoolbox_available() -> bool:
    return is_macos() and ffmpeg_has_encoder("prores_videotoolbox")
