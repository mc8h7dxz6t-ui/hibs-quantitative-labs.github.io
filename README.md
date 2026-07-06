# Forensic Media Suite v2

Production **any file → any format** media farm with legal chain-of-custody, strict stream preservation policies, SQLite job queue, and internet-facing FastAPI.

## Architecture (v2)

```
                    ┌─────────────┐
  Phone / API ─────►│ FastAPI     │──► SQLite jobs.db
                    │ + rate limit│
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │ Worker farm │  (24/7, concurrent)
                    └──────┬──────┘
                           │
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                 ▼
   ffprobe/yt-dlp      FFmpeg encode    Evidence bundle
   source SHA-256      output SHA-256    custody_ledger.jsonl
```

## Deploy on M5 Mac (production)

```bash
brew install ffmpeg
pip install -r requirements.txt
cp deploy/com.hibs.forensic-media-*.plist ~/Library/LaunchAgents/
# Edit plists: paths, MEDIA_SUITE_API_TOKEN, MEDIA_SUITE_FORENSIC_HMAC_KEY

export MEDIA_SUITE_API_TOKEN="$(openssl rand -hex 32)"
export MEDIA_SUITE_FORENSIC_HMAC_KEY="$(openssl rand -hex 32)"
export MEDIA_SUITE_FORENSIC_MODE=true
export MEDIA_SUITE_PRESERVE_SOURCE=true

python3 m5_forensic_media_suite.py doctor
```

**Three processes:**

| Process | Command | Role |
|---------|---------|------|
| API | `python3 m5_forensic_media_suite.py api` | Internet-facing job submission |
| Worker | `python3 m5_forensic_media_suite.py worker --concurrency 2` | 24/7 transcode farm |
| TLS edge | Caddy/nginx (`deploy/nginx-api.conf`) | HTTPS termination |

## 1. Advanced any-file converter

- **Local:** FFmpeg direct read — `mkv`, `mp4`, `mov`, `wav`, `flac`, etc.
- **Remote:** yt-dlp → FFmpeg pipe (YouTube + 1000+ sites)
- **Folder batch:** `batch /path/inbox/ -f mp3`
- **Remux-aware:** Dolby Vision strict mode uses `-c:v copy` (bitstream preservation)

```bash
python3 m5_forensic_media_suite.py convert "/path/movie.mkv" -f mp4
python3 m5_forensic_media_suite.py batch "/path/inbox/" -f mp3
```

## 2. Internet-facing webhook / API

FastAPI with bearer auth, rate limiting, CORS, job status:

```bash
export MEDIA_SUITE_API_TOKEN="your-secret"
python3 m5_forensic_media_suite.py api --host 127.0.0.1 --port 8765
```

```bash
# Submit job
curl -X POST https://media.example.com/v1/jobs \
  -H "Authorization: Bearer $MEDIA_SUITE_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"input":"/path/file.mkv","format":"mp4","forensic_mode":true}'

# Poll status
curl -H "Authorization: Bearer $MEDIA_SUITE_API_TOKEN" \
  https://media.example.com/v1/jobs/JOB_ID
```

Put **nginx/Caddy** in front for TLS (`deploy/nginx-api.conf`). Never expose port 8765 directly to the internet.

## 3. Forensic / legal evidence workflows

When `--forensic` or `forensic_mode: true`:

| Artifact | Location |
|----------|----------|
| Source SHA-256 | Local files hashed before transcode; remote archived when `preserve_source` |
| Output SHA-256 | After encode |
| Chain-of-custody JSON | `forensic_outputs/evidence_bundles/<job_id>/chain_of_custody.json` |
| Append-only ledger | `forensic_outputs/evidence_bundles/custody_ledger.jsonl` |
| HMAC signature | Set `MEDIA_SUITE_FORENSIC_HMAC_KEY` for tamper-evident manifests |
| Tool versions | ffmpeg, ffprobe, yt-dlp, Python recorded in bundle |

```bash
python3 m5_forensic_media_suite.py convert input.mkv -f mp4 \
  --forensic --preserve-source --case-id CASE-2026-001
```

**Honest limit:** This records hashes and metadata per SWGDE-style integrity practice. It is not a certified legal platform — consult counsel for admissibility requirements.

## 4. Unattended 24/7 farm processing

SQLite-backed queue with retries and dead-letter:

```bash
python3 m5_forensic_media_suite.py worker --concurrency 2
```

| Job state | Meaning |
|-----------|---------|
| `pending` | Queued |
| `running` | Worker claimed |
| `completed` | Success + result JSON |
| `dead` | Failed after max retries |

Env: `MEDIA_SUITE_WORKER_CONCURRENCY`, `MEDIA_SUITE_JOB_MAX_RETRIES`, `MEDIA_SUITE_JOBS_DB`

## 5. HDR / Dolby Vision / 5.1 preservation

**Strict modes fail loudly** if the source cannot meet requirements:

| Flag | Behavior |
|------|----------|
| `--strict-hdr` | Requires HDR10/HLG signaling; encodes with `p010le` + color metadata |
| `--strict-dv` | Requires Dolby Vision; uses **bitstream copy** (`-c:v copy`) to guarantee DV preservation |
| `--strict-surround` | Requires ≥6 channels; fails on stereo sources |

```bash
python3 m5_forensic_media_suite.py convert input.mkv -f mkv --strict-dv --forensic
```

**Honest limit:** Dolby Vision *guarantee* only applies in strict DV + copy mode to `mkv`/`mp4`/`mov`. Transcoding DV to SDR/HDR10 destroys the DV layer — the suite will refuse rather than silently degrade.

## Environment variables

| Variable | Purpose |
|----------|---------|
| `MEDIA_SUITE_API_TOKEN` | Bearer token (required for API) |
| `MEDIA_SUITE_FORENSIC_HMAC_KEY` | HMAC-SHA256 manifest signing |
| `MEDIA_SUITE_FORENSIC_MODE` | Default forensic bundles on |
| `MEDIA_SUITE_PRESERVE_SOURCE` | Archive remote source before transcode |
| `MEDIA_SUITE_STRICT_HDR/DOLBY_VISION/SURROUND` | Global strict preservation defaults |
| `MEDIA_SUITE_WORKER_CONCURRENCY` | Parallel farm workers |

## Output formats

`mp4` `mkv` `mov` `webm` `mp3` `wav` `m4a` `flac` `ogg` `prores`

## License

MIT
