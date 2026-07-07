"""24/7 worker farm — claims jobs from SQLite and executes transcodes."""

from __future__ import annotations

import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor, Future

from media_suite.config import WORKER_CONCURRENCY, WORKER_POLL_SECONDS
from media_suite.jobs import Job, claim_next_job, complete_job, fail_job, init_db
from media_suite.pipeline import run_transcode, run_transcode_prores


class WorkerFarm:
    def __init__(self, concurrency: int = WORKER_CONCURRENCY) -> None:
        self.concurrency = concurrency
        self._stop = threading.Event()
        self._pool: ThreadPoolExecutor | None = None
        self._active: set[Future] = set()
        self._lock = threading.Lock()

    def stop(self) -> None:
        self._stop.set()

    def _process_job(self, job: Job) -> None:
        opts = job.options

        def on_status(msg: str) -> None:
            print(f"[job {job.id[:8]}] {msg}")

        try:
            if job.output_format == "prores":
                result = run_transcode_prores(
                    job.source,
                    profile=job.prores_profile or "hq",
                    job_id=job.id,
                    options=opts,
                    on_status=on_status,
                )
            else:
                result = run_transcode(
                    job.source,
                    job.output_format,
                    job_id=job.id,
                    options=opts,
                    on_status=on_status,
                )

            if result.success:
                complete_job(
                    job.id,
                    {
                        "output_path": str(result.output_path) if result.output_path else None,
                        "sha256": result.sha256,
                        "source_sha256": result.source_sha256,
                        "evidence_bundle": str(result.evidence_bundle) if result.evidence_bundle else None,
                        "upload_destinations": result.upload_destinations,
                    },
                )
            else:
                fail_job(job.id, result.error or "Unknown error")
        except Exception as exc:  # noqa: BLE001 — worker must not crash farm
            fail_job(job.id, str(exc))

    def _reap_done(self) -> None:
        with self._lock:
            done = {f for f in self._active if f.done()}
            self._active -= done

    def _active_count(self) -> int:
        with self._lock:
            return len(self._active)

    def run(self) -> None:
        init_db()
        self._pool = ThreadPoolExecutor(max_workers=self.concurrency, thread_name_prefix="farm")

        def handle_signal(signum, frame) -> None:  # noqa: ARG001
            print(f"\n[worker] signal {signum} — shutting down gracefully…")
            self.stop()

        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)

        print(f"[worker] farm started — concurrency={self.concurrency}")

        while not self._stop.is_set():
            self._reap_done()

            if self._active_count() >= self.concurrency:
                time.sleep(WORKER_POLL_SECONDS)
                continue

            job = claim_next_job()
            if not job:
                time.sleep(WORKER_POLL_SECONDS)
                continue

            print(f"[worker] claimed job {job.id} — {job.source[:80]}")
            assert self._pool is not None
            future = self._pool.submit(self._process_job, job)
            with self._lock:
                self._active.add(future)

        if self._pool:
            self._pool.shutdown(wait=True, cancel_futures=False)
        print("[worker] farm stopped")


def run_worker_farm(concurrency: int | None = None) -> None:
    farm = WorkerFarm(concurrency=concurrency or WORKER_CONCURRENCY)
    farm.run()
