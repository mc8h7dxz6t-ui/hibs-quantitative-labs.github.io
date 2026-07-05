# Forensic Media Suite

Industry-grade **YouTube → any format** pipeline for macOS Apple Silicon (M-series) and cross-platform hosts. Built on the two gold-standard tools:

- **[FFmpeg](https://ffmpeg.org/)** — transcoding, muxing, broadcast filters
- **[yt-dlp](https://github.com/yt-dlp/yt-dlp)** — stream extraction and metadata probing

No heavy Python wrapper layers. The suite drives native CLI binaries via **zero-copy memory pipes** (`yt-dlp` stdout → `ffmpeg` stdin), selects **hardware encoders** when available, and writes a **SHA-256 forensic manifest** for every output.

## Technical edge

| Capability | Standard scripts | This suite |
|------------|------------------|------------|
| Compute path | CPU `libx264` | `h264_videotoolbox` / `hevc_videotoolbox` on Apple Silicon |
| Disk I/O | Temp download + re-read | Single-pass RAM pipe; one final write |
| HDR | Clamped to 8-bit SDR | `p010le` + Rec.2020 when source is HDR |
| Audio | Stereo downmix | Up to 5.1 via `aac_at` (macOS) or `aac` fallback |
| Integrity | None | SHA-256 manifest + signed log |
| Operations | Manual one-offs | CLI, batch playlists, watch-folder daemon + dashboard |
| Remote queue | None | Webhook API (`serve`) — queue from phone |
| Backup | Manual copy | Auto S3 / NAS / rsync after SHA-256 verify |
| Mastering | Generic MP4 | First-class ProRes workflow (`prores` command) |

## Prerequisites

### macOS (M-series recommended)

```bash
brew install ffmpeg
pip install -r requirements.txt
```

FFmpeg builds from Homebrew expose **VideoToolbox** (`h264_videotoolbox`, `hevc_videotoolbox`) and **AudioToolbox** (`aac_at`) encoders on Apple Silicon.

### Linux / CI

```bash
sudo apt-get install -y ffmpeg   # or your distro equivalent
pip install -r requirements.txt
```

Hardware encoders fall back to `libx264` / `libx265` / `aac` automatically.

## Quick start

Verify tooling:

```bash
python m5_forensic_media_suite.py doctor
```

Convert a single URL:

```bash
python m5_forensic_media_suite.py convert "https://www.youtube.com/watch?v=VIDEO_ID" -f mp4
```

Batch a playlist:

```bash
python m5_forensic_media_suite.py batch "https://www.youtube.com/playlist?list=PLAYLIST_ID" -f mp3
```

Watch-folder daemon with terminal dashboard:

```bash
python m5_forensic_media_suite.py watch
```

Add links to `download_queue.txt` (optionally `URL | format` per line). Outputs land in `forensic_outputs/` with a rolling `forensic_manifest.log`.

### ProRes mastering (first-class)

```bash
python m5_forensic_media_suite.py prores "YOUTUBE_URL" --profile hq
# Profiles: lt | 422 | hq | 4444
# Output: forensic_outputs/prores_masters/<title>.mov
```

Queue a ProRes job: `https://youtube.com/... | prores:hq`

### Remote webhook API (queue from your phone)

Terminal 1 — process the queue:

```bash
python m5_forensic_media_suite.py watch
```

Terminal 2 — expose the webhook (set a secret token first):

```bash
export MEDIA_SUITE_WEBHOOK_TOKEN="your-long-random-secret"
python m5_forensic_media_suite.py serve --port 8765
```

From your phone (same LAN or tunneled host):

```bash
curl -X POST http://YOUR_MAC_IP:8765/queue \
  -H "Authorization: Bearer your-long-random-secret" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.youtube.com/watch?v=VIDEO_ID","format":"mp4"}'
```

ProRes via webhook:

```json
{"url": "https://www.youtube.com/watch?v=...", "format": "prores", "prores_profile": "hq"}
```

Health check: `GET /health`

### S3 / NAS auto-upload (after verification)

Upload runs only **after** SHA-256 signing succeeds.

```bash
export MEDIA_SUITE_UPLOAD_ENABLED=true

# AWS S3 (or MinIO-compatible)
export MEDIA_SUITE_S3_BUCKET=my-media-archive
export MEDIA_SUITE_S3_PREFIX=forensic/
export MEDIA_SUITE_S3_REGION=eu-west-2
# Optional: MEDIA_SUITE_S3_ENDPOINT_URL=https://minio.example.com

# NAS mount (NFS/SMB path on your Mac)
export MEDIA_SUITE_NAS_PATH=/Volumes/NAS/forensic_inbox

# Optional rsync push
export MEDIA_SUITE_RSYNC_TARGET=user@nas.local:/volume1/forensic/
```

Destinations are logged in `forensic_manifest.log` under `UPLOAD=`.

## Output formats

| Flag | Container | Notes |
|------|-----------|-------|
| `mp4` | H.264 + AAC | `+faststart`, HDR when detected |
| `mkv` | HEVC + AAC | Archive-oriented |
| `mp3` | MP3 VBR | 48 kHz |
| `wav` | PCM 16-bit | Broadcast sample rate |
| `m4a` | AAC | Audio-only |
| `prores` | ProRes `.mov` | `prores` command or `-f prores --profile hq` |

### Extra flags

- `--normalize` — EBU R128 loudness (`-23 LUFS`) for broadcast compliance
- `--no-subs` — skip subtitle fetch/embed
- `--no-classify` — disable music → `audio_masters/` routing
- `--no-upload` — skip remote upload even when configured

## Architecture

```
YouTube CDN
    │  HTTPS
    ▼
 yt-dlp  (-o -)  ──stdout pipe──►  ffmpeg  (-i pipe:0)
                                      │
                    ┌─────────────────┼─────────────────┐
                    ▼                 ▼                 ▼
            VideoToolbox ASIC   AudioToolbox AAC   mov_text/srt
                    │                 │                 │
                    └────────► forensic_outputs/ ◄──────┘
                                      │
                               SHA-256 manifest
                                      │
                         S3 / NAS / rsync upload
```

## Environment variables

| Variable | Purpose |
|----------|---------|
| `MEDIA_SUITE_WEBHOOK_TOKEN` | Bearer token for `/queue` API |
| `MEDIA_SUITE_WEBHOOK_PORT` | Webhook port (default `8765`) |
| `MEDIA_SUITE_UPLOAD_ENABLED` | `true` to enable post-verify upload |
| `MEDIA_SUITE_S3_BUCKET` | S3 bucket name |
| `MEDIA_SUITE_NAS_PATH` | Mounted NAS directory |
| `MEDIA_SUITE_RSYNC_TARGET` | `user@host:/path` rsync target |
| `MEDIA_SUITE_PRORES_PROFILE` | Default ProRes tier (`hq`) |

## Project layout

```
media_suite/
  cli.py           # argparse entry
  pipeline.py      # core transcode + ProRes workflow
  encoders.py      # platform codec matrices
  probe.py         # yt-dlp metadata + playlists
  queue.py         # thread-safe queue file
  webhook.py       # remote queue HTTP API
  upload.py        # S3 / NAS / rsync after verify
  daemon.py        # queue watcher
  dashboard.py     # curses UI
  integrity.py     # SHA-256 + manifest
  telemetry.py     # live FPS / speed
  notifications.py # macOS / Linux alerts
m5_forensic_media_suite.py
download_queue.txt
requirements.txt
```

## License

MIT — see [LICENSE](LICENSE).
