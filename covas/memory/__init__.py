"""Persistent memory foundation (issue #59).

A TRANSPARENT store of small facts about the Commander plus a cheap, offline recall API.
The store is a human-readable JSON-lines file under the user's writable data dir — the user
can open it, read it, edit a line, or delete a fact in any text editor. It is git-ignored and
private (see `.gitignore` / config `[memory].dir`). This is deliberately the opposite of the
opaque, vendor-locked memory in EDCoPilot / COVAS:NEXT.

Recall is keyword/tag matching by DEFAULT — pure standard library, no network, no API, no cost.
An OPTIONAL embedding-backed similarity seam exists (a tiny Protocol, mirroring providers/base.py)
but is OFF unless a backend is configured and injected, so the default path stays free and the
bare `pytest` run stays hermetic.

This module is the store + retrieval foundation plus the recall detector (#61). The capture sink
(#60) and recall injection into the live voice loop live in `capabilities/memory_capability.py`
and `app.py`; the `MemoryDetector` here is pure policy (which turns reference the past), mirroring
the ED `ContextDetector`.
"""
from __future__ import annotations

from covas.memory.capture import MemoryCapture, describe_highlight
from covas.memory.detector import MemoryDetector, MemoryDetectorConfig, MemoryRef
from covas.memory.embedding import EmbeddingProvider, build_embedder, cosine
from covas.memory.retrieval import Retriever, keyword_score
from covas.memory.store import MemoryRecord, MemoryStore, store_from_config

__all__ = [
    "MemoryRecord",
    "MemoryStore",
    "store_from_config",
    "Retriever",
    "keyword_score",
    "MemoryCapture",
    "describe_highlight",
    "MemoryDetector",
    "MemoryDetectorConfig",
    "MemoryRef",
    "EmbeddingProvider",
    "build_embedder",
    "cosine",
]
