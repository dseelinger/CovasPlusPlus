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

This module is the store + retrieval foundation only. Wiring recall into the live voice loop and
exposing a memory capability/tool to the LLM is a later issue (#61); nothing here edits `app.py`.
"""
from __future__ import annotations

from covas.memory.embedding import EmbeddingProvider, build_embedder, cosine
from covas.memory.retrieval import Retriever, keyword_score
from covas.memory.store import MemoryRecord, MemoryStore, store_from_config

__all__ = [
    "MemoryRecord",
    "MemoryStore",
    "store_from_config",
    "Retriever",
    "keyword_score",
    "EmbeddingProvider",
    "build_embedder",
    "cosine",
]
