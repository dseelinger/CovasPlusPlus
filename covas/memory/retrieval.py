"""Recall: find the facts most relevant to a query. Keyword/tag by DEFAULT (cheap, offline).

The default scorer is bag-of-words token overlap with a tag bonus — no dependency, no network,
deterministic, and good enough to surface "what's my name?" against a fact tagged `name`. An
OPTIONAL embedder (injected, see embedding.py) adds semantic similarity for queries that don't
share literal words with the stored fact; when absent (the default) recall is purely keyword, so
the bare test run and the shipped default stay free.

Injection keeps this hermetic: `Retriever(store, embedder=None)`. Tests pass a tmp-backed store
and either no embedder (keyword path) or a deterministic fake embedder (similarity path).
"""
from __future__ import annotations

import re

from covas.memory.embedding import EmbeddingProvider, cosine
from covas.memory.store import MemoryRecord, MemoryStore

# Word tokens, lower-cased. Kept dead simple on purpose — a heavier tokenizer/stemmer would be a
# dependency for marginal gain at this scale (a personal memory is dozens, not millions, of facts).
_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def keyword_score(query: str, record: MemoryRecord) -> float:
    """Relevance of one fact to a query from token overlap. A query token that matches one of the
    fact's TAGS is worth more than a body-text match (tags are curated recall keys), and matching
    more distinct query terms scores higher. 0.0 means no overlap. Pure, deterministic, offline."""
    q = set(_tokens(query))
    if not q:
        return 0.0
    body = set(_tokens(record.text))
    tags = set(record.tags)
    score = 0.0
    for tok in q:
        if tok in tags:
            score += 2.0          # a tag hit is a strong, curated signal
        elif tok in body:
            score += 1.0
    return score / len(q)         # normalize by query length -> comparable across queries


class Retriever:
    """Recall over a MemoryStore. Keyword/tag by default; if an embedder is injected, blend in
    cosine similarity so semantically-related facts surface even without shared words."""

    def __init__(self, store: MemoryStore, embedder: EmbeddingProvider | None = None) -> None:
        self._store = store
        self._embedder = embedder  # None -> keyword only (the default, free, offline path)

    def recall(self, query: str, *, tags: list[str] | None = None,
               limit: int = 5, min_score: float = 0.0) -> list[MemoryRecord]:
        """Return up to `limit` records most relevant to `query`, best first.

        `tags` is an optional hard filter (only facts carrying at least one of these tags are
        considered) — cheap structured recall independent of the text score. `min_score` drops
        weak matches; the default 0.0 keeps anything with a positive score (any keyword overlap,
        or with an embedder any positive similarity) and drops the rest."""
        records = self._store.all()
        if tags:
            want = {t.strip().lower() for t in tags if t.strip()}
            records = [r for r in records if want & set(r.tags)]
        if not records:
            return []
        if not query.strip():
            # No query: return the most recent facts (optionally tag-filtered). `when` is ISO-8601
            # so a lexical sort is chronological.
            return sorted(records, key=lambda r: r.when, reverse=True)[:limit]

        scored = (self._embedding_scores(query, records) if self._embedder
                  else [(keyword_score(query, r), r) for r in records])
        # Stable, deterministic order: score desc, then newest first as the tie-break.
        scored.sort(key=lambda sr: (sr[0], sr[1].when), reverse=True)
        return [r for score, r in scored if score > min_score][:limit]

    def _embedding_scores(self, query: str, records: list[MemoryRecord]) -> list[tuple[float, MemoryRecord]]:
        """Cosine similarity of query vs. each fact. Fail soft: any embedder error falls back to
        the keyword scorer for the whole batch, so a flaky backend never breaks recall."""
        try:
            vectors = self._embedder.embed([query] + [r.text for r in records])
            qv, rvs = vectors[0], vectors[1:]
            return [(cosine(qv, rv), r) for rv, r in zip(rvs, records)]
        except Exception as e:  # noqa: BLE001 — a dead embedder must degrade, not crash recall
            print(f"!! [memory] embedder failed ({e}); falling back to keyword recall", flush=True)
            return [(keyword_score(query, r), r) for r in records]
