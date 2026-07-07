"""FFmpeg command builder — translates OUR plan into argv, nothing more."""

from __future__ import annotations

from media_engine.types import ConversionMode, ConversionPlan, StreamKind


def build_ffmpeg_command(plan: ConversionPlan) -> list[str]:
    """
    Map ConversionPlan → ffmpeg argv.
    FFmpeg is the codec backend; this function is our instruction generator.
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-stats",
        "-fflags",
        "+genpts+igndts",
        "-i",
        str(plan.catalog.source_path),
    ]

    video_map = next((m for m in plan.mappings if m.kind == StreamKind.VIDEO), None)
    audio_map = next((m for m in plan.mappings if m.kind == StreamKind.AUDIO), None)
    sub_maps = [m for m in plan.mappings if m.kind == StreamKind.SUBTITLE]

    if plan.global_mode == ConversionMode.EXTRACT:
        audio = audio_map
        if not audio:
            raise ValueError("extract plan missing audio mapping")
        cmd += ["-map", f"0:{audio.input_index}", "-vn"]
        cmd += _encode_args(audio.output_codec or "aac", plan.output_format)
        cmd.append(str(plan.output_path))
        return cmd

    # Video outputs
    if video_map:
        cmd += ["-map", f"0:{video_map.input_index}"]
        if video_map.mode == ConversionMode.BITSTREAM_COPY:
            cmd += ["-c:v", "copy"]
        else:
            cmd += _video_encode_args(video_map.output_codec or "libx264", plan)

    if audio_map:
        cmd += ["-map", f"0:{audio_map.input_index}"]
        if audio_map.mode == ConversionMode.BITSTREAM_COPY:
            cmd += ["-c:a", "copy"]
        else:
            cmd += _audio_encode_args(audio_map.output_codec or "aac", plan)

    for sm in sub_maps:
        cmd += ["-map", f"0:{sm.input_index}"]
        if sm.mode == ConversionMode.BITSTREAM_COPY:
            cmd += ["-c:s", "copy"]
        else:
            sub_codec = "mov_text" if plan.output_format in {"mp4", "mov"} else "srt"
            cmd += ["-c:s", sub_codec]

    if plan.output_format == "mp4":
        cmd += ["-movflags", "+faststart"]

    cmd.append(str(plan.output_path))
    plan.ffmpeg_args = cmd
    return cmd


def _video_encode_args(codec: str, plan: ConversionPlan) -> list[str]:
    video = plan.catalog.primary_video
    args = ["-c:v", codec]

    if codec == "libx264":
        args += ["-preset", "slow", "-crf", "18"]
    elif codec == "libx265":
        args += ["-crf", "20"]
    elif codec == "libvpx-vp9":
        args += ["-crf", "32", "-b:v", "0"]

    if video and video.color_science.value in {"hdr10", "hlg"}:
        args += [
            "-pix_fmt",
            "p010le",
            "-color_primaries",
            video.color_primaries or "bt2020",
            "-color_trc",
            video.color_transfer or "smpte2084",
            "-colorspace",
            video.color_space or "bt2020nc",
        ]
    else:
        args += ["-pix_fmt", "yuv420p"]

    return args


def _audio_encode_args(codec: str, plan: ConversionPlan) -> list[str]:
    audio = plan.catalog.primary_audio
    args = ["-c:a", codec]

    if codec == "libmp3lame":
        args += ["-q:a", "2"]
    elif codec == "aac":
        args += ["-b:a", "256k"]
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
    return ["-c:a", "aac", "-b:a", "256k"]
