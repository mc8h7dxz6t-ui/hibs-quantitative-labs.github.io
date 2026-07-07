#!/usr/bin/env python3
"""CLI for the owned media_engine construct."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from media_engine import ConversionEngine, ConversionRequest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Media Engine — owned probe/plan/custody/execute construct",
    )
    parser.add_argument("input", type=Path, help="Input media file")
    parser.add_argument("-f", "--format", required=True, help="Output format (mp4, mkv, mp3, …)")
    parser.add_argument("-o", "--output-dir", type=Path, help="Output directory")
    parser.add_argument("--probe-only", action="store_true", help="Stop after catalog (no convert)")
    parser.add_argument("--plan-only", action="store_true", help="Stop after plan (no execute)")
    parser.add_argument("--copy-video", action="store_true", help="Require bitstream video copy")
    parser.add_argument("--require-hdr", action="store_true")
    parser.add_argument("--require-surround", action="store_true")
    parser.add_argument("--case-id")
    parser.add_argument("--custody-dir", type=Path, default=Path("engine_output/custody"))

    args = parser.parse_args(argv)
    engine = ConversionEngine(custody_dir=args.custody_dir)

    if args.probe_only:
        from media_engine.probe import probe_file

        catalog = probe_file(args.input)
        print(json.dumps(
            {
                "path": str(catalog.source_path),
                "format": catalog.format_name,
                "duration": catalog.duration_sec,
                "streams": [
                    {
                        "index": s.index,
                        "kind": s.kind.value,
                        "codec": s.codec,
                        "color": s.color_science.value,
                        "channels": s.channels,
                        "layout": s.channel_layout,
                    }
                    for s in catalog.streams
                ],
            },
            indent=2,
        ))
        return 0

    request = ConversionRequest(
        input_path=args.input,
        output_format=args.format,
        output_dir=args.output_dir,
        require_bitstream_video=args.copy_video,
        require_hdr_metadata=args.require_hdr,
        require_surround_audio=args.require_surround,
        case_id=args.case_id,
    )

    if args.plan_only:
        from media_engine.planner import build_plan
        from media_engine.probe import probe_file

        catalog = probe_file(args.input)
        plan = build_plan(request, catalog)
        print(json.dumps(
            {
                "summary": plan.summary(),
                "output": str(plan.output_path),
                "global_mode": plan.global_mode.value,
                "mappings": [
                    {"input": m.input_index, "mode": m.mode.value, "codec": m.output_codec, "reason": m.reason}
                    for m in plan.mappings
                ],
                "notes": plan.preservation_notes,
                "warnings": plan.warnings,
            },
            indent=2,
        ))
        return 0

    result = engine.convert(request)
    if not result.success:
        print(result.error, file=sys.stderr)
        return 1

    print(f"OK → {result.output_path}")
    print(f"Mode: {result.plan.global_mode.value if result.plan else '?'}")
    for e in result.custody_events:
        print(f"  [{e.stage.value}] sha256={e.sha256 or '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
