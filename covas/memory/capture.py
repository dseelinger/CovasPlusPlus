"""Automatic memory CAPTURE (issue #60): populate the store WITHOUT the Commander asking.

Two cheap, cost-aware sources feed the persistent memory foundation (#59):

  * Journal HIGHLIGHTS — curated, DETERMINISTIC describers (`describe_highlight`) turn a
    handful of genuinely notable journal events (first discoveries, deaths, rank-ups, big
    payouts, a new ship/carrier) into a durable one-line memory. This mirrors the recent-
    events feed's curated-describer style (`ed/journal.describe_journal_event`) but writes a
    PERMANENT milestone rather than a rolling "what just happened" line. NO LLM per event.
  * Conversation FACTS — the LLM calls a `remember_this` tool DURING a turn it's already
    producing (see `capabilities/memory_capability.py`); `MemoryCapture.remember` is the sink.
    Because it rides the existing turn's tool-use, there is NO extra model call and no extra
    cost — the "piggyback" the issue asks for.

Everything here is pure-Python and offline: describers are table lookups, dedup is the
foundation's `keyword_score` (no embedding — that stays OFF, it costs money), and the cap is a
list trim. Fail-soft is the store's job (it never raises on I/O); this module adds the policy.

Recall (injecting memories into a turn, a "what do you remember about…" tool) is deliberately
NOT here — that's issue #61, which extends the capability. This half is capture/store only.
"""
from __future__ import annotations

import re
from typing import Callable, Optional

from .retrieval import keyword_score
from .store import MemoryRecord, MemoryStore

# Auto-captured journal milestones carry this type so the cap policy can tell them apart from
# facts the Commander explicitly asked to keep (preference/fact/note) — milestones are
# reproducible from the journal and evicted first when the store is full.
HIGHLIGHT_TYPE = "milestone"

# Credit floor for "notable" money events (big exploration payout, lucrative mission, large
# voucher redemption). Below this it's routine income and not worth a permanent memory. Tuned
# high on purpose — a milestone log should hold the standout paydays, not every sale.
NOTABLE_CREDITS = 10_000_000

# A new candidate counts as a duplicate of an existing memory when its keyword_score against
# that memory reaches this (near-1.0 == almost every candidate token already present), OR when
# its normalized text matches exactly. Keeps "First to discover X" and "…Y" distinct while
# collapsing verbatim repeats (e.g. the same milestone seen again across sessions).
DEDUP_THRESHOLD = 0.9

# Default upper bound on stored records so an always-on capture can't grow the file without
# limit. Overridable via [memory].cap.
DEFAULT_CAP = 500

_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    """Normalized form for exact-duplicate comparison: lower-cased, whitespace-collapsed,
    trailing punctuation stripped. Deliberately light — semantic near-dups are the
    keyword_score path's job, this only catches verbatim repeats."""
    return _WS.sub(" ", text.strip().lower()).strip(" .!,;:")


def _credits(event: dict, *keys: str) -> Optional[int]:
    """First present numeric credit field among `keys`, as an int, or None."""
    for k in keys:
        v = event.get(k)
        if isinstance(v, (int, float)):
            return int(v)
    return None


# --- curated journal-highlight describers ---------------------------------------------
# Each describer takes the raw journal event and returns (text, tags) for a durable memory,
# or None when this particular event isn't milestone-worthy (e.g. a payout below the floor).
# type is always HIGHLIGHT_TYPE. Deliberately a SMALL, high-signal set — quality over volume,
# so the milestone log stays worth reading.

def _died(e: dict) -> Optional[tuple[str, tuple[str, ...]]]:
    # Died carries no system; the killer (if any) differentiates otherwise-identical deaths.
    killer = e.get("KillerName_Localised") or e.get("KillerName")
    if not killer and isinstance(e.get("Killers"), list) and e["Killers"]:
        killer = e["Killers"][0].get("Name")
    text = f"Died — killed by {killer}" if killer else "Died"
    return text, ("death",)


