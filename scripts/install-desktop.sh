#!/usr/bin/env bash
# Install HIBS Media Studio desktop on macOS or Linux
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> Checking ffmpeg / ffprobe / yt-dlp"
for bin in ffmpeg ffprobe yt-dlp; do
  if ! command -v "$bin" >/dev/null 2>&1; then
    echo "Missing $bin — install before using the studio."
    exit 1
  fi
done

echo "==> Installing hibs-media-studio with desktop extras"
pip install -e ".[desktop]"

echo ""
echo "Launch:"
echo "  hibs-media-studio"
echo "  python3 m5_forensic_media_suite.py desktop"
echo ""
echo "API-only (headless / browser UI):"
echo "  python3 -m desktop --serve-only --port 9876 --browser"
