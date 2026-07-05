"""Watch-folder queue daemon."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from media_suite.config import DEFAULT_FORMAT, OUTPUT_DIR, WATCH_FILE
from media_suite.input import expand_inputs
from media_suite.pipeline import run_transcode, run_transcode_prores
from media_suite.queue import ensure_watch_file


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


def _parse_queue_line(line: str) -> tuple[str, str, str | None]:
    """Return (source, format, prores_profile)."""
    raw = line.strip()
    if "|" not in raw:
        return raw, DEFAULT_FORMAT, None

    source, suffix = [part.strip() for part in raw.split("|", 1)]
    if suffix.lower().startswith("prores:"):
        profile = suffix.split(":", 1)[1].strip() or "hq"
        return source, "prores", profile
    return source, suffix or DEFAULT_FORMAT, None


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
                state.update(status="Awaiting files or URLs in queue…", current_job="Idle")
            else:
                raw_line = entries[0]
                target, fmt, prores_profile = _parse_queue_line(raw_line)
                fmt = fmt or output_format
                state.update(status="Resolving batch inputs…", current_job=target)

                sources = expand_inputs(target)
                total = len(sources)

                for idx, source in enumerate(sources, start=1):
                    state.update(
                        status=f"Processing [{idx}/{total}]…",
                        current_job=source,
                    )

                    def on_status(msg: str, i=idx, t=total) -> None:
                        state.update(status=f"[{i}/{t}] {msg}")

                    if fmt == "prores":
                        result = run_transcode_prores(
                            source,
                            profile=prores_profile or "hq",
                            on_status=on_status,
                        )
                    else:
                        result = run_transcode(source, fmt, on_status=on_status)

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
