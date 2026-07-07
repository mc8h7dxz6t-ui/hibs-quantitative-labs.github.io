"""Conversion planner — we own remux vs transcode decisions."""

from __future__ import annotations

from pathlib import Path

from media_engine.types import (
    ColorScience,
    ConversionMode,
    ConversionPlan,
    ConversionRequest,
    MediaCatalog,
    StreamKind,
    StreamMapping,
)

# Container compatibility for bitstream copy
COPY_SAFE_CONTAINERS = {
    ("h264", "mp4"),
    ("h264", "mov"),
    ("h264", "mkv"),
    ("hevc", "mp4"),
    ("hevc", "mkv"),
    ("hevc", "mov"),
    ("aac", "mp4"),
    ("aac", "m4a"),
    ("aac", "mkv"),
    ("ac3", "mkv"),
    ("eac3", "mkv"),
    ("opus", "mkv"),
    ("opus", "webm"),
    ("vorbis", "ogg"),
    ("flac", "flac"),
    ("flac", "mkv"),
    ("mp3", "mp3"),
    ("pcm_s16le", "wav"),
    ("dvhe", "mkv"),
    ("dvhe", "mp4"),
    ("dvav", "mkv"),
}

AUDIO_OUTPUT = {"mp3", "wav", "m4a", "flac", "ogg", "aac"}
VIDEO_OUTPUT = {"mp4", "mkv", "mov", "webm"}


def _output_path(request: ConversionRequest, catalog: MediaCatalog) -> Path:
    out_dir = request.output_dir or catalog.source_path.parent / "converted"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = catalog.source_path.stem
    return out_dir / f"{stem}.{request.output_format}"


def _can_copy_stream(codec: str, output_fmt: str, kind: StreamKind) -> bool:
    if kind == StreamKind.SUBTITLE:
        return output_fmt in {"mkv", "mp4", "mov"}
    return (codec.lower(), output_fmt.lower()) in COPY_SAFE_CONTAINERS


def _target_audio_codec(output_fmt: str) -> str:
    return {
        "mp3": "libmp3lame",
        "wav": "pcm_s16le",
        "m4a": "aac",
        "flac": "flac",
        "ogg": "libvorbis",
        "aac": "aac",
    }.get(output_fmt, "aac")


def _target_video_codec(output_fmt: str) -> str:
    return {"webm": "libvpx-vp9", "mkv": "libx265"}.get(output_fmt, "libx264")


