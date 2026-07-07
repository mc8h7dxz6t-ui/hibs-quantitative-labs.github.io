"""Domain types for the conversion construct — we own this schema."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class StreamKind(str, Enum):
    VIDEO = "video"
    AUDIO = "audio"
    SUBTITLE = "subtitle"
    DATA = "data"
    ATTACHMENT = "attachment"


class ColorScience(str, Enum):
    SDR = "sdr"
    HDR10 = "hdr10"
    HLG = "hlg"
    DOLBY_VISION = "dolby_vision"
    UNKNOWN = "unknown"


class ConversionMode(str, Enum):
    """How we move bytes from input to output."""

    BITSTREAM_COPY = "bitstream_copy"  # packet remux, no decode
    TRANSCODE = "transcode"  # decode → encode
    EXTRACT = "extract"  # strip video, audio only
    REWRAP = "rewrap"  # same codec, new container


class CustodyStage(str, Enum):
    SOURCE_FILE = "source_file"
    PROBE_SNAPSHOT = "probe_snapshot"
    PLAN_ISSUED = "plan_issued"
    EXECUTION_STDERR = "execution_stderr"
    OUTPUT_FILE = "output_file"


@dataclass(frozen=True)
class StreamDescriptor:
    """One elementary stream inside a container."""

    index: int
    kind: StreamKind
    codec: str
    codec_long: str = ""
    bitrate: int | None = None
    language: str | None = None
    # Video
    width: int = 0
    height: int = 0
    pix_fmt: str = ""
    color_science: ColorScience = ColorScience.UNKNOWN
    color_primaries: str = ""
    color_transfer: str = ""
    color_space: str = ""
    frame_rate: str = ""
    # Audio
    channels: int = 0
    channel_layout: str = ""
    sample_rate: int = 0
    # Subtitle
    is_text_subtitle: bool = False

    @property
    def is_surround(self) -> bool:
        return self.channels >= 6 or "5.1" in self.channel_layout or "7.1" in self.channel_layout


@dataclass
class MediaCatalog:
    """Complete forensic snapshot of what's inside a file."""

    source_path: Path
    format_name: str
    format_long: str
    duration_sec: float | None
    size_bytes: int
    bit_rate: int | None
    streams: list[StreamDescriptor]
    raw_probe: dict[str, Any]

    @property
    def video_streams(self) -> list[StreamDescriptor]:
        return [s for s in self.streams if s.kind == StreamKind.VIDEO]

    @property
    def audio_streams(self) -> list[StreamDescriptor]:
        return [s for s in self.streams if s.kind == StreamKind.AUDIO]

    @property
    def subtitle_streams(self) -> list[StreamDescriptor]:
        return [s for s in self.streams if s.kind == StreamKind.SUBTITLE]

    @property
    def primary_video(self) -> StreamDescriptor | None:
        return self.video_streams[0] if self.video_streams else None

    @property
    def primary_audio(self) -> StreamDescriptor | None:
        return self.audio_streams[0] if self.audio_streams else None


@dataclass
class StreamMapping:
    """Maps input stream index → output handling."""

    input_index: int
    kind: StreamKind
    mode: ConversionMode
    output_codec: str | None = None  # None = copy
    reason: str = ""


@dataclass
class ConversionPlan:
    """Our owned decision artifact — not FFmpeg's."""

    catalog: MediaCatalog
    output_path: Path
    output_format: str
    mappings: list[StreamMapping]
    global_mode: ConversionMode
    prores_profile: str = "hq"
    ffmpeg_args: list[str] = field(default_factory=list)
    preservation_notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        modes = ", ".join(f"{m.input_index}:{m.mode.value}" for m in self.mappings)
        return f"{self.global_mode.value} → {self.output_path.name} [{modes}]"


@dataclass
class ConversionRequest:
    """User intent."""

    input_path: Path
    output_format: str
    output_dir: Path | None = None
    output_path: Path | None = None
    require_bitstream_video: bool = False
    require_hdr_metadata: bool = False
    require_surround_audio: bool = False
    require_dolby_vision_copy: bool = False
    embed_subtitles: bool = True
    normalize_lufs: bool = False
    prores_profile: str = "hq"
    audio_stream_index: int | None = None
    video_stream_index: int | None = None
    case_id: str | None = None
    job_id: str | None = None


@dataclass
class CustodyEvent:
    stage: CustodyStage
    sha256: str | None
    md5: str | None = None
    path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp_utc: str = ""


@dataclass
class EngineResult:
    success: bool
    plan: ConversionPlan | None = None
    output_path: Path | None = None
    source_sha256: str | None = None
    output_sha256: str | None = None
    output_md5: str | None = None
    custody_events: list[CustodyEvent] = field(default_factory=list)
    custody_bundle: Path | None = None
    ffmpeg_command: list[str] = field(default_factory=list)
    error: str | None = None
