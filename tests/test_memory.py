"""Unit tests for the memory foundation (issue #59) — store I/O + recall.

All offline and hermetic: the store is pointed at a tmp file (no data dir, no config), and the
embedding path is exercised with a deterministic in-test fake — nothing here touches the network,
so this runs in the default `pytest`. See DESIGN §9.
"""
from __future__ import annotations

import json

import pytest

from covas.memory import (
    MemoryRecord,
    MemoryStore,
    Retriever,
    keyword_score,
    store_from_config,
)
from covas.memory.embedding import build_embedder, cosine


# --- MemoryRecord ------------------------------------------------------------

def test_record_autofills_id_and_when_and_normalizes_tags():
    r = MemoryRecord(text="likes the Krait", tags=["Ship", " favorite "])
    assert r.id  # uuid filled
    assert r.when  # timestamp filled
    assert r.tags == ("ship", "favorite")  # lower-cased, stripped


def test_record_roundtrips_through_dict():
    r = MemoryRecord(text="CMDR name is Jameson", type="fact", tags=["name"])
    r2 = MemoryRecord.from_dict(r.to_dict())
    assert (r2.text, r2.type, r2.tags, r2.id) == (r.text, r.type, r.tags, r.id)


def test_from_dict_requires_text():
    with pytest.raises(ValueError):
        MemoryRecord.from_dict({"tags": ["x"]})


def test_from_dict_lone_string_tag_becomes_single_tag():
    r = MemoryRecord.from_dict({"text": "hi", "tags": "greeting"})
    assert r.tags == ("greeting",)


# --- MemoryStore write/read --------------------------------------------------

def test_add_then_load_roundtrip(tmp_path):
    path = tmp_path / "memory.jsonl"
    store = MemoryStore(path)
    store.remember("prefers metric units", type="preference", tags=["units"])
    store.remember("main ship is a Krait Mk II", type="fact", tags=["ship"])

    # a fresh store reading the same file sees both facts
    reread = MemoryStore(path).load()
    texts = {r.text for r in reread}
    assert texts == {"prefers metric units", "main ship is a Krait Mk II"}


def test_add_appends_one_line_per_fact(tmp_path):
    path = tmp_path / "memory.jsonl"
    store = MemoryStore(path)
    store.remember("a")
    store.remember("b")
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 2
    assert all(json.loads(l)["text"] in {"a", "b"} for l in lines)


def test_missing_file_is_empty_memory(tmp_path):
    assert MemoryStore(tmp_path / "nope.jsonl").load() == []


def test_save_rewrites_whole_file(tmp_path):
    path = tmp_path / "memory.jsonl"
    store = MemoryStore(path)
    store.remember("keep me")
    store.remember("drop me")
    kept = [r for r in store.all() if r.text == "keep me"]
    store.save(kept)
    reread = MemoryStore(path).load()
    assert [r.text for r in reread] == ["keep me"]


# --- issue #164: concurrent add()/save() must not drop a fact ----------------

