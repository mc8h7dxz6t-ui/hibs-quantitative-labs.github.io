"""SQLite-backed persistent job queue for 24/7 farm processing."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator

from media_suite.config import JOB_MAX_RETRIES, JOBS_DB

_db_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    output_format TEXT NOT NULL DEFAULT 'mp4',
    prores_profile TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    priority INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 3,
    options_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    result_json TEXT,
    created_at REAL NOT NULL,
    started_at REAL,
    completed_at REAL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status_priority ON jobs(status, priority DESC, created_at);
"""


@dataclass
class JobOptions:
    embed_subtitles: bool = True
    normalize_lufs: bool = False
    auto_classify: bool = True
    upload_after_verify: bool = True
    forensic_mode: bool = False
    preserve_source: bool = False
    case_id: str | None = None
    operator_id: str | None = None
    strict_hdr: bool = False
    strict_dolby_vision: bool = False
    strict_surround: bool = False
    idempotency_key: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobOptions:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class Job:
    id: str
    source: str
    output_format: str
    prores_profile: str | None
    status: str
    priority: int
    attempts: int
    max_retries: int
    options: JobOptions
    error: str | None = None
    result: dict[str, Any] | None = None
    created_at: float = 0.0
    started_at: float | None = None
    completed_at: float | None = None


def init_db(db_path: Path | None = None) -> Path:
    path = db_path or JOBS_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
    return path


_db_initialized: set[str] = set()


@contextmanager
def _connect(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    path = db_path or JOBS_DB
    key = str(path.resolve())
    if key not in _db_initialized:
        init_db(path)
        _db_initialized.add(key)
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _row_to_job(row: sqlite3.Row) -> Job:
    opts = JobOptions.from_dict(json.loads(row["options_json"] or "{}"))
    result = json.loads(row["result_json"]) if row["result_json"] else None
    return Job(
        id=row["id"],
        source=row["source"],
        output_format=row["output_format"],
        prores_profile=row["prores_profile"],
        status=row["status"],
        priority=row["priority"],
        attempts=row["attempts"],
        max_retries=row["max_retries"],
        options=opts,
        error=row["error"],
        result=result,
        created_at=row["created_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
    )


def enqueue_job(
    source: str,
    output_format: str = "mp4",
    *,
    prores_profile: str | None = None,
    priority: int = 0,
    options: JobOptions | None = None,
    max_retries: int = JOB_MAX_RETRIES,
) -> Job:
    job_id = str(uuid.uuid4())
    opts = options or JobOptions()
    now = time.time()

    with _db_lock, _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            INSERT INTO jobs (id, source, output_format, prores_profile, status, priority,
                              max_retries, options_json, created_at)
            VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?)
            """,
            (
                job_id,
                source,
                output_format,
                prores_profile,
                priority,
                max_retries,
                json.dumps(opts.to_dict()),
                now,
            ),
        )
        conn.execute("COMMIT")

    return get_job(job_id)


def get_job(job_id: str) -> Job:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        raise KeyError(f"Job not found: {job_id}")
    return _row_to_job(row)


def list_jobs(status: str | None = None, limit: int = 50) -> list[Job]:
    with _connect() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [_row_to_job(r) for r in rows]


def claim_next_job() -> Job | None:
    """Atomically claim highest-priority pending job."""
    with _db_lock, _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT * FROM jobs
            WHERE status = 'pending'
            ORDER BY priority DESC, created_at ASC
            LIMIT 1
            """
        ).fetchone()
        if not row:
            conn.execute("COMMIT")
            return None

        job_id = row["id"]
        now = time.time()
        conn.execute(
            "UPDATE jobs SET status = 'running', started_at = ?, attempts = attempts + 1 WHERE id = ?",
            (now, job_id),
        )
        conn.execute("COMMIT")
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_job(row) if row else None


def complete_job(job_id: str, result: dict[str, Any]) -> None:
    with _db_lock, _connect() as conn:
        conn.execute(
            """
            UPDATE jobs SET status = 'completed', result_json = ?, completed_at = ?, error = NULL
            WHERE id = ?
            """,
            (json.dumps(result), time.time(), job_id),
        )


def fail_job(job_id: str, error: str) -> str:
    """Mark failed or requeue for retry. Returns new status."""
    with _db_lock, _connect() as conn:
        row = conn.execute("SELECT attempts, max_retries FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            return "missing"
        attempts = row["attempts"]
        max_retries = row["max_retries"]
        if attempts < max_retries:
            conn.execute(
                "UPDATE jobs SET status = 'pending', error = ?, started_at = NULL WHERE id = ?",
                (error, job_id),
            )
            return "pending"
        conn.execute(
            "UPDATE jobs SET status = 'dead', error = ?, completed_at = ? WHERE id = ?",
            (error, time.time(), job_id),
        )
        return "dead"


def queue_stats() -> dict[str, int]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM jobs GROUP BY status"
        ).fetchall()
    stats = {r["status"]: r["cnt"] for r in rows}
    stats["total"] = sum(stats.values())
    return stats
