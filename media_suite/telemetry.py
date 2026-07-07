"""Live FFmpeg stderr telemetry parsing."""

from __future__ import annotations

import re
import subprocess
import threading
from dataclasses import dataclass


@dataclass
class TelemetryState:
    fps: str = "0.0"
    speed: str = "0.0x"
    time: str = "00:00:00.00"


_FPS = re.compile(r"fps=\s*([\d.]+)")
_SPEED = re.compile(r"speed=\s*([\d.]+x)")
_TIME = re.compile(r"time=\s*([\d:.]+)")


def track_ffmpeg_telemetry(process: subprocess.Popen[bytes], state: TelemetryState) -> None:
    if process.stderr is None:
        return

    while True:
        line = process.stderr.readline()
        if not line:
            break
        text = line.decode("utf-8", errors="ignore")
        if m := _FPS.search(text):
            state.fps = m.group(1)
        if m := _SPEED.search(text):
            state.speed = m.group(1)
        if m := _TIME.search(text):
            state.time = m.group(1)


def start_telemetry_thread(
    process: subprocess.Popen[bytes], state: TelemetryState
) -> threading.Thread:
    thread = threading.Thread(target=track_ffmpeg_telemetry, args=(process, state), daemon=True)
    thread.start()
    return thread