def test_concurrent_add_and_save_drops_no_fact(tmp_path):
    # The voice loop's add() (append) and the web thread's save() (full rewrite) both touch the shared
    # store; without the RMW lock a save racing an add could clobber the just-added line. Hammer both
    # concurrently and assert every added fact survives (and the file never corrupts under iteration).
    import threading

    path = tmp_path / "memory.jsonl"
    store = MemoryStore(path)
    store.load()

    n_adds = 200
    added_ids: list[str] = []
    start = threading.Barrier(2)

    def _adder() -> None:
        start.wait()
        for i in range(n_adds):
            rec = store.remember(f"fact number {i}", tags=["bulk"])
            added_ids.append(rec.id)

    def _saver() -> None:
        start.wait()
        for _ in range(n_adds):
            store.save()          # full rewrite from the current in-memory records

    threads = [threading.Thread(target=_adder), threading.Thread(target=_saver)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Rewrite once more so the on-disk file reflects the final in-memory set, then reload fresh.
    store.save()
    reread_ids = {r.id for r in MemoryStore(path).load()}
    assert len(added_ids) == n_adds
    assert set(added_ids) <= reread_ids          # no added fact was lost to a racing save


# --- fail-soft on corrupt input ---------------------------------------------

def test_corrupt_line_is_skipped_not_fatal(tmp_path, capsys):
    path = tmp_path / "memory.jsonl"
    good = json.dumps({"text": "valid fact", "tags": ["ok"]})
    path.write_text(
        "\n".join([
            good,
            "{ this is not json",     # malformed JSON -> skipped
            json.dumps({"tags": ["no-text"]}),  # valid JSON but no text -> skipped
            "# a hand-written comment line",     # comment -> ignored
            "",                                   # blank -> ignored
        ]),
        encoding="utf-8",
    )
    records = MemoryStore(path).load()
    assert [r.text for r in records] == ["valid fact"]
    # a warning was surfaced (fail soft, not silent) but nothing raised
    assert "[memory]" in capsys.readouterr().err


# --- keyword recall (the default, offline path) ------------------------------

def _seed(tmp_path) -> MemoryStore:
    store = MemoryStore(tmp_path / "memory.jsonl")
    store.remember("Commander's name is Jameson", type="fact", tags=["name"])
    store.remember("prefers the Krait Mk II for combat", type="preference", tags=["ship"])
    store.remember("dislikes long supercruise trips", type="preference", tags=["travel"])
    return store


def test_keyword_score_tag_beats_body():
    tagged = MemoryRecord(text="likes it", tags=["ship"])
    bodied = MemoryRecord(text="ship talk here", tags=[])
    assert keyword_score("ship", tagged) > keyword_score("ship", bodied) > 0


def test_recall_finds_by_body_word(tmp_path):
    r = Retriever(_seed(tmp_path))
    hits = r.recall("what is my name")
    assert hits and hits[0].text == "Commander's name is Jameson"


def test_recall_tag_filter_restricts_candidates(tmp_path):
    r = Retriever(_seed(tmp_path))
    hits = r.recall("Krait", tags=["ship"])
    assert len(hits) == 1 and hits[0].tags == ("ship",)


def test_recall_empty_query_returns_recent(tmp_path):
    r = Retriever(_seed(tmp_path))
    hits = r.recall("", limit=2)
    assert len(hits) == 2  # most-recent facts, no scoring


def test_recall_limit_and_no_match(tmp_path):
    r = Retriever(_seed(tmp_path))
    assert r.recall("xyzzy nonexistent") == []
    assert len(r.recall("prefers", limit=1)) == 1


# --- embedding seam (optional, off by default) -------------------------------

class _FakeEmbedder:
    """Deterministic bag-of-words vectors over a fixed vocabulary — no network, for tests only.
    Two texts sharing words get a high cosine; this lets us assert the similarity path runs."""

    _VOCAB = ["name", "jameson", "krait", "ship", "combat", "travel", "supercruise", "fuel"]

    def embed(self, texts):
        out = []
        for t in texts:
            toks = t.lower()
            out.append([1.0 if w in toks else 0.0 for w in self._VOCAB])
        return out


def test_build_embedder_disabled_by_default():
    assert build_embedder({}) is None
    assert build_embedder({"memory": {"embedding": {"enabled": False}}}) is None


def test_build_embedder_enabled_without_backend_falls_back_to_none(capsys):
    cfg = {"memory": {"embedding": {"enabled": True, "provider": "openai"}}}
    assert build_embedder(cfg) is None  # no backend ships yet -> keyword fallback
    assert "[memory]" in capsys.readouterr().err


def test_recall_with_injected_embedder_uses_similarity(tmp_path):
    store = _seed(tmp_path)
    r = Retriever(store, embedder=_FakeEmbedder())
    # "combat vessel" shares no literal word with the stored text besides via the embedder's
    # vocabulary ("combat"), so a keyword-only recall would miss but the embedder surfaces it.
    hits = r.recall("combat", limit=1)
    assert hits and "Krait" in hits[0].text


def test_embedder_failure_falls_back_to_keyword(tmp_path):
    class _Boom:
        def embed(self, texts):
            raise RuntimeError("backend down")

    r = Retriever(_seed(tmp_path), embedder=_Boom())
    hits = r.recall("name")  # must still work via keyword fallback
    assert hits and hits[0].text == "Commander's name is Jameson"


def test_cosine_basic():
    assert cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert cosine([], [1]) == 0.0  # fail soft on mismatch


# --- store_from_config -------------------------------------------------------

def test_store_from_config_uses_configured_dir(tmp_path):
    store = store_from_config({"memory": {"dir": str(tmp_path / "mem")}})
    assert store.path == tmp_path / "mem" / "memory.jsonl"
    store.remember("hello")
    assert store.path.exists()
