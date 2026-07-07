"""Probe analyzer — we own parsing ffprobe JSON into MediaCatalog."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from media_engine.types import ColorScience, MediaCatalog, StreamDescriptor, StreamKind

HDR_TRANSFERS = {"smpte2084": ColorScience.HDR10, "arib-std-b67": ColorScience.HLG}
DV_CODECS = {"dvhe", "dvav", "dovi"}


def _classify_color(codec: str, transfer: str, primaries: str) -> ColorScience:
    codec = codec.lower()
    transfer = transfer.lower()
    if codec in DV_CODECS or "dv" in codec:
        return ColorScience.DOLBY_VISION
    if transfer in HDR_TRANSFERS:
        return HDR_TRANSFERS[transfer]
    if primaries in {"bt2020", "bt2020nc"} and transfer == "smpte2084":
        return ColorScience.HDR10
    return ColorScience.SDR


def probe_file(path: Path) -> MediaCatalog:
    """Run ffprobe and build our catalog — this is our data model, not ffprobe's."""
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=120)
    data = json.loads(proc.stdout)
    return catalog_from_ffprobe(path, data)


def catalog_from_ffprobe(path: Path, data: dict) -> MediaCatalog:
    fmt = data.get("format") or {}
    streams: list[StreamDescriptor] = []

    for raw in data.get("streams") or []:
        codec_type = raw.get("codec_type", "")
        kind_map = {
            "video": StreamKind.VIDEO,
            "audio": StreamKind.AUDIO,
            "subtitle": StreamKind.SUBTITLE,
            "data": StreamKind.DATA,
        }
        kind = kind_map.get(codec_type, StreamKind.DATA)
        tags = raw.get("tags") or {}
        codec = raw.get("codec_name") or "unknown"
        transfer = raw.get("color_transfer") or ""
        primaries = raw.get("color_primaries") or ""

        streams.append(
            StreamDescriptor(
                index=int(raw.get("index", 0)),
                kind=kind,
                codec=codec,
                codec_long=raw.get("codec_long_name") or "",
                bitrate=int(raw.get("bit_rate")) if raw.get("bit_rate") else None,
                language=tags.get("language"),
                width=int(raw.get("width") or 0),
                height=int(raw.get("height") or 0),
                pix_fmt=raw.get("pix_fmt") or "",
                color_science=_classify_color(codec, transfer, primaries),
                color_primaries=primaries,
                color_transfer=transfer,
                color_space=raw.get("color_space") or "",
                frame_rate=raw.get("r_frame_rate") or raw.get("avg_frame_rate") or "",
                channels=int(raw.get("channels") or 0),
                channel_layout=raw.get("channel_layout") or "",
                sample_rate=int(raw.get("sample_rate") or 0),
                is_text_subtitle=codec in {"subrip", "ass", "mov_text", "webvtt"},
            )
        )

    size = int(fmt.get("size") or path.stat().st_size if path.exists() else 0)
    duration = float(fmt["duration"]) if fmt.get("duration") else None

    return MediaCatalog(
        source_path=path.resolve(),
        format_name=fmt.get("format_name") or path.suffix.lstrip("."),
        format_long=fmt.get("format_long_name") or "",
        duration_sec=duration,
        size_bytes=size,
        bit_rate=int(fmt["bit_rate"]) if fmt.get("bit_rate") else None,
        streams=streams,
        raw_probe=data,
    )
