"""FFmpeg encoder matrices with Apple Silicon hardware paths."""

from __future__ import annotations

from dataclasses import dataclass

from media_suite.platform import (
    aac_at_available,
    prores_videotoolbox_available,
    videotoolbox_h264_available,
    videotoolbox_hevc_available,
)
from media_suite.probe import StreamProfile

PRORES_PROFILES = {
    "lt": {"videotoolbox": "1", "prores_ks": "1", "label": "ProRes 422 LT"},
    "422": {"videotoolbox": "2", "prores_ks": "2", "label": "ProRes 422"},
    "hq": {"videotoolbox": "3", "prores_ks": "3", "label": "ProRes 422 HQ"},
    "4444": {"videotoolbox": "4", "prores_ks": "4", "label": "ProRes 4444"},
}


@dataclass
class EncoderPlan:
    args: list[str]
    video_encoder: str
    audio_encoder: str
    container_extension: str = "mp4"


def _audio_layout(channels: int) -> list[str]:
    if channels >= 6:
        return ["-channel_layout", "5.1(side)"]
    if channels >= 2:
        return ["-ac", "2"]
    return []


def _audio_encoder(bitrate: str = "256k") -> tuple[str, list[str]]:
    if aac_at_available():
        return "aac_at", ["-c:a", "aac_at", "-b:a", bitrate]
    return "aac", ["-c:a", "aac", "-b:a", bitrate]


def build_prores_plan(profile: StreamProfile, profile_name: str = "hq") -> EncoderPlan:
    """First-class ProRes mastering — .mov container, hardware when available."""
    key = profile_name.lower()
    if key not in PRORES_PROFILES:
        raise ValueError(f"Unknown ProRes profile '{profile_name}'. Choose: {', '.join(PRORES_PROFILES)}")

    spec = PRORES_PROFILES[key]
    audio_enc, audio_args = _audio_encoder("320k")
    layout = _audio_layout(profile.audio_channels)

    if prores_videotoolbox_available():
        video_enc = "prores_videotoolbox"
        video_args = ["-c:v", "prores_videotoolbox", "-profile:v", spec["videotoolbox"]]
    else:
        video_enc = "prores_ks"
        video_args = ["-c:v", "prores_ks", "-profile:v", spec["prores_ks"]]

    if profile.is_hdr and prores_videotoolbox_available():
        video_args += [
            "-pix_fmt",
            "p010le",
            "-color_primaries",
            "bt2020",
            "-color_trc",
            "smpte2084",
            "-colorspace",
            "bt2020nc",
        ]
    else:
        video_args += ["-pix_fmt", "yuv422p10le"]

    return EncoderPlan(
        args=[
            *video_args,
            *audio_args,
            *layout,
            "-movflags",
            "+faststart",
        ],
        video_encoder=video_enc,
        audio_encoder=audio_enc,
        container_extension="mov",
    )


def build_encoder_plan(
    output_format: str,
    profile: StreamProfile,
    *,
    prores_profile: str = "hq",
    prores_archive: bool = False,
    normalize_lufs: bool = False,
) -> EncoderPlan:
    fmt = output_format.lower()
    audio_enc, audio_args = _audio_encoder("448k" if profile.audio_channels >= 6 else "256k")
    layout = _audio_layout(profile.audio_channels)

    if fmt == "prores" or (prores_archive and fmt in {"mp4", "mov"}):
        return build_prores_plan(profile, prores_profile)

    if fmt == "mp4":
        video_enc = "libx264"
        video_args: list[str] = ["-c:v", "libx264", "-preset", "medium", "-crf", "18"]

        if videotoolbox_h264_available():
            video_enc = "h264_videotoolbox"
            video_args = ["-c:v", "h264_videotoolbox", "-b:v", "12000k"]

        if profile.is_hdr and videotoolbox_h264_available():
            video_args += [
                "-pix_fmt",
                "p010le",
                "-color_primaries",
                "bt2020",
                "-color_trc",
                "smpte2084",
                "-colorspace",
                "bt2020nc",
            ]
        else:
            video_args += ["-pix_fmt", "yuv420p"]

        args = [
            *video_args,
            *audio_args,
            *layout,
            "-movflags",
            "+faststart",
        ]

        return EncoderPlan(args=args, video_encoder=video_enc, audio_encoder=audio_enc)

    if fmt == "mkv":
        video_enc = "libx265"
        video_args = ["-c:v", "libx265", "-crf", "20"]
        if videotoolbox_hevc_available():
            video_enc = "hevc_videotoolbox"
            video_args = ["-c:v", "hevc_videotoolbox", "-q:v", "65"]
        if profile.is_hdr:
            video_args += ["-pix_fmt", "p010le"]
        else:
            video_args += ["-pix_fmt", "yuv420p"]

        return EncoderPlan(
            args=[*video_args, *audio_args, *layout],
            video_encoder=video_enc,
            audio_encoder=audio_enc,
            container_extension="mkv",
        )

    if fmt == "mp3":
        return EncoderPlan(
            args=[
                "-vn",
                "-c:a",
                "libmp3lame",
                "-q:a",
                "2",
                "-ar",
                "48000",
                "-ac",
                "2",
            ],
            video_encoder="none",
            audio_encoder="libmp3lame",
            container_extension="mp3",
        )

    if fmt == "wav":
        wav_layout = _audio_layout(profile.audio_channels) if profile.audio_channels >= 6 else ["-ac", "2"]
        return EncoderPlan(
            args=[
                "-vn",
                "-c:a",
                "pcm_s16le",
                "-ar",
                "48000",
                *wav_layout,
            ],
            video_encoder="none",
            audio_encoder="pcm_s16le",
            container_extension="wav",
        )

    if fmt == "m4a":
        return EncoderPlan(
            args=["-vn", *audio_args, *layout, "-ar", "48000"],
            video_encoder="none",
            audio_encoder=audio_enc,
            container_extension="m4a",
        )

    raise ValueError(
        f"Unsupported output format '{output_format}'. "
        "Supported: mp4, mkv, mp3, wav, m4a, prores."
    )
