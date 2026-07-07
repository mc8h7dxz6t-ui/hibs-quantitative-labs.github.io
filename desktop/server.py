"""Local FastAPI server for the desktop UI — no auth on localhost."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from desktop import __version__
from desktop.service import DesktopJob, doctor_report, job_manager, preview_plan, probe_media
from media_suite.encoders import OUTPUT_FORMATS, PRORES_PROFILES
from media_suite.jobs import JobOptions

STATIC_DIR = Path(__file__).resolve().parent / "static"


class ConvertRequest(BaseModel):
    source: str
    output_format: str = Field("mp4", alias="format")
    prores_profile: str = "hq"
    forensic_mode: bool = True
    preserve_source: bool = True
    strict_hdr: bool = False
    strict_dolby_vision: bool = False
    strict_surround: bool = False
    embed_subtitles: bool = True
    normalize_lufs: bool = False
    auto_classify: bool = True
    upload_after_verify: bool = False

    model_config = {"populate_by_name": True}


class ProbeRequest(BaseModel):
    source: str


class PlanRequest(BaseModel):
    source: str
    output_format: str = Field("mp4", alias="format")
    forensic_mode: bool = True
    preserve_source: bool = True
    strict_hdr: bool = False
    strict_dolby_vision: bool = False
    strict_surround: bool = False
    embed_subtitles: bool = True
    normalize_lufs: bool = False
    auto_classify: bool = True

    model_config = {"populate_by_name": True}


def _job_payload(job: DesktopJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "source": job.source,
        "output_format": job.output_format,
        "status": job.status,
        "message": job.message,
        "progress": job.progress,
        "fps": job.fps,
        "speed": job.speed,
        "elapsed": job.elapsed,
        "error": job.error,
        "result": job.result,
        "options": job.options,
        "created_at": job.created_at,
        "completed_at": job.completed_at,
    }


def _options_from_body(body: ConvertRequest | PlanRequest) -> JobOptions:
    return JobOptions(
        embed_subtitles=body.embed_subtitles,
        normalize_lufs=body.normalize_lufs,
        auto_classify=body.auto_classify,
        upload_after_verify=getattr(body, "upload_after_verify", False),
        forensic_mode=body.forensic_mode,
        preserve_source=body.preserve_source,
        strict_hdr=body.strict_hdr,
        strict_dolby_vision=body.strict_dolby_vision,
        strict_surround=body.strict_surround,
    )


def create_desktop_app() -> FastAPI:
    app = FastAPI(
        title="HIBS Media Studio",
        version=__version__,
        description="Desktop API for forensic media conversion.",
    )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/api/doctor")
    def doctor() -> dict[str, Any]:
        return doctor_report()

    @app.get("/api/formats")
    def formats() -> dict[str, Any]:
        return {"formats": OUTPUT_FORMATS, "prores_profiles": list(PRORES_PROFILES)}

    @app.post("/api/probe")
    def probe(body: ProbeRequest) -> dict[str, Any]:
        try:
            return probe_media(body.source)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/plan")
    def plan(body: PlanRequest) -> dict[str, Any]:
        try:
            return preview_plan(body.source, body.output_format, options=_options_from_body(body))
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/convert", status_code=202)
    def convert(body: ConvertRequest) -> dict[str, Any]:
        fmt = body.output_format.lower()
        if fmt not in OUTPUT_FORMATS:
            raise HTTPException(400, f"Unsupported format: {OUTPUT_FORMATS}")
        if body.prores_profile not in PRORES_PROFILES:
            raise HTTPException(400, f"Invalid prores profile: {list(PRORES_PROFILES)}")

        job = job_manager.submit(
            body.source,
            fmt,
            options=_options_from_body(body),
            prores_profile=body.prores_profile,
        )
        return _job_payload(job)

    @app.get("/api/jobs")
    def jobs_list(limit: int = 50) -> list[dict[str, Any]]:
        return [_job_payload(j) for j in job_manager.list_jobs(limit=limit)]

    @app.get("/api/jobs/{job_id}")
    def jobs_get(job_id: str) -> dict[str, Any]:
        job = job_manager.get_job(job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        return _job_payload(job)

    @app.websocket("/ws/jobs/{job_id}")
    async def job_ws(websocket: WebSocket, job_id: str) -> None:
        await websocket.accept()
        job = job_manager.get_job(job_id)
        if not job:
            await websocket.close(code=4404)
            return

        loop = asyncio.get_event_loop()
        queue: asyncio.Queue[DesktopJob] = asyncio.Queue()

        def on_update(updated: DesktopJob) -> None:
            if updated.id == job_id:
                loop.call_soon_threadsafe(queue.put_nowait, updated)

        job_manager.subscribe(on_update)
        await websocket.send_json(_job_payload(job))

        try:
            while True:
                updated = await queue.get()
                await websocket.send_json(_job_payload(updated))
                if updated.status in {"completed", "failed"}:
                    break
                await asyncio.sleep(0)
        except WebSocketDisconnect:
            pass
        finally:
            job_manager.unsubscribe(on_update)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app


desktop_app = create_desktop_app()
