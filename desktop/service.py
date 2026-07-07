"""Desktop job orchestration — in-process ConversionEngine with live progress."""

from __future__ import annotations

import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from media_engine.planner import build_plan
from media_engine.probe import probe_file
from media_suite import __version__ as suite_version
from media_suite.encoders import OUTPUT_FORMATS, PRORES_PROFILES
from media_suite.engine_bridge import (
    coerce_output_format,
    is_music_classification,
    materialize_source,
    options_to_request,
    resolve_output_path,
)
from media_suite.input import is_remote_url
from media_suite.jobs import JobOptions
from media_suite.pipeline import run_transcode, run_transcode_prores
from media_suite.platform import (
    aac_at_available,
    prores_videotoolbox_available,
    videotoolbox_h264_available,
    videotoolbox_hevc_available,
)
from media_suite.probe import probe_source


@dataclass
class DesktopJob:
    id: str
    source: str
    output_format: str
    status: str = "queued"
    message: str = "Queued"
    progress: float = 0.0
    fps: str = "0.0"
    speed: str = "0.0x"
    elapsed: str = "00:00:00.00"
    error: str | None = None
    result: dict[str, Any] | None = None
    options: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None


class JobManager:
    """Thread-safe desktop job queue with subscriber callbacks for WebSocket push."""

    def __init__(self) -> None:
        self._jobs: dict[str, DesktopJob] = {}
        self._lock = threading.Lock()
        self._listeners: list[Callable[[DesktopJob], None]] = []

    def subscribe(self, listener: Callable[[DesktopJob], None]) -> None:
        self._listeners.append(listener)

    def unsubscribe(self, listener: Callable[[DesktopJob], None]) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def _emit(self, job: DesktopJob) -> None:
        for listener in list(self._listeners):
            try:
                listener(job)
            except Exception:
                pass

    def _update(self, job_id: str, **fields: Any) -> DesktopJob:
        with self._lock:
            job = self._jobs[job_id]
            for key, value in fields.items():
                setattr(job, key, value)
        self._emit(job)
        return job

    def list_jobs(self, limit: int = 50) -> list[DesktopJob]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]

    def get_job(self, job_id: str) -> DesktopJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def submit(
        self,
        source: str,
        output_format: str,
        *,
        options: JobOptions | None = None,
        prores_profile: str = "hq",
    ) -> DesktopJob:
        fmt = output_format.lower()
        if fmt not in OUTPUT_FORMATS:
            raise ValueError(f"Unsupported format. Choose: {OUTPUT_FORMATS}")

        job_id = str(uuid.uuid4())
        opts = options or JobOptions()
        job = DesktopJob(
            id=job_id,
            source=source,
            output_format=fmt,
            options=opts.to_dict(),
        )
        with self._lock:
            self._jobs[job_id] = job
        self._emit(job)

        thread = threading.Thread(
            target=self._run_job,
            args=(job_id, source, fmt, opts, prores_profile),
            daemon=True,
        )
        thread.start()
        return job

    def _run_job(
        self,
        job_id: str,
        source: str,
        output_format: str,
        options: JobOptions,
        prores_profile: str,
    ) -> None:
        self._update(job_id, status="running", message="Starting conversion…", progress=0.05)

        def on_status(msg: str) -> None:
            job = self.get_job(job_id)
            progress = min(0.92, (job.progress if job else 0.05) + 0.08)
            self._update(job_id, message=msg, progress=progress)

        def on_telemetry(telemetry) -> None:
            self._update(
                job_id,
                fps=telemetry.fps,
                speed=telemetry.speed,
                elapsed=telemetry.time,
                progress=min(0.95, max(0.1, self.get_job(job_id).progress if self.get_job(job_id) else 0.1)),
            )

        # Patch telemetry via wrapper — pipeline passes TelemetryState internally
        try:
            if output_format == "prores":
                result = run_transcode_prores(
                    source,
                    profile=prores_profile,
                    options=options,
                    job_id=job_id,
                    on_status=on_status,
                    upload_after_verify=options.upload_after_verify,
                )
            else:
                result = run_transcode(
                    source,
                    output_format,
                    options=options,
                    job_id=job_id,
                    on_status=on_status,
                    upload_after_verify=options.upload_after_verify,
                )

            if result.telemetry:
                on_telemetry(result.telemetry)

            if result.success:
                payload = {
                    "output_path": str(result.output_path) if result.output_path else None,
                    "sha256": result.sha256,
                    "source_sha256": result.source_sha256,
                    "evidence_bundle": str(result.evidence_bundle) if result.evidence_bundle else None,
                    "ffmpeg_command": result.ffmpeg_command,
                    "upload_destinations": result.upload_destinations,
                }
                self._update(
                    job_id,
                    status="completed",
                    message="Conversion complete",
                    progress=1.0,
                    result=payload,
                    completed_at=time.time(),
                )
            else:
                self._update(
                    job_id,
                    status="failed",
                    message="Conversion failed",
                    error=result.error or "Unknown error",
                    progress=1.0,
                    completed_at=time.time(),
                )
        except Exception as exc:
            self._update(
                job_id,
                status="failed",
                message="Conversion failed",
                error=str(exc),
                progress=1.0,
                completed_at=time.time(),
            )


