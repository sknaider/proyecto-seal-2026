"""SOUL Universal Ingestion Engine.

The package deliberately keeps acquisition, deterministic derivation and SOUL
memory promotion as separate operations. Importing it has no network or
database side effects.
"""

from .contracts import (
    CoverageMap,
    Derivation,
    IngestResult,
    MemoryCandidate,
    NormalizedDocument,
    SourceDescriptor,
)
from .engine import IngestionEngine

__all__ = [
    "CoverageMap",
    "Derivation",
    "IngestResult",
    "IngestionEngine",
    "MemoryCandidate",
    "NormalizedDocument",
    "SourceDescriptor",
]

__version__ = "0.1.0"
