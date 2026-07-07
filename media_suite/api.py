"""Internet-facing FastAPI — job submission, status, health."""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from media_suite import __version__
from media_suite.config import API_CORS_ORIGINS, API_RATE_LIMIT, API_TOKEN
from media_suite.encoders import OUTPUT_FORMATS, PRORES_PROFILES
from media_suite.jobs import JobOptions, enqueue_job, get_job, init_db, list_jobs, queue_stats

_bearer = HTTPBearer(auto_error=False)
_rate_buckets: dict[str, list[float]] = defaultdict(list)


def _parse_rate_limit(spec: str) -> tuple[int, float]:
    try:
        count, period = spec.split("/")
        windows = {"second": 1.0, "minute": 60.0, "hour": 3600.0}
        return int(count), windows.get(period, 60.0)
    except ValueError:
        return 60, 60.0


_RATE_MAX, _RATE_WINDOW = _parse_rate_limit(API_RATE_LIMIT)


def verify_token(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> None:
    if not API_TOKEN:
        raise HTTPException(503, "API_TOKEN not configured")
    if not credentials or credentials.credentials != API_TOKEN:
        raise HTTPException(401, "Invalid or missing bearer token")


def rate_limit(request: Request) -> None:
    client = request.client.host if request.client else "unknown"
    now = time.time()
    bucket = _rate_buckets[client]
    _rate_buckets[client] = [t for t in bucket if now - t < _RATE_WINDOW]
    if len(_rate_buckets[client]) >= _RATE_MAX:
        raise HTTPException(429, "Rate limit exceeded")
    _rate_buckets[client].append(now)


class JobCreateRequest(BaseModel):
    input: str = Field(..., description="Local path, folder, or remote URL")
    output_format: str = Field("mp4", alias="format")
    prores_profile: str | None = None
    priority: int = 0
    case_id: str | None = None
    operator_id: str | None = None
    forensic_mode: bool = False
    preserve_source: bool = False
    strict_hdr: bool = False
    strict_dolby_vision: bool = False
    strict_surround: bool = False
    embed_subtitles: bool = True
    normalize_lufs: bool = False
    upload_after_verify: bool = True

    model_config = {"populate_by_name": True}


class JobResponse(BaseModel):
    id: str
    status: str
    source: str
    output_format: str
    priority: int
    attempts: int
    error: str | None = None
    result: dict[str, Any] | None = None
    created_at: float
    completed_at: float | None = None


def _job_to_response(job) -> JobResponse:
    return JobResponse(
        id=job.id,
        status=job.status,
        source=job.source,
        output_format=job.output_format,
        priority=job.priority,
        attempts=job.attempts,
        error=job.error,
        result=job.result,
        created_at=job.created_at,
        completed_at=job.completed_at,
    )


def create_app() -> FastAPI:
    app = FastAPI(
        title="Forensic Media Suite API",
        version=__version__,
        description="Production job API for any-file conversion with forensic chain of custody.",
    )

    origins = [o.strip() for o in API_CORS_ORIGINS.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    def startup() -> None:
        init_db()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/ready", dependencies=[Depends(rate_limit)])
    def ready() -> dict[str, Any]:
        return {"status": "ready", "queue": queue_stats(), "auth_required": bool(API_TOKEN)}

    @app.get("/v1/jobs", dependencies=[Depends(verify_token), Depends(rate_limit)])
    def jobs_list(status: str | None = None, limit: int = 50) -> list[JobResponse]:
        return [_job_to_response(j) for j in list_jobs(status=status, limit=limit)]

    @app.get("/v1/jobs/{job_id}", dependencies=[Depends(verify_token), Depends(rate_limit)])
    def jobs_get(job_id: str) -> JobResponse:
        try:
            return _job_to_response(get_job(job_id))
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/v1/jobs", status_code=202, dependencies=[Depends(verify_token), Depends(rate_limit)])
    def jobs_create(body: JobCreateRequest) -> JobResponse:
        fmt = body.output_format.lower()
        if fmt not in OUTPUT_FORMATS:
            raise HTTPException(400, f"Unsupported format. Choose: {OUTPUT_FORMATS}")
        if body.prores_profile and body.prores_profile not in PRORES_PROFILES:
            raise HTTPException(400, f"Invalid prores_profile: {list(PRORES_PROFILES)}")

        options = JobOptions(
            embed_subtitles=body.embed_subtitles,
            normalize_lufs=body.normalize_lufs,
            upload_after_verify=body.upload_after_verify,
            forensic_mode=body.forensic_mode,
            preserve_source=body.preserve_source,
            case_id=body.case_id,
            operator_id=body.operator_id,
            strict_hdr=body.strict_hdr,
            strict_dolby_vision=body.strict_dolby_vision,
            strict_surround=body.strict_surround,
        )
        job = enqueue_job(body.input, fmt, prores_profile=body.prores_profile, priority=body.priority, options=options)
        return _job_to_response(job)

    @app.post("/queue", status_code=202, dependencies=[Depends(verify_token), Depends(rate_limit)])
    def legacy_queue(body: JobCreateRequest) -> dict[str, Any]:
        """Backward-compatible webhook path."""
        resp = jobs_create(body)
        return {"status": "queued", "job_id": resp.id, "queue_depth": queue_stats().get("pending", 0)}

    return app


app = create_app()


def run_api(host: str | None = None, port: int | None = None) -> None:
    import uvicorn

    from media_suite.config import API_HOST, API_PORT

    uvicorn.run(
        "media_suite.api:app",
        host=host or API_HOST,
        port=port or API_PORT,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
