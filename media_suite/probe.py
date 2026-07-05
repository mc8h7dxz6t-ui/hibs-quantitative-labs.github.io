"""yt-dlp metadata probing and playlist expansion."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from media_suite.config import SUBTITLE_LANGS


@dataclass
class StreamProfile:
    url: str
    title: str = "asset"
    has_subtitles: bool = False
    subtitle_langs: list[str] = field(default_factory=list)
    audio_channels: int = 2
    is_hdr: bool = False
    duration: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def safe_filename(title: str, max_len: int = 120) -> str:
    cleaned = "".join(c for c in title if c.isalnum() or c in {" ", "-", "_"}).strip()
    cleaned = re.sub(r"\s+", "_", cleaned)
    return (cleaned or "asset")[:max_len]


def probe_stream(url: str) -> StreamProfile:
    cmd = ["yt-dlp", "--dump-single-json", "--no-download", url]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=120)
        data = json.loads(proc.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        return StreamProfile(url=url, title="asset", raw={"probe_error": str(exc)})

    title = data.get("title") or "asset"
    subs = data.get("subtitles") or {}
    auto = data.get("automatic_captions") or {}
    sub_langs = sorted(set(list(subs.keys()) + list(auto.keys())))

    channels = 2
    is_hdr = False
    for fmt in data.get("formats") or []:
        if fmt.get("vcodec") not in (None, "none"):
            height = fmt.get("height") or 0
            if height >= 2160:
                is_hdr = True
        if fmt.get("acodec") not in (None, "none"):
            channels = max(channels, int(fmt.get("audio_channels") or 2))

    return StreamProfile(
        url=url,
        title=title,
        has_subtitles=bool(sub_langs),
        subtitle_langs=sub_langs,
        audio_channels=channels,
        is_hdr=is_hdr,
        duration=data.get("duration"),
        raw=data,
    )


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
    """Fetch subtitle sidecars without downloading media."""
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
