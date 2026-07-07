"""Conversion engine — orchestrates probe → plan → custody → execute."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from media_engine.backend_ffmpeg import build_ffmpeg_command
from media_engine.custody import CustodyLedger
from media_engine.planner import build_plan
from media_engine.probe import probe_file
from media_engine.types import ConversionRequest, CustodyStage, EngineResult


class ConversionEngine:
    """
    Owned pipeline construct.

    Stages:
      1. INGEST    — resolve path, hash source file
      2. PROBE     — build MediaCatalog (our schema)
      3. PLAN      — decide remux/transcode per stream (our logic)
      4. EXECUTE   — backend runs argv we generated
      5. VERIFY    — hash output, write custody bundle
    """

    def __init__(self, custody_dir: Path | None = None) -> None:
        self.custody_dir = custody_dir or Path("engine_output/custody")

    def convert(self, request: ConversionRequest) -> EngineResult:
        if not shutil.which("ffmpeg"):
            return EngineResult(success=False, error="ffmpeg not on PATH")
        if not shutil.which("ffprobe"):
            return EngineResult(success=False, error="ffprobe not on PATH")

        input_path = request.input_path.expanduser().resolve()
        if not input_path.is_file():
            return EngineResult(success=False, error=f"Input not found: {input_path}")

        ledger = CustodyLedger(case_id=request.case_id)

        # Stage 1: INGEST
        ledger.record(CustodyStage.SOURCE_FILE, path=input_path)

        # Stage 2: PROBE
        try:
            catalog = probe_file(input_path)
        except (subprocess.CalledProcessError, OSError, json.JSONDecodeError) as exc:
            return EngineResult(success=False, error=f"Probe failed: {exc}", custody_events=ledger.events)

        probe_snapshot = self.custody_dir / f"{input_path.stem}_probe.json"
        probe_snapshot.parent.mkdir(parents=True, exist_ok=True)
        probe_snapshot.write_text(json.dumps(catalog.raw_probe, indent=2), encoding="utf-8")
        ledger.record(
            CustodyStage.PROBE_SNAPSHOT,
            path=probe_snapshot,
            metadata={
                "format": catalog.format_name,
                "streams": len(catalog.streams),
                "duration": catalog.duration_sec,
            },
        )

        # Stage 3: PLAN
        try:
            plan = build_plan(request, catalog)
        except ValueError as exc:
            return EngineResult(success=False, error=str(exc), custody_events=ledger.events)

        plan_doc = self.custody_dir / f"{input_path.stem}_plan.json"
        plan_doc.write_text(
            json.dumps(
                {
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
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        ledger.record(CustodyStage.PLAN_ISSUED, path=plan_doc, metadata={"summary": plan.summary()})

        # Stage 4: EXECUTE
        ffmpeg_cmd = build_ffmpeg_command(plan)
        try:
            proc = subprocess.run(
                ffmpeg_cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=7200,
            )
            stderr_path = self.custody_dir / f"{input_path.stem}_ffmpeg.log"
            stderr_path.write_text(proc.stderr or "", encoding="utf-8")
            ledger.record(CustodyStage.EXECUTION_STDERR, path=stderr_path)
        except subprocess.CalledProcessError as exc:
            return EngineResult(
                success=False,
                error=(exc.stderr or str(exc))[:2000],
                plan=plan,
                custody_events=ledger.events,
            )
        except subprocess.TimeoutExpired:
            return EngineResult(success=False, error="FFmpeg timeout", plan=plan, custody_events=ledger.events)

        # Stage 5: VERIFY
        if not plan.output_path.exists():
            return EngineResult(success=False, error="Output missing", plan=plan, custody_events=ledger.events)

        ledger.record(CustodyStage.OUTPUT_FILE, path=plan.output_path)
        bundle = ledger.write_bundle(self.custody_dir / input_path.stem, plan.summary())

        return EngineResult(
            success=True,
            plan=plan,
            output_path=plan.output_path,
            custody_events=ledger.events,
        )