def build_plan(request: ConversionRequest, catalog: MediaCatalog) -> ConversionPlan:
    """
    Forensic planning step — document WHY each stream is copied or transcoded.
    This is the core owned logic; FFmpeg only executes our plan.
    """
    out_fmt = request.output_format.lower()
    output_path = _output_path(request, catalog)
    mappings: list[StreamMapping] = []
    notes: list[str] = []
    warnings: list[str] = []

    video = catalog.primary_video
    audio = catalog.primary_audio

    # --- Validation gates (fail loud) ---
    if request.require_surround_audio:
        if not audio or not audio.is_surround:
            raise ValueError(
                f"require_surround_audio: source has "
                f"{audio.channels if audio else 0}ch, not surround"
            )

    if request.require_hdr_metadata:
        if not video or video.color_science not in {ColorScience.HDR10, ColorScience.HLG}:
            raise ValueError("require_hdr_metadata: source has no HDR signaling")

    if request.require_bitstream_video:
        if not video:
            raise ValueError("require_bitstream_video: no video stream")
        if not _can_copy_stream(video.codec, out_fmt, StreamKind.VIDEO):
            raise ValueError(
                f"require_bitstream_video: cannot copy {video.codec} into .{out_fmt}"
            )

    # --- Audio-only output ---
    if out_fmt in AUDIO_OUTPUT:
        if not audio:
            raise ValueError(f"Cannot extract audio: no audio stream in {catalog.source_path}")
        mappings.append(
            StreamMapping(
                input_index=audio.index,
                kind=StreamKind.AUDIO,
                mode=ConversionMode.EXTRACT,
                output_codec=_target_audio_codec(out_fmt),
                reason=f"audio-only output .{out_fmt}",
            )
        )
        return ConversionPlan(
            catalog=catalog,
            output_path=output_path,
            output_format=out_fmt,
            mappings=mappings,
            global_mode=ConversionMode.EXTRACT,
            preservation_notes=notes,
            warnings=warnings,
        )

    # --- Video output ---
    if not video:
        raise ValueError(f"No video stream for .{out_fmt} output")

    vid_idx = request.video_stream_index if request.video_stream_index is not None else video.index
    aud_idx = request.audio_stream_index if request.audio_stream_index is not None else (
        audio.index if audio else None
    )

    # Dolby Vision: only bitstream copy preserves DV layer
    if video.color_science == ColorScience.DOLBY_VISION:
        if _can_copy_stream(video.codec, out_fmt, StreamKind.VIDEO):
            mappings.append(
                StreamMapping(
                    input_index=vid_idx,
                    kind=StreamKind.VIDEO,
                    mode=ConversionMode.BITSTREAM_COPY,
                    output_codec=None,
                    reason="Dolby Vision RPU must not be re-encoded",
                )
            )
            notes.append("DV: video bitstream copy — transcoding would destroy enhancement layer")
        else:
            raise ValueError(
                f"Dolby Vision cannot be preserved in .{out_fmt}; use mkv/mp4/mov with copy"
            )
    elif request.require_bitstream_video or (
        _can_copy_stream(video.codec, out_fmt, StreamKind.VIDEO) and out_fmt == catalog.format_name
    ):
        mappings.append(
            StreamMapping(
                input_index=vid_idx,
                kind=StreamKind.VIDEO,
                mode=ConversionMode.BITSTREAM_COPY,
                output_codec=None,
                reason=f"codec {video.codec} compatible with .{out_fmt}",
            )
        )
        notes.append("video: bitstream copy (no generation loss)")
    else:
        enc = _target_video_codec(out_fmt)
        mappings.append(
            StreamMapping(
                input_index=vid_idx,
                kind=StreamKind.VIDEO,
                mode=ConversionMode.TRANSCODE,
                output_codec=enc,
                reason=f"transcode {video.codec} → {enc} for .{out_fmt}",
            )
        )
        if video.color_science in {ColorScience.HDR10, ColorScience.HLG}:
            notes.append(f"HDR {video.color_science.value}: metadata will be forwarded in encode args")
        else:
            warnings.append("SDR transcode — no HDR metadata to preserve")

    if audio and aud_idx is not None:
        if _can_copy_stream(audio.codec, out_fmt, StreamKind.AUDIO):
            mappings.append(
                StreamMapping(
                    input_index=aud_idx,
                    kind=StreamKind.AUDIO,
                    mode=ConversionMode.BITSTREAM_COPY,
                    output_codec=None,
                    reason=f"audio {audio.codec} compatible with container",
                )
            )
        else:
            aenc = _target_audio_codec(out_fmt) if out_fmt in AUDIO_OUTPUT else "aac"
            mappings.append(
                StreamMapping(
                    input_index=aud_idx,
                    kind=StreamKind.AUDIO,
                    mode=ConversionMode.TRANSCODE,
                    output_codec=aenc,
                    reason=f"transcode audio {audio.codec} → {aenc}",
                )
            )

    if request.embed_subtitles:
        for sub in catalog.subtitle_streams:
            mappings.append(
                StreamMapping(
                    input_index=sub.index,
                    kind=StreamKind.SUBTITLE,
                    mode=ConversionMode.BITSTREAM_COPY if sub.is_text_subtitle else ConversionMode.TRANSCODE,
                    output_codec="copy" if sub.is_text_subtitle else "mov_text",
                    reason="subtitle passthrough" if sub.is_text_subtitle else "subtitle re-encode",
                )
            )

    global_mode = ConversionMode.BITSTREAM_COPY
    if any(m.mode == ConversionMode.TRANSCODE for m in mappings):
        global_mode = ConversionMode.TRANSCODE

    return ConversionPlan(
        catalog=catalog,
        output_path=output_path,
        output_format=out_fmt,
        mappings=mappings,
        global_mode=global_mode,
        preservation_notes=notes,
        warnings=warnings,
    )