job_manager = JobManager()


def doctor_report() -> dict[str, Any]:
    """System health for the desktop splash / settings panel."""
    checks = []
    for binary in ("ffmpeg", "ffprobe", "yt-dlp"):
        path = shutil.which(binary)
        checks.append({"name": binary, "ok": bool(path), "path": path or "not found"})

    return {
        "suite_version": suite_version,
        "desktop_version": "2.0.0",
        "checks": checks,
        "hardware": {
            "h264_videotoolbox": videotoolbox_h264_available(),
            "hevc_videotoolbox": videotoolbox_hevc_available(),
            "prores_videotoolbox": prores_videotoolbox_available(),
            "aac_at": aac_at_available(),
        },
        "formats": OUTPUT_FORMATS,
        "prores_profiles": list(PRORES_PROFILES),
        "standards": [
            "EBU R128 loudness (-23 LUFS)",
            "ISOBMFF +faststart (mp4/mov)",
            "SHA-256 + MD5 custody digests",
            "Metadata & chapter preservation",
            "Dolby Vision bitstream copy (strict mode)",
        ],
    }


def probe_media(source: str) -> dict[str, Any]:
    """Probe local file or remote URL for the inspector panel."""
    if is_remote_url(source):
        profile = probe_source(source)
        return {
            "source": source,
            "is_remote": True,
            "title": profile.title,
            "has_video": profile.has_video,
            "has_subtitles": profile.has_subtitles,
            "audio_channels": profile.audio_channels,
            "is_hdr": profile.is_hdr,
            "duration": profile.duration,
        }

    path = Path(source).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    catalog = probe_file(path)
    video = catalog.primary_video
    audio = catalog.primary_audio
    return {
        "source": str(path),
        "is_remote": False,
        "title": path.stem,
        "format": catalog.format_name,
        "duration_sec": catalog.duration_sec,
        "size_bytes": catalog.size_bytes,
        "streams": len(catalog.streams),
        "video": {
            "codec": video.codec if video else None,
            "resolution": f"{video.width}x{video.height}" if video else None,
            "color_science": video.color_science.value if video else None,
            "frame_rate": video.frame_rate if video else None,
        },
        "audio": {
            "codec": audio.codec if audio else None,
            "channels": audio.channels if audio else 0,
            "layout": audio.channel_layout if audio else "",
            "sample_rate": audio.sample_rate if audio else 0,
        },
        "subtitles": len(catalog.subtitle_streams),
    }


def preview_plan(source: str, output_format: str, options: JobOptions | None = None) -> dict[str, Any]:
    """Show ConversionEngine plan before executing (local files only; remote returns probe summary)."""
    opts = options or JobOptions()
    profile = probe_source(source)

    if is_remote_url(source):
        fmt = coerce_output_format(profile, output_format, opts.auto_classify)
        return {
            "output_format": fmt,
            "remote": True,
            "title": profile.title,
            "has_video": profile.has_video,
            "note": "Full stream plan is computed after source materialization at convert time.",
        }

    path = Path(source).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    fmt = coerce_output_format(profile, output_format, opts.auto_classify)
    ext = "mov" if fmt == "prores" else fmt
    audio_only = opts.auto_classify and is_music_classification(profile, fmt)
    output_path = resolve_output_path(profile, fmt, audio_only_folder=audio_only, extension=ext)

    request = options_to_request(
        local_path=path,
        output_format=fmt,
        output_path=output_path,
        options=opts,
        prores_profile="hq",
        job_id=None,
    )
    catalog = probe_file(path)
    plan = build_plan(request, catalog)
    return {
        "output_format": fmt,
        "output_path": str(output_path),
        "summary": plan.summary(),
        "global_mode": plan.global_mode.value,
        "mappings": [
            {
                "input": m.input_index,
                "kind": m.kind.value,
                "mode": m.mode.value,
                "codec": m.output_codec,
                "reason": m.reason,
            }
            for m in plan.mappings
        ],
        "preservation_notes": plan.preservation_notes,
        "warnings": plan.warnings,
    }
