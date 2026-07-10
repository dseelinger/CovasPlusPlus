"""External Community-Goals feed — the COMPLETE active list, so CGs the Commander hasn't
visited still surface (the point of the feature).

Source: Inara's `getCommunityGoalsRecent` (POST https://inara.cz/inapi/v1/). EDSM has no
public CG API (every candidate endpoint 404s as of build time), so Inara is the supported
feed; it needs a free generic Inara API key. No key -> journal-only (the capability says so).

Reuses the search `Http` POST seam (so the real app injects `RequestsHttp` and tests inject a
fake — the default `pytest` never hits the network, DESIGN §9). Fail-soft: any transport / API
error raises `CGFeedError`, which the capability catches and degrades to journal-only.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..search.spansh import Http
from .models import CommunityGoal

_INARA_URL = "https://inara.cz/inapi/v1/"


class CGFeedError(Exception):
    """The external CG feed couldn't be read (no key, transport error, or a non-OK API status).
    The capability catches it and falls back to journal-only CGs."""


@dataclass(frozen=True)
class CGConfig:
    """Immutable snapshot of `[cg]`. `source` selects the external feed; blank key or
    source='none' means journal-only."""
    source: str = "inara"            # "inara" | "none"
    inara_api_key: str = ""

    @classmethod
    def from_cfg(cls, cfg: dict) -> "CGConfig":
        c = cfg.get("cg", {}) or {}
        d = cls()
        return cls(
            source=str(c.get("source", d.source) or d.source).strip().lower(),
            inara_api_key=str(c.get("inara_api_key", "") or "").strip(),
        )

    @property
    def external_enabled(self) -> bool:
        """Whether an external feed is actually usable (a source + the credentials it needs)."""
        return self.source == "inara" and bool(self.inara_api_key)


# App identity for the Inara envelope (not user-facing; not a secret).
_APP_NAME = "COVAS-Plus-Plus"
_APP_VERSION = "0.1"


def fetch_inara_goals(http: Http, *, api_key: str, timestamp: str,
                      app_name: str = _APP_NAME, app_version: str = _APP_VERSION,
                      commander_name: str | None = None) -> list[CommunityGoal]:
    """Fetch the complete active CG list from Inara. `timestamp` is an ISO string the caller
    stamps (kept out of here so this stays pure/testable). Raises `CGFeedError` on any failure.

    External goals are `engaged=False` — the journal is the only source of personal standing,
    which the capability folds in via `merge`."""
    if not api_key:
        raise CGFeedError("no Inara API key configured")
    header: dict = {"appName": app_name, "appVersion": app_version,
                    "isDeveloped": False, "APIkey": api_key}
    if commander_name:
        header["commanderName"] = commander_name
    payload = {
        "header": header,
        "events": [{"eventName": "getCommunityGoalsRecent",
                    "eventTimestamp": timestamp, "eventData": []}],
    }
    try:
        status, body = http.post_json(_INARA_URL, payload,
                                      headers={"Content-Type": "application/json"})
    except Exception as e:  # noqa: BLE001 — any transport failure degrades to journal-only
        raise CGFeedError(f"couldn't reach Inara ({e})") from e

    if status != 200 or not isinstance(body, dict):
        raise CGFeedError(f"Inara returned HTTP {status}")
    if (body.get("header", {}) or {}).get("eventStatus") != 200:
        raise CGFeedError("Inara rejected the request (check the API key)")
    events = body.get("events") or []
    if not events or not isinstance(events[0], dict):
        raise CGFeedError("Inara returned no CG event")
    ev = events[0]
    if ev.get("eventStatus") != 200:
        raise CGFeedError(f"Inara CG event status {ev.get('eventStatus')}")
    return [_from_inara(g) for g in (ev.get("eventData") or []) if isinstance(g, dict) and g.get("communitygoalName")]


def _from_inara(g: dict) -> CommunityGoal:
    return CommunityGoal(
        title=str(g["communitygoalName"]),
        system=str(g.get("starsystemName") or ""),
        station=g.get("stationName"),
        expiry=g.get("goalExpiry"),
        cgid=g.get("communitygoalGameID"),
        is_complete=bool(g.get("isCompleted")),
        tier_reached=g.get("tierReached"),
        current_total=None,
        engaged=False,
    )
