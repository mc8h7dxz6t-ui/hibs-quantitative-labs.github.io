"""Watch-folder queue daemon."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from media_suite.config import DEFAULT_FORMAT, OUTPUT_DIR, WATCH_FILE
from media_suite.pipeline import run_transcode
from media_suite.probe import expand_playlist


@dataclass
class DaemonState:
    status: str = "Initializing forensic media engine…"
    current_job: str = "Idle"
    processed_count: int = 0
    live_fps: str = "0.0"
    live_speed: str = "0.0x"
    lock: threading.Lock = field(default_factory=threading.Lock)

    def update(
        self,
        *,
        status: str | None = None,
        current_job: str | None = None,
        processed_delta: int = 0,
        fps: str | None = None,
        speed: str | None = None,
    ) -> None:
        with self.lock:
            if status is not None:
                self.status = status
            if current_job is not None:
                self.current_job = current_job
            if processed_delta:
                self.processed_count += processed_delta
            if fps is not None:
                self.live_fps = fps
            if speed is not None:
                self.live_speed = speed


def ensure_watch_file() -> None:
    if not WATCH_FILE.exists():
        WATCH_FILE.write_text(
            "# Drop YouTube URLs or playlists here — one per line.\n"
            "# Optional format suffix: URL | mp3\n",
            encoding="utf-8",
        )


def _parse_queue_line(line: str) -> tuple[str, str]:
    if "|" in line:
        url, fmt = [part.strip() for part in line.split("|", 1)]
        return url, fmt or DEFAULT_FORMAT
    return line.strip(), DEFAULT_FORMAT


def watch_folder_daemon(
    state: DaemonState,
    *,
    output_format: str = DEFAULT_FORMAT,
    stop_event: threading.Event | None = None,
    poll_seconds: float = 2.0,
) -> None:
    ensure_watch_file()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stop = stop_event or threading.Event()

    while not stop.is_set():
        try:
            lines = WATCH_FILE.read_text(encoding="utf-8").splitlines()
            entries = [ln for ln in lines if ln.strip() and not ln.strip().startswith("#")]

            if not entries:
                state.update(status="Awaiting links in queue file…", current_job="Idle")
            else:
                raw_line = entries[0]
                target_url, fmt = _parse_queue_line(raw_line)
                fmt = fmt or output_format
                state.update(status="Resolving playlist entries…", current_job=target_url)

                urls = expand_playlist(target_url)
                total = len(urls)

                for idx, url in enumerate(urls, start=1):
                    state.update(
                        status=f"Processing track [{idx}/{total}]…",
                        current_job=url,
                    )

                    def on_status(msg: str, i=idx, t=total) -> None:
                        state.update(status=f"[{i}/{t}] {msg}")

                    result = run_transcode(url, fmt, on_status=on_status)
                    if result.telemetry:
                        state.update(
                            fps=result.telemetry.fps,
                            speed=result.telemetry.speed,
                        )
                    if result.success:
                        state.update(processed_delta=1, status="Asset verified and logged.")
                    else:
                        state.update(status=f"Error: {result.error}")

                remaining = [ln for ln in lines if ln.strip() != raw_line.strip()]
                WATCH_FILE.write_text(
                    "\n".join(remaining) + ("\n" if remaining else ""),
                    encoding="utf-8",
                )
        except OSError as exc:
            state.update(status=f"Daemon error: {exc}")

        stop.wait(poll_seconds)
