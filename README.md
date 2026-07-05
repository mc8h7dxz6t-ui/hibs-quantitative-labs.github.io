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

## Output formats

| Flag | Container | Notes |
|------|-----------|-------|
| `mp4` | H.264 + AAC | `+faststart`, HDR when detected |
| `mkv` | HEVC + AAC | Archive-oriented |
| `mp3` | MP3 VBR | 48 kHz |
| `wav` | PCM 16-bit | Broadcast sample rate |
| `m4a` | AAC | Audio-only |
| `prores` | ProRes 422 | macOS + `--prores` |

### Extra flags

- `--normalize` — EBU R128 loudness (`-23 LUFS`) for broadcast compliance
- `--no-subs` — skip subtitle fetch/embed
- `--no-classify` — disable music → `audio_masters/` routing

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
```

## Project layout

```
media_suite/
  cli.py           # argparse entry
  pipeline.py      # core transcode
  encoders.py      # platform codec matrices
  probe.py         # yt-dlp metadata + playlists
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
