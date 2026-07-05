"""Command-line interface."""

from __future__ import annotations

import argparse
import sys

from media_suite import __version__
from media_suite.config import (
    DEFAULT_FORMAT,
    DEFAULT_PRORES_PROFILE,
    NAS_DEST_PATH,
    OUTPUT_DIR,
    PRORES_OUTPUT_DIR,
    S3_BUCKET,
    UPLOAD_ENABLED,
    WEBHOOK_HOST,
    WEBHOOK_PORT,
    WEBHOOK_TOKEN,
)
from media_suite.dashboard import run_dashboard
from media_suite.encoders import PRORES_PROFILES
from media_suite.pipeline import run_batch, run_transcode, run_transcode_prores
from media_suite.probe import expand_playlist
from media_suite.upload import upload_configured
from media_suite.webhook import run_webhook_server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="m5-forensic-media-suite",
        description=(
            "Industry-grade YouTube → media pipeline: yt-dlp memory pipes, "
            "FFmpeg hardware encoders (VideoToolbox on Apple Silicon), "
            "HDR/5.1 preservation, subtitles, SHA-256 forensic manifest, "
            "S3/NAS upload, webhook queue API, and ProRes mastering."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    convert = sub.add_parser("convert", help="Convert a single URL")
    convert.add_argument("url", help="YouTube URL")
    convert.add_argument(
        "-f",
        "--format",
        default=DEFAULT_FORMAT,
        choices=["mp4", "mkv", "mp3", "wav", "m4a", "prores"],
        help="Output container/codec profile",
    )
    convert.add_argument("--no-subs", action="store_true", help="Skip subtitle embedding")
    convert.add_argument(
        "--profile",
        default=DEFAULT_PRORES_PROFILE,
        choices=list(PRORES_PROFILES),
        help="ProRes profile when -f prores",
    )
    convert.add_argument(
        "--normalize",
        action="store_true",
        help="Apply EBU R128 loudness normalization (-23 LUFS)",
    )
    convert.add_argument(
        "--no-classify",
        action="store_true",
        help="Disable music → audio-only folder routing",
    )
    convert.add_argument(
        "--no-upload",
        action="store_true",
        help="Skip S3/NAS upload even when configured",
    )

    prores = sub.add_parser("prores", help="ProRes mastering workflow (outputs .mov)")
    prores.add_argument("url", help="YouTube URL")
    prores.add_argument(
        "--profile",
        default=DEFAULT_PRORES_PROFILE,
        choices=list(PRORES_PROFILES),
        help="ProRes tier: lt, 422, hq, 4444",
    )
    prores.add_argument("--no-subs", action="store_true")
    prores.add_argument("--no-upload", action="store_true")

    batch = sub.add_parser("batch", help="Convert a playlist URL")
    batch.add_argument("url", help="Playlist or video URL")
    batch.add_argument("-f", "--format", default=DEFAULT_FORMAT)
    batch.add_argument("--profile", default=DEFAULT_PRORES_PROFILE, choices=list(PRORES_PROFILES))
    batch.add_argument("--no-subs", action="store_true")
    batch.add_argument("--no-upload", action="store_true")

    serve = sub.add_parser("serve", help="Start webhook API to queue links remotely")
    serve.add_argument("--host", default=WEBHOOK_HOST)
    serve.add_argument("--port", type=int, default=WEBHOOK_PORT)

    sub.add_parser("watch", help="Start curses dashboard + queue daemon")

    sub.add_parser("doctor", help="Verify tooling and upload/webhook config")

    return parser


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

    print(f"  macOS: {is_macos()} | Apple Silicon: {is_apple_silicon()}")
    print(f"  h264_videotoolbox: {videotoolbox_h264_available()}")
    print(f"  hevc_videotoolbox: {videotoolbox_hevc_available()}")
    print(f"  prores_videotoolbox: {prores_videotoolbox_available()}")
    print(f"  aac_at: {aac_at_available()}")
    print(f"  Output directory: {OUTPUT_DIR.resolve()}")
    print(f"  ProRes directory: {PRORES_OUTPUT_DIR.resolve()}")
    print(f"  Upload enabled: {UPLOAD_ENABLED} | configured: {upload_configured()}")
    if S3_BUCKET:
        print(f"  S3 bucket: {S3_BUCKET}")
    if NAS_DEST_PATH:
        print(f"  NAS path: {NAS_DEST_PATH}")
    print(f"  Webhook token set: {bool(WEBHOOK_TOKEN)}")
    print(f"  Webhook bind: {WEBHOOK_HOST}:{WEBHOOK_PORT}")
    return 0 if ok else 1


def _transcode_kwargs(args) -> dict:
    return {
        "embed_subtitles": not getattr(args, "no_subs", False),
        "prores_profile": getattr(args, "profile", DEFAULT_PRORES_PROFILE),
        "normalize_lufs": getattr(args, "normalize", False),
        "auto_classify": not getattr(args, "no_classify", False),
        "upload_after_verify": not getattr(args, "no_upload", False),
    }


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "doctor":
        return cmd_doctor()

    if args.command == "watch":
        run_dashboard()
        return 0

    if args.command == "serve":
        if not WEBHOOK_TOKEN:
            print(
                "Warning: MEDIA_SUITE_WEBHOOK_TOKEN is unset — all /queue requests will be rejected.",
                file=sys.stderr,
            )
        run_webhook_server(host=args.host, port=args.port, block=True)
        return 0

    kwargs = _transcode_kwargs(args)

    if args.command == "prores":
        result = run_transcode_prores(args.url, profile=args.profile, **kwargs)
        if not result.success:
            print(result.error, file=sys.stderr)
            return 1
        return 0

    if args.command == "convert":
        result = run_transcode(args.url, args.format, **kwargs)
        if not result.success:
            print(result.error, file=sys.stderr)
            return 1
        return 0

    if args.command == "batch":
        urls = expand_playlist(args.url)
        print(f"Resolved {len(urls)} track(s)")
        results = run_batch(urls, args.format, **kwargs)
        failed = sum(1 for r in results if not r.success)
        return 1 if failed else 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
