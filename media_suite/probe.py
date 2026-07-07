"""Metadata probing for remote streams and local files."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from media_suite.config import SUBTITLE_LANGS
from media_suite.input import is_remote_url


@dataclass
class StreamProfile:
    source: str
    title: str = "asset"
    has_subtitles: bool = False
    subtitle_langs: list[str] = field(default_factory=list)
    audio_channels: int = 2
    is_hdr: bool = False
    has_video: bool = True
    duration: float | None = None
    is_local: bool = False
    local_path: Path | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def url(self) -> str:
        """Backward-compatible alias."""
        return self.source


def safe_filename(title: str, max_len: int = 120) -> str:
    cleaned = "".join(c for c in title if c.isalnum() or c in {" ", "-", "_", "."}).strip()
    cleaned = re.sub(r"\s+", "_", cleaned)
    return (cleaned or "asset")[:max_len]


def probe_source(source: str) -> StreamProfile:
    if is_remote_url(source):
        return probe_remote(source)
    path = Path(source).expanduser().resolve()
    return probe_local_file(path)


def probe_remote(url: str) -> StreamProfile:
    cmd = ["yt-dlp", "--dump-single-json", "--no-download", url]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=120)
        data = json.loads(proc.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        return StreamProfile(source=url, title="asset", raw={"probe_error": str(exc)})

    title = data.get("title") or "asset"
    subs = data.get("subtitles") or {}
    auto = data.get("automatic_captions") or {}
    sub_langs = sorted(set(list(subs.keys()) + list(auto.keys())))

    channels = 2
    is_hdr = False
    has_video = False
    for fmt in data.get("formats") or []:
        if fmt.get("vcodec") not in (None, "none"):
            has_video = True
            height = fmt.get("height") or 0
            if height >= 2160:
                is_hdr = True
        if fmt.get("acodec") not in (None, "none"):
            channels = max(channels, int(fmt.get("audio_channels") or 2))

    return StreamProfile(
        source=url,
        title=title,
        has_subtitles=bool(sub_langs),
        subtitle_langs=sub_langs,
        audio_channels=channels,
        is_hdr=is_hdr,
        has_video=has_video,
        duration=data.get("duration"),
        is_local=False,
        raw=data,
    )


def probe_local_file(path: Path) -> StreamProfile:
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
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=60)
        data = json.loads(proc.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError, subprocess.TimeoutExpired, OSError) as exc:
        return StreamProfile(
            source=str(path),
            title=safe_filename(path.stem),
            is_local=True,
            local_path=path,
            raw={"probe_error": str(exc)},
        )

    streams = data.get("streams") or []
    fmt = data.get("format") or {}

    channels = 2
    is_hdr = False
    has_video = False
    has_subs = False
    sub_langs: list[str] = []

    hdr_transfers = {"smpte2084", "arib-std-b67", "smpte428_1"}

    for stream in streams:
        codec_type = stream.get("codec_type")
        if codec_type == "video":
            has_video = True
            transfer = (stream.get("color_transfer") or "").lower()
            if transfer in hdr_transfers:
                is_hdr = True
            height = stream.get("height") or 0
            if height >= 2160:
                is_hdr = True
        elif codec_type == "audio":
            channels = max(channels, int(stream.get("channels") or 2))
        elif codec_type == "subtitle":
            has_subs = True
            tag = stream.get("tags") or {}
            lang = tag.get("language") or "und"
            sub_langs.append(lang)

    duration_raw = fmt.get("duration")
    duration = float(duration_raw) if duration_raw else None

    return StreamProfile(
        source=str(path),
        title=safe_filename(path.stem),
        has_subtitles=has_subs,
        subtitle_langs=sorted(set(sub_langs)),
        audio_channels=channels,
        is_hdr=is_hdr,
        has_video=has_video,
        duration=duration,
        is_local=True,
        local_path=path,
        raw=data,
    )


# Backward-compatible alias
def probe_stream(url: str) -> StreamProfile:
    return probe_remote(url)


def expand_playlist(url: str) -> list[str]:
    """Return individual watch URLs when *url* is a playlist."""
    cmd = ["yt-dlp", "--flat-playlist", "--dump-single-json", url]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=120)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return [url]

    urls: list[str] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        entry_id = entry.get("id")
        entry_url = entry.get("url") or entry.get("webpage_url")
        if entry_url and entry_url.startswith("http"):
            urls.append(entry_url)
        elif entry_id:
            urls.append(f"https://www.youtube.com/watch?v={entry_id}")

    return urls or [url]


def download_subtitles(url: str, dest_dir: Path) -> list[Path]:
    """Fetch subtitle sidecars for remote URLs without downloading media."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    template = str(dest_dir / "%(title)s.%(ext)s")
    cmd = [
        "yt-dlp",
        "--skip-download",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs",
        SUBTITLE_LANGS,
        "--convert-subs",
        "vtt",
        "-o",
        template,
        url,
    ]
    try:
        subprocess.run(cmd, capture_output=True, check=True, timeout=300)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []

    return sorted(dest_dir.glob("*.vtt"))
