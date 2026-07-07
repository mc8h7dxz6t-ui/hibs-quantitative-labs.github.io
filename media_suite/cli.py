"""Command-line interface."""

from __future__ import annotations

import argparse
import sys

from media_suite import __version__
from media_suite.config import (
    API_HOST,
    API_PORT,
    API_TOKEN,
    DEFAULT_FORMAT,
    DEFAULT_PRORES_PROFILE,
    EVIDENCE_DIR,
    JOBS_DB,
    NAS_DEST_PATH,
    OUTPUT_DIR,
    PRORES_OUTPUT_DIR,
    S3_BUCKET,
    UPLOAD_ENABLED,
    WORKER_CONCURRENCY,
)
from media_suite.dashboard import run_dashboard
from media_suite.encoders import OUTPUT_FORMATS, PRORES_PROFILES
from media_suite.input import expand_inputs, resolve_input
from media_suite.jobs import JobOptions, init_db
from media_suite.pipeline import run_batch, run_transcode, run_transcode_prores
from media_suite.upload import upload_configured


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="m5-forensic-media-suite",
        description="Production any-to-any media farm: forensic custody, strict HDR/DV/5.1, 24/7 workers, internet API.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    convert = sub.add_parser("convert", help="Convert one file or URL")
    convert.add_argument("input")
    convert.add_argument("-f", "--format", default=DEFAULT_FORMAT, choices=OUTPUT_FORMATS)
    convert.add_argument("--forensic", action="store_true", help="Full chain-of-custody bundle")
    convert.add_argument("--preserve-source", action="store_true", help="Archive remote source before transcode")
    convert.add_argument("--strict-hdr", action="store_true")
    convert.add_argument("--strict-dv", action="store_true")
    convert.add_argument("--strict-surround", action="store_true")
    convert.add_argument("--case-id")
    convert.add_argument("--no-subs", action="store_true")
    convert.add_argument("--normalize", action="store_true")
    convert.add_argument("--no-upload", action="store_true")

    prores = sub.add_parser("prores", help="ProRes mastering")
    prores.add_argument("input")
    prores.add_argument("--profile", default=DEFAULT_PRORES_PROFILE, choices=list(PRORES_PROFILES))
    prores.add_argument("--forensic", action="store_true")
    prores.add_argument("--strict-dv", action="store_true")
    prores.add_argument("--no-upload", action="store_true")

    batch = sub.add_parser("batch", help="Batch folder/playlist")
    batch.add_argument("input")
    batch.add_argument("-f", "--format", default=DEFAULT_FORMAT, choices=OUTPUT_FORMATS)
    batch.add_argument("--forensic", action="store_true")
    batch.add_argument("--no-upload", action="store_true")

    api = sub.add_parser("api", help="Internet-facing FastAPI (put behind nginx/Caddy TLS)")
    api.add_argument("--host", default=API_HOST)
    api.add_argument("--port", type=int, default=API_PORT)

    worker = sub.add_parser("worker", help="24/7 job farm worker")
    worker.add_argument("--concurrency", type=int, default=WORKER_CONCURRENCY)

    sub.add_parser("watch", help="Legacy text-queue dashboard")
    sub.add_parser("doctor", help="Verify tooling and configuration")

    desktop = sub.add_parser("desktop", help="Launch HIBS Media Studio desktop app")
    desktop.add_argument("--host", default="127.0.0.1")
    desktop.add_argument("--port", type=int, default=None)
    desktop.add_argument("--browser", action="store_true", help="Open browser if pywebview unavailable")

    # Legacy alias
    serve = sub.add_parser("serve", help="Alias for api")
    serve.add_argument("--host", default=API_HOST)
    serve.add_argument("--port", type=int, default=API_PORT)

    return parser


def _options_from_args(args) -> JobOptions:
    return JobOptions(
        embed_subtitles=not getattr(args, "no_subs", False),
        normalize_lufs=getattr(args, "normalize", False),
        upload_after_verify=not getattr(args, "no_upload", False),
        forensic_mode=getattr(args, "forensic", False),
        preserve_source=getattr(args, "preserve_source", False),
        case_id=getattr(args, "case_id", None),
        strict_hdr=getattr(args, "strict_hdr", False),
        strict_dolby_vision=getattr(args, "strict_dv", False),
        strict_surround=getattr(args, "strict_surround", False),
    )


def cmd_doctor() -> int:
    import shutil

    from media_suite.platform import (
        aac_at_available,
        is_apple_silicon,
        is_macos,
        prores_videotoolbox_available,
        videotoolbox_h264_available,
        videotoolbox_hevc_available,
    )

    ok = True
    for tool in ("ffmpeg", "yt-dlp", "ffprobe"):
        path = shutil.which(tool)
        print(f"{'✓' if path else '✗'} {tool}: {path or 'NOT FOUND'}")
        ok = ok and bool(path)

    init_db()
    print(f"  macOS: {is_macos()} | Apple Silicon: {is_apple_silicon()}")
    print(f"  h264_videotoolbox: {videotoolbox_h264_available()}")
    print(f"  prores_videotoolbox: {prores_videotoolbox_available()}")
    print(f"  aac_at: {aac_at_available()}")
    print(f"  Jobs DB: {JOBS_DB.resolve()}")
    print(f"  Evidence dir: {EVIDENCE_DIR.resolve()}")
    print(f"  Output formats: {', '.join(OUTPUT_FORMATS)}")
    print(f"  Upload: {UPLOAD_ENABLED} | configured: {upload_configured()}")
    print(f"  API token set: {bool(API_TOKEN)}")
    print(f"  API bind: {API_HOST}:{API_PORT}")
    if S3_BUCKET:
        print(f"  S3: {S3_BUCKET}")
    if NAS_DEST_PATH:
        print(f"  NAS: {NAS_DEST_PATH}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "doctor":
        return cmd_doctor()

    if args.command == "desktop":
        from desktop.__main__ import launch_desktop

        return launch_desktop(host=args.host, port=args.port, browser_fallback=args.browser)

    if args.command == "watch":
        run_dashboard()
        return 0

    if args.command in {"api", "serve"}:
        if not API_TOKEN:
            print("Warning: MEDIA_SUITE_API_TOKEN unset — authenticated endpoints will reject requests.", file=sys.stderr)
        from media_suite.api import run_api

        run_api(host=args.host, port=args.port)
        return 0

    if args.command == "worker":
        from media_suite.worker import run_worker_farm

        run_worker_farm(concurrency=args.concurrency)
        return 0

    opts = _options_from_args(args)

    if args.command == "prores":
        result = run_transcode_prores(args.input, profile=args.profile, options=opts)
        if not result.success:
            print(result.error, file=sys.stderr)
            return 1
        return 0

    if args.command == "convert":
        try:
            resolve_input(args.input)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 1
        result = run_transcode(args.input, args.format, options=opts)
        if not result.success:
            print(result.error, file=sys.stderr)
            return 1
        return 0

    if args.command == "batch":
        sources = expand_inputs(args.input)
        if not sources:
            print("No inputs resolved.", file=sys.stderr)
            return 1
        print(f"Resolved {len(sources)} input(s)")
        results = run_batch(sources, args.format, options=opts)
        return 1 if any(not r.success for r in results) else 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
