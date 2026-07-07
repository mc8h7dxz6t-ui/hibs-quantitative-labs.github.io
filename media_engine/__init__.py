"""
Media Engine — owned conversion construct.

We own: probe catalog, remux/transcode planning, pipeline graph, custody boundaries.
We delegate: codec math (FFmpeg backend today; libav/hardware backends later).
"""

__version__ = "0.1.0"

from media_engine.engine import ConversionEngine, EngineResult
from media_engine.types import ConversionRequest, MediaCatalog

__all__ = ["ConversionEngine", "EngineResult", "ConversionRequest", "MediaCatalog"]
