# Media Engine — Forensic Construct Map

This document maps **how any-file → any-file conversion actually works** at the byte level, and which parts we **own** vs **delegate**.

## The honest boundary

| Layer | Who owns it | What it does |
|-------|-------------|--------------|
| **Domain model** | `media_engine/types.py` | `MediaCatalog`, `StreamDescriptor`, `ConversionPlan` |
| **Probe parsing** | `media_engine/probe.py` | ffprobe JSON → our catalog (not raw ffprobe) |
| **Planning** | `media_engine/planner.py` | remux vs transcode **decisions** with documented reasons |
| **Custody** | `media_engine/custody.py` | SHA-256 at each stage we control |
| **Command gen** | `media_engine/backend_ffmpeg.py` | Plan → ffmpeg argv |
| **Codec math** | FFmpeg (delegated) | DCT, entropy coding, mux timing |
| **Demux/mux** | FFmpeg (delegated) | Container parsing |

We do **not** pretend to replace libav. We **own the construct** that decides *what* happens to each stream and *proves* what we did.

---

## Forensic byte journey (input → output)

```
┌─────────────────────────────────────────────────────────────────────────┐
│ CONTAINER (mkv/mp4/mov/…)                                               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                   │
│  │ Video track  │  │ Audio track  │  │ Subtitle trk │  ← elementary     │
│  │ (H.264/DV/…) │  │ (AAC/FLAC/…) │  │ (SRT/ASS/…)  │    streams        │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘                   │
└─────────┼─────────────────┼─────────────────┼───────────────────────────┘
          │                 │                 │
          ▼                 ▼                 ▼
     STAGE 1: INGEST — hash entire file (SHA-256 of container bytes)
          │
          ▼
     STAGE 2: PROBE — demux metadata without decode
          │           ffprobe reads headers: codec_id, color_transfer,
          │           channel_layout, duration, index
          ▼
     MediaCatalog (OUR schema)
          │
          ▼
     STAGE 3: PLAN — per-stream decision
          │
          ├── BITSTREAM_COPY ──► packets pass through unchanged
          │                        (DV RPU, HDR SEI preserved)
          │
          ├── TRANSCODE ────────► decode frames/samples → encode new
          │                        (generation loss; HDR metadata must
          │                         be re-injected explicitly)
          │
          └── EXTRACT ──────────► audio-only output (-vn)
          │
          ▼
     ConversionPlan + reasons per stream
          │
          ▼
     STAGE 4: EXECUTE — backend runs argv we built
          │
          ▼
     STAGE 5: VERIFY — hash output file, write custody_trace.json
```

---

## What happens inside each mode

### BITSTREAM_COPY (remux)

- **No decode.** Compressed packets move from input container → output container.
- **Dolby Vision:** enhancement layer (RPU) stays intact — **only** way to "guarantee" DV.
- **HDR:** static metadata (SEI/PPS) often survives if container supports it.
- **Risk:** incompatible container → plan refuses or falls back to transcode.

### TRANSCODE

- **Decode** → raw YUV or PCM → **encode** new bitstream.
- **Always** generation loss for video (unless mathematically lossless codec like ffv1).
- **HDR:** must map `color_primaries`, `color_transfer`, `pix_fmt` explicitly (see `backend_ffmpeg._video_encode_args`).
- **5.1:** must map `channel_layout`; downmix is a choice, not preservation.

### EXTRACT

- Drop video/subtitle maps; encode audio to target format.

---

## Our pipeline stages (code)

```python
ConversionEngine.convert():
    1. INGEST      custody.record(SOURCE_FILE, hash)
    2. PROBE       catalog = probe_file() → MediaCatalog
    3. PLAN        plan = build_plan() → ConversionPlan
    4. EXECUTE     subprocess( build_ffmpeg_command(plan) )
    5. VERIFY      custody.record(OUTPUT_FILE, hash)
```

Each stage writes artifacts under `engine_output/custody/<stem>/`:
- `*_probe.json` — raw ffprobe
- `*_plan.json` — our decisions + reasons
- `*_ffmpeg.log` — stderr
- `custody_trace.json` — boundary hashes

---

## Planner decision tree (simplified)

```
output_format in {mp3,wav,m4a,flac,ogg}?
  YES → EXTRACT audio stream only

video.color_science == DOLBY_VISION?
  YES → require BITSTREAM_COPY to mkv/mp4/mov
        else FAIL (cannot guarantee DV)

require_bitstream_video OR (codec compatible with container)?
  YES → BITSTREAM_COPY video
  NO  → TRANSCODE video (libx264/libx265/vp9)

audio codec compatible?
  YES → copy
  NO  → transcode to aac/mp3/…

subtitles + embed_subtitles?
  YES → map each sub stream (copy or mov_text)
```

---

## Module map

```
media_engine/
  types.py           # Domain types — we own this schema
  probe.py           # ffprobe → MediaCatalog
  planner.py         # ConversionRequest + Catalog → ConversionPlan
  custody.py         # CustodyLedger boundary hashes
  backend_ffmpeg.py  # Plan → ffmpeg argv (thin backend)
  engine.py          # Orchestrator (5 stages)
  __main__.py        # CLI: probe-only, plan-only, convert
```

---

## CLI examples

```bash
# Inspect streams only (no conversion)
python -m media_engine input.mkv -f mp4 --probe-only

# See our plan without executing
python -m media_engine input.mkv -f mp4 --plan-only

# Full pipeline with custody
python -m media_engine input.mkv -f mp4 --case-id DEMO-001

# Force bitstream copy (fail if impossible)
python -m media_engine input.mkv -f mkv --copy-video

# Require surround (fail if stereo)
python -m media_engine surround.mkv -f mkv --require-surround
```

---

## What this is NOT

- Not a replacement for FFmpeg/libav codec implementations
- Not legal-grade e-discovery without external process + counsel
- Not distributed scale (single process; see `media_suite` for job queue)

## What this IS

- An **owned conversion construct**: catalog, plan, custody, execute
- Honest about **when** remux preserves bytes vs when transcode loses them
- Extensible: swap `backend_ffmpeg` for `backend_videotoolbox` or libav bindings later
