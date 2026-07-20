"""Currency registry — the game currencies COVAS can GROUND, one data row each (issue #101).

The wallet ethos mirrors the ship-spec one (#83): never let the model invent a balance.
Balances come only from the journal, and a currency COVAS understands lives here as a single
row mapping (journal event + field) -> (display name + hedged phrasing + the spoken names a
Commander uses to ask about it). Adding a future currency FDev documents is a ONE-ROW edit —
no new handler, no context field, no detector change.

A brand-new currency (the "merc coins" case) is deliberately absent: with no row it is never
extracted into the wallet and its name is not in `known_names()`, so the context detector
won't inject a bogus balance and the LLM guardrail (`llm._CURRENCY_GUARDRAIL`) makes the model
say plainly that it has no data yet rather than confabulate an amount. Honest, not hallucinated.

A journal *heuristic* that sniffs an unrecognised event for a `*Balance`/`*Count` integer was
DESIGNED but deliberately NOT BUILT for v1 (issue #101 open question): the existing `Cargo`
handler already keys on an integer named `Count`, so a naive sniffer would misfire immediately.
The prompt guardrail alone meets the "honest, not invented" bar; the heuristic waits for a real
FDev currency to prove the need. See `docs/currency-behavior.md`.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Currency:
    """One grounded currency. `event`/`field` say where its balance is read from the journal
    (`field` is a dotted path so nested payloads like `Finance.CarrierBalance` work); `display`
    + `phrasing` render the hedged status line; `names` are the spoken phrases that ask about it
    (they drive the context detector, so they must include how players actually say it)."""
    key: str                    # stable id, also the EDContext wallet key
    event: str                  # journal event that carries the balance
    field: str                  # dotted path into that event's payload
    display: str                # spoken display name ("credits")
    phrasing: str               # status-line template; {amount} = grouped integer
    names: tuple[str, ...]      # spoken phrases that reference this currency


# The registry. TWO rows day one (issue #101 decision) so the multi-currency design is actually
# exercised, not just credits. Both balances are login-only (see the hedged phrasing): `Credits`
# arrives on `LoadGame`, the carrier balance on `CarrierStats` — neither updates intra-session, so
# the wording says "as of login". Intra-session credit-delta summing is out of scope (a follow-up).
REGISTRY: tuple[Currency, ...] = (
    Currency(
        key="credits",
        event="LoadGame",
        field="Credits",
        display="credits",
        phrasing="as of login you had {amount} credits",
        names=(
            "my credits", "credits", "how many credits", "how much money",
            "how much cash", "my balance", "my money", "my cash", "my wallet",
            "my bank balance", "how rich am i", "my net worth",
        ),
    ),
    Currency(
        key="carrier_balance",
        event="CarrierStats",
        field="Finance.CarrierBalance",
        display="fleet carrier balance",
        phrasing="your fleet carrier balance was {amount} credits as of login",
        names=(
            "carrier balance", "my carrier balance", "carrier funds",
            "carrier account", "carrier bank",
        ),
    ),
)


def _dig(event: dict, dotted: str):
    """Walk a dotted path into a nested event payload, returning None on any missing/non-dict
    hop (so `Finance.CarrierBalance` reads through the `Finance` object, or gives up cleanly)."""
    cur = event
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def extract_balances(event: dict) -> dict:
    """{currency-key: int amount} for every registry row whose event matches `event` and whose
    field is present as a real number. Empty when the event carries no KNOWN balance — that is
    exactly how a new/unknown currency stays invisible to the wallet (no row -> not extracted).
    bools are rejected (they're `int` subclasses) so a stray flag can't masquerade as an amount."""
    name = event.get("event", "")
    out: dict = {}
    for cur in REGISTRY:
        if cur.event != name:
            continue
        val = _dig(event, cur.field)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            out[cur.key] = int(val)
    return out


def known_names() -> list[str]:
    """Every spoken phrase that references a KNOWN currency — the money-question phrases the
    context detector folds into its status set. An unknown currency's name is deliberately
    absent, so 'how many merc coins' never trips a status lookup (it degrades to the guardrail)."""
    names: list[str] = []
    for cur in REGISTRY:
        names.extend(cur.names)
    return names


def wallet_line(wallet: dict) -> str | None:
    """One hedged status-block clause for the known balances present in `wallet` ({key: amount}),
    or None when none are known. Each row's phrasing hedges on staleness (balances are login-only).
    A wallet key with no registry row is ignored — the wallet can only voice grounded currencies."""
    from ..i18n import fmt_int
    parts: list[str] = []
    for cur in REGISTRY:
        amt = wallet.get(cur.key)
        if isinstance(amt, (int, float)) and not isinstance(amt, bool):
            parts.append(cur.phrasing.format(amount=fmt_int(amt)))  # locale grouping (#199)
    return "; ".join(parts) if parts else None
