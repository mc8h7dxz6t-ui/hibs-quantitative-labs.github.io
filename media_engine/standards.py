"""Industry-standard processing flags we own and document."""

from __future__ import annotations

# EBU R128 / ITU-R BS.1770 loudness normalization (broadcast)
EBU_R128_FILTER = "loudnorm=I=-23:TP=-1.5:LRA=11"

# ISO BMFF (MP4/MOV) — move moov atom for streaming (ISO/IEC 14496-12)
ISOBMFF_FASTSTART = "+faststart"

# Preserve container/global metadata (timecode, tags) where ffmpeg supports
MAP_METADATA = "0"
MAP_CHAPTERS = "0"

# Remux timestamp continuity (SMPTE workflows)
COPYTIMESTAMPS = True

# SWGDE still references MD5 for legacy integrity verification alongside SHA-256
ENABLE_MD5_DIGEST = True

# FFV1 lossless archival (Matroska ecosystem / archival communities)
FFV1_LOSSLESS = "ffv1"

# ProRes profile map (Apple / broadcast post)
PRORES_PROFILES = {"lt": "1", "422": "2", "hq": "3", "4444": "4"}