def _promotion(e: dict) -> Optional[tuple[str, tuple[str, ...]]]:
    # A Promotion event sets one (occasionally more) rank field to the NEW numeric rank.
    ranks = ("Combat", "Trade", "Explore", "Exobiologist", "Soldier",
             "CQC", "Empire", "Federation")
    got = [(r, e[r]) for r in ranks if isinstance(e.get(r), int)]
    if not got:
        return None
    parts = ", ".join(f"{r.lower()} rank {n}" for r, n in got)
    return f"Promoted: reached {parts}", ("rank", "promotion")


def _saa_scan(e: dict) -> Optional[tuple[str, tuple[str, ...]]]:
    body = e.get("BodyName")
    if not body:
        return None
    return f"Fully mapped {body}", ("exploration", "mapping")


def _carrier_buy(e: dict) -> Optional[tuple[str, tuple[str, ...]]]:
    cs = e.get("Callsign")
    return (f"Bought a fleet carrier ({cs})" if cs else "Bought a fleet carrier",
            ("carrier", "milestone"))


def _shipyard_new(e: dict) -> Optional[tuple[str, tuple[str, ...]]]:
    ship = e.get("ShipType_Localised") or e.get("ShipType")
    if not ship:
        return None
    return f"Added a {_title(ship)} to the fleet", ("ship", "purchase")


def _mission_completed(e: dict) -> Optional[tuple[str, tuple[str, ...]]]:
    reward = _credits(e, "Reward")
    if reward is None or reward < NOTABLE_CREDITS:
        return None
    name = e.get("LocalisedName") or e.get("Name") or "a mission"
    return f"Completed a lucrative mission: {name} ({reward:,} cr)", ("mission", "credits")


def _sell_exploration(e: dict) -> Optional[tuple[str, tuple[str, ...]]]:
    earned = _credits(e, "TotalEarnings", "Earnings", "BaseValue")
    if earned is None or earned < NOTABLE_CREDITS:
        return None
    return f"Sold exploration data for {earned:,} credits", ("exploration", "credits")


def _redeem_voucher(e: dict) -> Optional[tuple[str, tuple[str, ...]]]:
    amount = _credits(e, "Amount")
    if amount is None or amount < NOTABLE_CREDITS:
        return None
    kind = str(e.get("Type") or "").strip() or "bounty"
    return f"Redeemed {kind} vouchers for {amount:,} credits", ("credits", kind.lower())


_HIGHLIGHTS: dict[str, Callable[[dict], Optional[tuple[str, tuple[str, ...]]]]] = {
    "Died": _died,
    "Promotion": _promotion,
    "SAAScanComplete": _saa_scan,
    "CarrierBuy": _carrier_buy,
    "ShipyardNew": _shipyard_new,
    "MissionCompleted": _mission_completed,
    "SellExplorationData": _sell_exploration,
    "MultiSellExplorationData": _sell_exploration,
    "RedeemVoucher": _redeem_voucher,
}


def describe_highlight(event: dict) -> Optional[tuple[str, str, tuple[str, ...]]]:
    """A durable (text, type, tags) memory for a milestone-worthy journal event, or None.

    Deterministic and offline — a curated table lookup, no LLM. `Scan` is special-cased like
    the recent-events feed: only a DETAILED scan of a body the Commander is FIRST to discover
    (WasDiscovered false) earns a memory, so routine auto-scans don't flood the log."""
    if not isinstance(event, dict):
        return None
    name = event.get("event", "")
    if name == "Scan":
        if (event.get("ScanType") == "Detailed" and event.get("BodyName")
                and event.get("WasDiscovered") is False):
            body = event["BodyName"]
            return f"First to discover {body}", HIGHLIGHT_TYPE, ("discovery", "exploration")
        return None
    describer = _HIGHLIGHTS.get(name)
    if describer is None:
        return None
    result = describer(event)
    if result is None:
        return None
    text, tags = result
    return text, HIGHLIGHT_TYPE, tags


def _title(name: str) -> str:
    """Title-case an internal ship id ('federation_corvette' -> 'Federation Corvette')
    while leaving an already-display name alone."""
    return name if any(c.isupper() for c in name) else name.replace("_", " ").title()


