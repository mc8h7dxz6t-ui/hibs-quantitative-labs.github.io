"""Input resolution: local files, folders, and remote URLs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

# Extensions FFmpeg reliably decodes (industry-common containers/codecs).
MEDIA_EXTENSIONS = frozenset(
    {
        "aac",
        "ac3",
        "aiff",
        "alac",
        "amr",
        "avi",
        "dff",
        "dsf",
        "flac",
        "flv",
        "m2ts",
        "m4a",
        "m4v",
        "mkv",
        "mov",
        "mp3",
        "mp4",
        "mpeg",
        "mpg",
        "mts",
        "oga",
        "ogg",
        "ogv",
        "opus",
        "ts",
        "wav",
        "webm",
        "wma",
        "wmv",
    }
)


class InputKind(str, Enum):
    LOCAL_FILE = "local_file"
    LOCAL_DIR = "local_dir"
    REMOTE_URL = "remote_url"


@dataclass
class MediaInput:
    source: str
    kind: InputKind
    path: Path | None = None


def is_remote_url(value: str) -> bool:
    return bool(re.match(r"^https?://", value.strip(), re.IGNORECASE))


def is_media_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lstrip(".").lower() in MEDIA_EXTENSIONS


def resolve_input(source: str) -> MediaInput:
    raw = source.strip().strip("'\"")
    if not raw:
        raise ValueError("Input cannot be empty")

    if is_remote_url(raw):
        return MediaInput(source=raw, kind=InputKind.REMOTE_URL)

    path = Path(raw).expanduser()
    if path.is_dir():
        return MediaInput(source=raw, kind=InputKind.LOCAL_DIR, path=path.resolve())
    if path.is_file():
        if not is_media_file(path):
            raise ValueError(f"Unsupported or unknown media file: {path}")
        return MediaInput(source=raw, kind=InputKind.LOCAL_FILE, path=path.resolve())

    raise ValueError(f"Input not found: {raw}")


def expand_inputs(source: str) -> list[str]:
    """Expand playlists/URLs, directories, or return a single local file path."""
    if is_remote_url(source.strip()):
        from media_suite.probe import expand_playlist

        return expand_playlist(source.strip())

    try:
        resolved = resolve_input(source)
    except ValueError:
        return [source]

    if resolved.kind == InputKind.LOCAL_DIR and resolved.path:
        files = sorted(
            p for p in resolved.path.iterdir() if is_media_file(p)
        )
        return [str(p) for p in files]

    return [resolved.source]
