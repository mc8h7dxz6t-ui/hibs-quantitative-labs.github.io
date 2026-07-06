"""Advanced stream analysis and preservation policy enforcement."""

from __future__ import annotations

from dataclasses import dataclass, field

from media_suite.probe import StreamProfile

HDR_TRANSFERS = frozenset({"smpte2084", "arib-std-b67", "smpte428_1"})
DV_CODECS = frozenset({"dvhe", "dvav", "dovi"})


@dataclass
class VideoStreamInfo:
    index: int = 0
    codec: str = ""
    width: int = 0
    height: int = 0
    pix_fmt: str = ""
    color_primaries: str = ""
    color_transfer: str = ""
    color_space: str = ""
    is_hdr10: bool = False
    is_hlg: bool = False
    is_dolby_vision: bool = False
    has_video: bool = False


@dataclass
class AudioStreamInfo:
    index: int = 0
    codec: str = ""
    channels: int = 2
    channel_layout: str = ""
    sample_rate: int = 48000
    is_surround: bool = False


@dataclass
class StreamAnalysis:
    video: VideoStreamInfo = field(default_factory=VideoStreamInfo)
    audio: AudioStreamInfo = field(default_factory=AudioStreamInfo)
    subtitle_stream_count: int = 0


@dataclass
class PreservationPolicy:
    strict_hdr: bool = False
    strict_dolby_vision: bool = False
    strict_surround: bool = False

    def any_strict(self) -> bool:
        return self.strict_hdr or self.strict_dolby_vision or self.strict_surround


def analyze_profile(profile: StreamProfile) -> StreamAnalysis:
    analysis = StreamAnalysis()
    streams = profile.raw.get("streams") or []

    if profile.is_local:
        for stream in streams:
            codec_type = stream.get("codec_type")
            if codec_type == "video" and not analysis.video.has_video:
                codec = (stream.get("codec_name") or "").lower()
                transfer = (stream.get("color_transfer") or "").lower()
                primaries = (stream.get("color_primaries") or "").lower()
                analysis.video = VideoStreamInfo(
                    index=int(stream.get("index", 0)),
                    codec=codec,
                    width=int(stream.get("width") or 0),
                    height=int(stream.get("height") or 0),
                    pix_fmt=(stream.get("pix_fmt") or ""),
                    color_primaries=primaries,
                    color_transfer=transfer,
                    color_space=(stream.get("color_space") or "").lower(),
                    is_hdr10=transfer == "smpte2084" or primaries in {"bt2020", "bt2020nc"},
                    is_hlg=transfer == "arib-std-b67",
                    is_dolby_vision=codec in DV_CODECS or "dv" in (stream.get("profile") or "").lower(),
                    has_video=True,
                )
            elif codec_type == "audio" and analysis.audio.index == 0 and stream.get("index") is not None:
                channels = int(stream.get("channels") or 2)
                layout = stream.get("channel_layout") or ""
                analysis.audio = AudioStreamInfo(
                    index=int(stream.get("index", 0)),
                    codec=(stream.get("codec_name") or ""),
                    channels=channels,
                    channel_layout=layout,
                    sample_rate=int(stream.get("sample_rate") or 48000),
                    is_surround=channels >= 6 or "5.1" in layout or "7.1" in layout,
                )
            elif codec_type == "subtitle":
                analysis.subtitle_stream_count += 1
    else:
        # Remote yt-dlp format scan
        channels = profile.audio_channels
        analysis.audio = AudioStreamInfo(
            channels=channels,
            is_surround=channels >= 6,
            channel_layout="5.1(side)" if channels >= 6 else "stereo",
        )
        analysis.video.has_video = profile.has_video
        analysis.video.is_hdr10 = profile.is_hdr
        analysis.video.height = 2160 if profile.is_hdr else 1080
        for fmt in profile.raw.get("formats") or []:
            vcodec = (fmt.get("vcodec") or "").lower()
            if any(dv in vcodec for dv in DV_CODECS):
                analysis.video.is_dolby_vision = True
        if profile.has_subtitles:
            analysis.subtitle_stream_count = len(profile.subtitle_langs)

    if profile.has_video and not analysis.video.has_video:
        analysis.video.has_video = True

    return analysis


def validate_preservation(
    analysis: StreamAnalysis,
    policy: PreservationPolicy,
    output_format: str,
) -> list[str]:
    """Return list of validation errors. Empty = OK."""
    errors: list[str] = []

    if policy.strict_hdr:
        if not (analysis.video.is_hdr10 or analysis.video.is_hlg):
            errors.append("STRICT_HDR: source has no HDR10/HLG signaling")
        elif output_format in {"mp3", "wav", "flac", "ogg", "m4a"}:
            errors.append("STRICT_HDR: cannot preserve HDR in audio-only output format")

    if policy.strict_dolby_vision:
        if not analysis.video.is_dolby_vision:
            errors.append("STRICT_DOLBY_VISION: source has no Dolby Vision track")
        elif output_format not in {"mkv", "mp4", "mov"}:
            errors.append(
                "STRICT_DOLBY_VISION: output format must be mkv/mp4/mov for DV bitstream copy"
            )

    if policy.strict_surround:
        if not analysis.audio.is_surround:
            errors.append(
                f"STRICT_SURROUND: source is {analysis.audio.channels}ch "
                f"({analysis.audio.channel_layout or 'unknown'}), not surround"
            )

    return errors


def preservation_video_args(
    analysis: StreamAnalysis,
    policy: PreservationPolicy,
    output_format: str,
    *,
    hardware_h264: bool,
) -> tuple[list[str], str, bool]:
    """
    Returns (extra_ffmpeg_video_args, video_encoder_name, use_bitstream_copy).
    use_bitstream_copy=True → -c:v copy (required for guaranteed DV preservation).
    """
    fmt = output_format.lower()

    if policy.strict_dolby_vision and analysis.video.is_dolby_vision:
        if fmt in {"mkv", "mp4", "mov"}:
            return ["-c:v", "copy"], "copy", True
        raise ValueError("Dolby Vision preservation requires mkv, mp4, or mov output")

    if not (analysis.video.is_hdr10 or analysis.video.is_hlg):
        return [], "transcode", False

    args: list[str] = []
    encoder = "libx264"

    if hardware_h264:
        encoder = "h264_videotoolbox"
        args += ["-c:v", "h264_videotoolbox", "-b:v", "12000k"]
    else:
        args += ["-c:v", "libx264", "-preset", "slow", "-crf", "16"]

    if analysis.video.is_hdr10:
        args += [
            "-pix_fmt",
            "p010le",
            "-color_primaries",
            analysis.video.color_primaries or "bt2020",
            "-color_trc",
            analysis.video.color_transfer or "smpte2084",
            "-colorspace",
            analysis.video.color_space or "bt2020nc",
        ]
    elif analysis.video.is_hlg:
        args += [
            "-pix_fmt",
            "p010le",
            "-color_primaries",
            "bt2020",
            "-color_trc",
            "arib-std-b67",
            "-colorspace",
            "bt2020nc",
        ]

    return args, encoder, False


def preservation_audio_args(analysis: StreamAnalysis, policy: PreservationPolicy) -> list[str]:
    if policy.strict_surround and analysis.audio.is_surround:
        layout = analysis.audio.channel_layout or "5.1(side)"
        return ["-map", f"0:{analysis.audio.index}", "-channel_layout", layout]
    if analysis.audio.is_surround:
        return ["-channel_layout", analysis.audio.channel_layout or "5.1(side)"]
    return []