class MemoryCapture:
    """Capture sink over a `MemoryStore`: describes journal highlights, stores conversation
    facts, DEDUPES against what's already known, and enforces a CAP so the file stays bounded.

    Cost-aware by construction — describers are table lookups, dedup is keyword_score (no
    embedding), and the cap is a list trim. Nothing here calls a model or the network. Every
    public method is fail-soft: a bad event or store error returns None / is logged, never
    raised, so capture can't take down the voice loop or the event pump."""

    def __init__(self, store: MemoryStore, *, cap: int = DEFAULT_CAP,
                 dedup_threshold: float = DEDUP_THRESHOLD,
                 log: Optional[Callable[[str], None]] = None) -> None:
        self._store = store
        self._cap = max(1, int(cap))
        self._dedup = float(dedup_threshold)
        self._log = log

    # -- journal highlights ------------------------------------------------------------
    def capture_journal_event(self, event: dict) -> Optional[MemoryRecord]:
        """Fold one journal event into memory when it's a curated milestone. Returns the new
        record, or None when the event isn't milestone-worthy or is a duplicate."""
        try:
            described = describe_highlight(event)
            if described is None:
                return None
            text, mtype, tags = described
            return self._add_deduped(text, mtype, tags)
        except Exception as e:  # noqa: BLE001 — capture must never crash a watcher/pump
            self._warn(f"journal highlight capture failed ({e})")
            return None

    # -- conversation facts ------------------------------------------------------------
    def remember(self, text: str, *, type: str = "note",  # noqa: A002 — mirrors the record field
                 tags: object = ()) -> Optional[MemoryRecord]:
        """Store a fact the Commander stated (via the `remember_this` tool). Returns the new
        record, or None when the text is empty or already known. No model call — the LLM
        already produced this as part of the current turn's tool use."""
        try:
            return self._add_deduped(str(text or ""), str(type or "note") or "note", tags)
        except Exception as e:  # noqa: BLE001 — a tool call must never crash the loop
            self._warn(f"remember failed ({e})")
            return None

    # -- shared write path -------------------------------------------------------------
    def _add_deduped(self, text: str, type: str, tags: object) -> Optional[MemoryRecord]:
        text = text.strip()
        if not text:
            return None
        if self._is_duplicate(text):
            return None
        record = self._store.add(MemoryRecord(text=text, type=type, tags=tags))
        self._enforce_cap()
        return record

    def _is_duplicate(self, text: str) -> bool:
        """True when `text` is already represented in the store — either a verbatim (normalized)
        match, or a keyword_score against an existing record at/over the dedup threshold. Cheap,
        offline: a linear scan with the foundation's pure scorer, no embedding."""
        norm = _norm(text)
        candidate = MemoryRecord(text=text)  # for symmetric keyword_score against existing
        for existing in self._store.all():
            if _norm(existing.text) == norm:
                return True
            # Score the EXISTING record's text as the query against the candidate, so a short
            # existing fact fully contained in a longer candidate (and vice-versa) is caught.
            if (keyword_score(existing.text, candidate) >= self._dedup
                    or keyword_score(text, existing) >= self._dedup):
                return True
        return False

    def _enforce_cap(self) -> None:
        """Keep the store within `cap` records. Eviction preferences, in order: drop the OLDEST
        auto-captured journal milestones first (they're reproducible from the journal), and only
        if the store is STILL over cap — i.e. the Commander's own facts alone exceed it — drop
        the oldest of those too. Rewrites the file once (store.save) rather than per-eviction."""
        records = self._store.all()
        if len(records) <= self._cap:
            return
        overflow = len(records) - self._cap
        # Oldest-first indices of the auto milestones — the first eviction pool.
        order = sorted(range(len(records)), key=lambda i: records[i].when)
        drop: set[int] = set()
        for i in order:
            if len(drop) >= overflow:
                break
            if records[i].type == HIGHLIGHT_TYPE:
                drop.add(i)
        # Still over? Fall through to oldest overall (protecting nothing further).
        for i in order:
            if len(drop) >= overflow:
                break
            drop.add(i)
        kept = [r for i, r in enumerate(records) if i not in drop]
        self._store.save(kept)
        self._warn(f"memory cap {self._cap} reached — pruned {len(drop)} oldest record(s)")

    def _warn(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)
