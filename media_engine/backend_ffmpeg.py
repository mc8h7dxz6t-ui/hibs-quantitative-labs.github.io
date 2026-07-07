"""FFmpeg argv builder — industry standards + hardware encoders."""

from __future__ import annotations

from media_engine.hardware import pick_aac_encoder, pick_h264_encoder, pick_hevc_encoder, pick_prores_encoder
from media_engine.standards import (
    COPYTIMESTAMPS,
    EBU_R128_FILTER,
    ISOBMFF_FASTSTART,
    MAP_CHAPTERS,
    MAP_METADATA,
    PRORES_PROFILES,
)
from media_engine.types import ConversionMode, ConversionPlan, StreamKind


def build_ffmpeg_command(plan: ConversionPlan, *, normalize_lufs: bool = False) -> list[str]:
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-stats",
        "-fflags", "+genpts+igndts",
        "-i", str(plan.catalog.source_path),
    ]

    video_map = next((m for m in plan.mappings if m.kind == StreamKind.VIDEO), None)
    audio_map = next((m for m in plan.mappings if m.kind == StreamKind.AUDIO), None)
    sub_maps = [m for m in plan.mappings if m.kind == StreamKind.SUBTITLE]
    any_copy = any(m.mode == ConversionMode.BITSTREAM_COPY for m in plan.mappings)

    if plan.global_mode == ConversionMode.EXTRACT:
        audio = audio_map
        if not audio:
            raise ValueError("extract plan missing audio mapping")
        cmd += ["-map", f"0:{audio.input_index}", "-vn"]
        cmd += _encode_args(audio.output_codec or "aac", plan.output_format)
        cmd += _global_output_flags(plan, normalize_lufs=False)
        cmd.append(str(plan.output_path))
        plan.ffmpeg_args = cmd
        return cmd

    if video_map:
        cmd += ["-map", f"0:{video_map.input_index}"]
        if video_map.mode == ConversionMode.BITSTREAM_COPY:
            cmd += ["-c:v", "copy"]
        elif plan.output_format == "prores":
            cmd += _prores_args(plan)
        else:
            cmd += _video_encode_args(video_map.output_codec or pick_h264_encoder(), plan)

    if audio_map:
        cmd += ["-map", f"0:{audio_map.input_index}"]
        if audio_map.mode == ConversionMode.BITSTREAM_COPY:
            cmd += ["-c:a", "copy"]
        else:
            cmd += _audio_encode_args(audio_map.output_codec or pick_aac_encoder(), plan)

    for sm in sub_maps:
        cmd += ["-map", f"0:{sm.input_index}"]
        if sm.mode == ConversionMode.BITSTREAM_COPY:
            cmd += ["-c:s", "copy"]
        else:
            sub_codec = "mov_text" if plan.output_format in {"mp4", "mov", "prores"} else "srt"
            cmd += ["-c:s", sub_codec]

    cmd += _global_output_flags(plan, normalize_lufs=normalize_lufs, bitstream_copy=any_copy)
    cmd.append(str(plan.output_path))
    plan.ffmpeg_args = cmd
    return cmd


def _global_output_flags(plan: ConversionPlan, *, normalize_lufs: bool, bitstream_copy: bool = False) -> list[str]:
    args: list[str] = []
    # ITU/ISO: preserve metadata + chapters where possible
    args += ["-map_metadata", MAP_METADATA, "-map_chapters", MAP_CHAPTERS]
    if bitstream_copy and COPYTIMESTAMPS:
        args += ["-copyts"]
    if normalize_lufs and plan.output_format not in {"mp3", "wav", "flac", "ogg"}:
        args += ["-af", EBU_R128_FILTER]
    if plan.output_format in {"mp4", "mov"}:
        args += ["-movflags", ISOBMFF_FASTSTART]
    if plan.output_format == "mp3":
        args += ["-write_id3v2", "1", "-id3v2_version", "3"]
    return args


def _prores_args(plan: ConversionPlan) -> list[str]:
    enc = pick_prores_encoder()
    return ["-c:v", enc, "-profile:v", PRORES_PROFILES.get(plan.prores_profile, "3")]


def _video_encode_args(codec: str, plan: ConversionPlan) -> list[str]:
    video = plan.catalog.primary_video
    # Upgrade software picks to hardware when planner left generic names
    if codec == "libx264":
        codec = pick_h264_encoder()
    elif codec == "libx265":
        codec = pick_hevc_encoder()

    args = ["-c:v", codec]
    if codec == "h264_videotoolbox":
        args += ["-b:v", "12000k"]
    elif codec == "hevc_videotoolbox":
        args += ["-q:v", "65"]
    elif codec == "libx264":
        args += ["-preset", "slow", "-crf", "18"]
    elif codec == "libx265":
        args += ["-crf", "20"]
    elif codec == "libvpx-vp9":
        args += ["-crf", "32", "-b:v", "0"]

    if video and video.color_science.value in {"hdr10", "hlg"}:
        args += [
            "-pix_fmt", "p010le",
            "-color_primaries", video.color_primaries or "bt2020",
            "-color_trc", video.color_transfer or "smpte2084",
            "-colorspace", video.color_space or "bt2020nc",
        ]
    else:
        args += ["-pix_fmt", "yuv420p"]
    return args


def _audio_encode_args(codec: str, plan: ConversionPlan) -> list[str]:
    audio = plan.catalog.primary_audio
    if codec == "aac":
        codec = pick_aac_encoder()
    args = ["-c:a", codec]
    if codec == "libmp3lame":
        args += ["-q:a", "2"]
    elif codec in {"aac", "aac_at"}:
        args += ["-b:a", "448k" if audio and audio.is_surround else "256k"]
    elif codec == "pcm_s16le":
        args += ["-ar", "48000"]
    if audio and audio.is_surround:
        args += ["-channel_layout", audio.channel_layout or "5.1(side)"]
    return args


def _encode_args(codec: str, output_fmt: str) -> list[str]:
    if codec == "libmp3lame":
        return ["-c:a", "libmp3lame", "-q:a", "2", "-ar", "48000"]
    if codec == "pcm_s16le":
        return ["-c:a", "pcm_s16le", "-ar", "48000"]
    if codec == "flac":
        return ["-c:a", "flac"]
    if codec == "libvorbis":
        return ["-c:a", "libvorbis", "-q:a", "6"]
    return ["-c:a", pick_aac_encoder(), "-b:a", "256k"]
