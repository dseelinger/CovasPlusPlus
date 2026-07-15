"""The OPTIONAL embedding seam — off by default, mirroring the provider Protocol pattern.

Default recall is pure keyword/tag (see retrieval.py): free, offline, no dependency at import
time. Some queries are semantic ("what do I like to fly?" vs. a fact tagged "ship preference")
where embedding similarity would recall better — but embeddings cost money and/or a heavy local
model, and phone home, so they are strictly OPT-IN.

The seam is a tiny Protocol (one method). A real backend is built ONLY when config asks for it,
and its heavy import is lazy (inside build_embedder) so nothing here pulls a numpy/HTTP dependency
just by importing the memory package. No backend ships today; `build_embedder` therefore returns
None unless one is both configured and importable, and warns-then-falls-back otherwise. Tests
inject a deterministic fake embedder to exercise the similarity path without any network.
"""
from __future__ import annotations

import math
import sys
from typing import Protocol, Sequence, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Turn texts into fixed-length vectors. Kept to one method on purpose (CLAUDE.md: interfaces
    stay tiny). Batched so a backend can amortize an API round-trip over many facts at once."""

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one vector per input text, order-aligned. Vectors need not be normalized —
        `cosine` handles magnitude."""
        ...


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity in [-1, 1]; 0.0 for a zero/empty vector or a length mismatch (fail soft
    rather than raise, so a misbehaving backend degrades instead of crashing recall)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def build_embedder(cfg: dict) -> EmbeddingProvider | None:
    """Return an embedding backend ONLY if config opts in AND it can be built; else None.

    Contract: `[memory.embedding].enabled = true` plus a `provider` name. No backend ships yet, so
    this currently always warns and returns None (keyword recall remains the fallback). It is the
    single place a future backend gets wired — keep the heavy import LOCAL to this function so the
    default (disabled) path never imports it. This is the composition root; tests bypass it and
    inject a fake embedder straight into Retriever."""
    emb = cfg.get("memory", {}).get("embedding", {})
    if not emb.get("enabled"):
        return None
    provider = str(emb.get("provider", "")).strip()
    if not provider:
        _warn("embedding enabled but no [memory.embedding].provider set; using keyword recall")
        return None
    # No embedding backend ships in this foundation issue. When one lands, import it lazily HERE,
    # e.g.:  if provider == "openai": from covas.memory.backends.openai import OpenAIEmbedder ...
    _warn(f"embedding provider '{provider}' is not available yet; using keyword recall")
    return None


def _warn(msg: str) -> None:
    print(f"!! [memory] {msg}", file=sys.stderr, flush=True)
