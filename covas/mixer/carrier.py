"""Fleet-carrier context voices (issue #19) — captain / tower / carrier chatter.

When the Commander is at (or in the same system as) the fleet carrier they OWN, three named,
independently-voiced roles bring the carrier to life:

  * **Captain**       — status/welcome flavor, spoken in the carrier's home system (NEAR context)
                        and aboard (AT context).
  * **Tower Control** — docking/traffic-control flavor, spoken only while DOCKED at the carrier.
  * **Carrier chatter** — ambient deck/crew/services buzz while aboard.

This mirrors the C6 space-chatter design, with three deliberate differences that make it a
CONTEXT voice rather than a per-speaker one:

  1. Eligibility is the own-carrier LOCATION context (`AT_OWN_CARRIER` / `NEAR_OWN_CARRIER`,
     folded in by the audio layer from EDContext carrier tracking), not population.
  2. Each role speaks in its OWN configured voice (`[audio.carrier].<role>`), resolved through
     the same provider registry as the cast — so any TTS provider works — with a stable cast-pool
     voice as the zero-config fallback. A role's display NAME is configurable too and is woven
     into its lines via `{captain}` / `{tower}` placeholders.
  3. Every line is fact_bearing (curated pool, deterministic rotation) — the LLM is never in this
     path, and literal docking messages ("docking granted, pad 07") still flow through the comms
     gate (C4/C5), so this layer adds ATMOSPHERE without duplicating that.

Pure + offline-testable: config parsing, cue definitions, line selection, and name templating
have no I/O; only the injected `speak`/`voice_for` seams touch synthesis.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from ..providers.registry import resolve_provider
from .buses import COMMS
from .cues import Cue
from .eligibility import AT_OWN_CARRIER, NEAR_OWN_CARRIER
from .voices import Voice

# The three carrier roles. "chatter" reuses the cast-role name (its provider override lives under
# the same `[audio.voices.providers].chatter` key); "captain"/"tower" are new cast roles (#19).
CAPTAIN = "captain"
TOWER = "tower"
CHATTER = "chatter"
CARRIER_ROLES = (CAPTAIN, TOWER, CHATTER)

# Default display names, used when the Commander configures none. Woven into lines that carry a
# `{captain}`/`{tower}` placeholder; carrier chatter is anonymous crew, so it has no name.
_DEFAULT_NAMES: dict[str, str] = {CAPTAIN: "Captain", TOWER: "Tower Control", CHATTER: ""}


# ---- config -------------------------------------------------------------------------------
@dataclass(frozen=True)
class CarrierRole:
    """One configured carrier role: its display `name` and its `voice` (None = fall back to a
    stable cast-pool voice, so the role still sounds distinct with zero configuration)."""

    role: str
    name: str
    voice: Voice | None


@dataclass(frozen=True)
class CarrierConfig:
    """The parsed `[audio.carrier]` section: the master enable + the three roles by name."""

    enabled: bool
    roles: dict[str, CarrierRole]

    def name_map(self) -> dict[str, str]:
        """role -> display name, for templating carrier lines."""
        return {r: cr.name for r, cr in self.roles.items()}


def build_carrier_config(cfg: dict) -> CarrierConfig:
    """Parse `[audio.carrier]` into a `CarrierConfig`. Each `[audio.carrier].<role>` may set a
    `name`, a `voice_ref` (an EL voice_id / Piper .onnx path — blank = auto cast-pool voice), a
    `voice_provider` (blank = the role's `[audio.voices.providers]` override, else the cast
    provider), and a `gender`. Pure — no I/O, no provider construction."""
    c = (cfg.get("audio", {}) or {}).get("carrier", {}) or {}
    enabled = bool(c.get("enabled", True))
    roles: dict[str, CarrierRole] = {}
    for role in CARRIER_ROLES:
        sub = c.get(role, {}) or {}
        name = str(sub.get("name", _DEFAULT_NAMES[role]) or _DEFAULT_NAMES[role])
        ref = str(sub.get("voice_ref", "") or "").strip()
        if ref:
            prov_override = str(sub.get("voice_provider", "") or "").strip().lower()
            provider = prov_override or resolve_provider(cfg, role, default=None)
            gender = str(sub.get("gender", "neutral") or "neutral").strip().lower()
            voice: Voice | None = Voice(provider, ref, gender)
        else:
            voice = None
        roles[role] = CarrierRole(role, name, voice)
    return CarrierConfig(enabled, roles)


# ---- cues ---------------------------------------------------------------------------------
# All carrier lines are fact_bearing (curated pool, no LLM) and ride the radio-treated COMMS bus.
# `voice_role` tags each cue with the role whose configured voice speaks it (the CarrierPlayer
# reads it). Names are woven in at play time via `{captain}`/`{tower}` placeholders. Cooldowns are
# generous — the carrier should feel present, not chatty; the C3 governor caps the overall rate.
def carrier_cues() -> list[Cue]:
    return [
        # -- Captain: aboard (AT) welcome/status + a lighter in-system (NEAR) greeting ----------
        Cue(
            "carrier_captain_welcome", COMMS, {AT_OWN_CARRIER}, cooldown_s=300.0,
            voice_role=CAPTAIN,
            phrasings=(
                "{captain} here — welcome back aboard, Commander.",
                "This is {captain}. Good to have you on the flight deck.",
                "{captain} speaking. All decks report nominal.",
                "{captain} here — the ship's yours, Commander; make yourself at home.",
            ),
        ),
        Cue(
            "carrier_captain_status", COMMS, {AT_OWN_CARRIER}, cooldown_s=240.0,
            voice_role=CAPTAIN,
            phrasings=(
                "{captain}: services are online and the crew's squared away.",
                "{captain} here — she's holding steady, Commander.",
                "This is {captain}. Refuel, repair, and rearm are all standing by.",
                "{captain}: hull and systems check out green across the board, Commander.",
            ),
        ),
        # Deferential duty flavor (issue #137): status/deck/jump-prep/upkeep asides, always in the
        # employed-by register — reported to the owner, never a peer. Low-frequency, aboard only.
        Cue(
            "carrier_captain_duty", COMMS, {AT_OWN_CARRIER}, cooldown_s=280.0,
            voice_role=CAPTAIN,
            phrasings=(
                "{captain} here — the quartermaster's balanced the books; nothing needs your hand, "
                "Commander.",
                "This is {captain}. Deck crew's rotated and upkeep's paid through the week, at your "
                "leisure.",
                "{captain}: tritium reserves are steady — I'll flag you well before we run short, "
                "Commander.",
                "This is {captain}. Jump drive's prepped and cycled, ready the moment you set a "
                "course.",
                "{captain} here — cargo's stowed and the manifest's in order, Commander; your "
                "carrier's running clean.",
            ),
        ),
        Cue(
            "carrier_captain_nearby", COMMS, {NEAR_OWN_CARRIER}, cooldown_s=260.0,
            voice_role=CAPTAIN,
            phrasings=(
                "{captain} here — good to see you back in the system, Commander.",
                "This is {captain}. We've got you on the scope; the deck's ready when you are.",
                "{captain}: your carrier's holding station, Commander — come in whenever you like.",
            ),
        ),
        # -- Tower Control: docking/traffic flavor, DOCKED only ---------------------------------
        Cue(
            "carrier_tower_traffic", COMMS, {AT_OWN_CARRIER}, cooldown_s=220.0,
            voice_role=TOWER,
            phrasings=(
                "{tower}: pads are clear, traffic's light.",
                "{tower} here — flight deck's yours, Commander.",
                "{tower}: lanes are open, no inbound to worry about.",
            ),
        ),
        Cue(
            "carrier_tower_depart", COMMS, {AT_OWN_CARRIER}, cooldown_s=200.0,
            voice_role=TOWER,
            phrasings=(
                "{tower}: you're cleared for launch whenever you're ready.",
                "{tower} here — mind the mass lock on your way out.",
                "{tower}: safe flight out there, Commander.",
            ),
        ),
        # -- Carrier chatter: anonymous ambient deck/crew buzz, DOCKED only ---------------------
        Cue(
            "carrier_deck_chatter", COMMS, {AT_OWN_CARRIER}, cooldown_s=160.0,
            voice_role=CHATTER,
            phrasings=(
                "Deck crew's running the usual checks.",
                "Somebody get a scan on that inbound hauler.",
                "Refuel line's clear, next pad's open.",
                "Quiet shift on the flight deck for once.",
            ),
        ),
        Cue(
            "carrier_services_chatter", COMMS, {AT_OWN_CARRIER}, cooldown_s=190.0,
            voice_role=CHATTER,
            phrasings=(
                "Market's open, prices holding.",
                "Repair bay's got a slot free.",
                "Rearm crew's standing by if you need them.",
            ),
        ),
    ]


# ---- event-anchored captain responses (issue #137) ----------------------------------------
# The ambient cues above fire on LOCATION context throttled by a long cooldown, so a welcome is a
# random greeting — NOT guaranteed at the exact moment you arrive or leave. These two GUARANTEE a
# captain line at the transitions that matter: dropping out of supercruise at/near the owned carrier
# (arrival) and undocking from it (departure). They're fired DIRECTLY at the event (like the
# interdiction cue), not registered with the driver, so the ambient budget can't swallow them.
_ARRIVAL_EVENT = "SupercruiseExit"     # dropped into normal space (at/near the carrier's system)
_DEPARTURE_EVENT = "Undocked"          # left the pad; the event carries StationType + MarketID
_FLEET_CARRIER = "fleetcarrier"        # Undocked.StationType for a carrier (case-insensitive)


def carrier_event_cues() -> dict[str, Cue]:
    """The two event-anchored captain cues (issue #137), keyed 'arrival'/'departure'. Each carries
    the CAPTAIN role + its own deferential pool so it routes through the same CarrierPlayer (name
    weaving + configured voice) as the ambient cues, but is fired at the transition, not the driver."""
    return {
        "arrival": Cue(
            "carrier_captain_arrival", COMMS, {NEAR_OWN_CARRIER}, voice_role=CAPTAIN,
            phrasings=(
                "{captain} here — dropping in nicely. Good to have you back, Commander.",
                "This is {captain}. Welcome home, Commander; the deck's ready for you.",
                "{captain} speaking — we have you on approach. Good to see you, Commander.",
            ),
        ),
        "departure": Cue(
            "carrier_captain_departure", COMMS, {NEAR_OWN_CARRIER}, voice_role=CAPTAIN,
            phrasings=(
                "{captain} here — safe flying, Commander; we'll hold station.",
                "This is {captain}. Fair winds, Commander; the carrier's yours to return to.",
                "{captain}: clear of the deck. We'll keep her warm for you, Commander.",
            ),
        ),
    }


class CaptainDedup:
    """A short shared 'a captain line just spoke' gate (issue #137). Both the event-anchored
    responder and the ambient captain path consult it, so the guaranteed arrival/departure line and
    a long-cooldown ambient welcome can't stack into a back-to-back double-fire. Monotonic-clock,
    not thread-guarded — driven from the single audio event-pump thread."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic, window_s: float = 60.0) -> None:
        self._clock = clock
        self._window = float(window_s)
        self._last = float("-inf")

    def allow(self) -> bool:
        """True when no captain line has spoken within the dedup window."""
        return (self._clock() - self._last) >= self._window

    def mark(self) -> None:
        """Record that a captain line just spoke (starts the window)."""
        self._last = self._clock()


class CarrierEventResponder:
    """Fires a GUARANTEED captain line at the two carrier transitions — supercruise-arrival at/near
    the owned carrier, and undock leaving it — independent of the ambient cooldown cues (issue #137).

    Injected seams (offline-testable):
      * `play(cue) -> bool` — voice a captain cue (the app wires this to the CarrierPlayer, gated on
        carrier-on / not-muted); returns True only if it started.
      * `at_near() -> (at_own, near_own)` — the live own-carrier location context.
      * `owned_id() -> Optional[int]` — the owned carrier's CarrierID (== its MarketID), so an undock
        from a DIFFERENT carrier in the same system isn't mistaken for leaving ours. None = unknown.
      * `dedup` — the shared CaptainDedup (None = no dedup); prevents a double-fire with the ambient
        captain cue in the same tick.
    Fail-soft: never raises into the event pump."""

    def __init__(
        self,
        play: Callable[[Cue], bool],
        *,
        at_near: Callable[[], tuple[bool, bool]],
        owned_id: Callable[[], int | None] | None = None,
        dedup: CaptainDedup | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._play = play
        self._at_near = at_near
        self._owned_id = owned_id
        self._dedup = dedup
        self._log = log
        cues = carrier_event_cues()
        self._arrival = cues["arrival"]
        self._departure = cues["departure"]

    def _is_own_carrier_undock(self, event: dict) -> bool:
        """Whether an Undocked event is us leaving the OWN carrier: a fleet-carrier undock whose
        MarketID matches the owned carrier id (or, when the id is unknown, any fleet-carrier undock —
        the caller has already confirmed we're in the owned carrier's system)."""
        if str(event.get("StationType") or "").strip().lower() != _FLEET_CARRIER:
            return False
        owned = None
        if self._owned_id is not None:
            try:
                owned = self._owned_id()
            except Exception:  # noqa: BLE001 — an id-read glitch just drops the id check
                owned = None
        mid = event.get("MarketID")
        if owned is not None and isinstance(mid, int) and not isinstance(mid, bool):
            return mid == owned
        return True

    def _fire(self, cue: Cue) -> bool:
        """Play the cue through the dedup gate; on a real play, arm the shared window."""
        if self._dedup is not None and not self._dedup.allow():
            return False
        started = bool(self._play(cue))
        if started:
            if self._dedup is not None:
                self._dedup.mark()
            if self._log is not None:
                self._log(f"carrier[captain] event {cue.name} -> {cue.bus}")
        return started

    def on_event(self, event: dict) -> bool:
        """React to one bus event: an arrival (SupercruiseExit near/at the owned carrier) or a
        departure (Undocked from it) fires the matching captain line. Returns whether one played.
        Never raises — it shares the audio event pump."""
        try:
            if not isinstance(event, dict):
                return False
            name = event.get("event")
            if name == _ARRIVAL_EVENT:
                _at, near = self._at_near()
                if not near:
                    return False
                return self._fire(self._arrival)
            if name == _DEPARTURE_EVENT:
                _at, near = self._at_near()
                if not near or not self._is_own_carrier_undock(event):
                    return False
                return self._fire(self._departure)
            return False
        except Exception:  # noqa: BLE001 — a bad event must never take down the pump
            return False


# ---- player -------------------------------------------------------------------------------
class _SafeNames(dict):
    """A format-map dict that leaves an unknown `{placeholder}` intact instead of raising, so a
    line can never blow up on a name key that isn't configured."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def apply_names(text: str, names: dict[str, str]) -> str:
    """Weave role display names into a carrier line's `{captain}`/`{tower}` placeholders. A stray
    or malformed brace leaves the line untouched (never raises). Pure."""
    try:
        return str(text).format_map(_SafeNames(names))
    except (ValueError, IndexError, KeyError):
        return str(text)


class CarrierPlayer:
    """The C3 driver's `play` callback for carrier cues (those with a `voice_role`). Selects the
    role's next pooled line (deterministic rotation), weaves in the configured names, resolves the
    role's voice, and speaks it on the cue's bus.

    Injected seams (offline-testable):
      * `speak(voice, text, bus) -> bool` — synthesize+play a Voice on a bus (the app wires this to
        AudioLayer._submit_voice), returning True only if playback started;
      * `voice_for(role) -> Voice` — the configured voice for a role, or its cast-pool fallback.
      * `names` — role -> display name, for templating.
    """

    def __init__(
        self,
        speak: Callable[[Voice, str, str], bool],
        voice_for: Callable[[str], Voice],
        *,
        names: dict[str, str] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._speak = speak
        self._voice_for = voice_for
        self._names = dict(names or {})
        self._log = log
        self._rot: dict[str, int] = {}   # per-cue pool rotation pointer

    def set_names(self, names: dict[str, str]) -> None:
        """Refresh the display names (e.g. after a settings change)."""
        self._names = dict(names or {})

    def line_for(self, cue: Cue) -> str:
        """The cue's next pooled line with names woven in (deterministic rotation)."""
        raw = cue.phrasing_at(self._rot.get(cue.name, 0))
        return apply_names(raw, self._names)

    def __call__(self, cue: Cue) -> bool:
        role = str(getattr(cue, "voice_role", "") or "")
        if not role:
            return False
        text = self.line_for(cue)
        if not text.strip():
            return False
        started = bool(self._speak(self._voice_for(role), text, cue.bus))
        if started:
            self._rot[cue.name] = self._rot.get(cue.name, 0) + 1
            if self._log is not None:
                self._log(f"carrier[{role}] {cue.name} -> {cue.bus}: {text}")
        return started


def register_carrier(registry) -> None:  # noqa: ANN001 — a CueRegistry
    """Register every carrier cue with the cue registry (mirrors register_chatter)."""
    for cue in carrier_cues():
        registry.register(cue)
