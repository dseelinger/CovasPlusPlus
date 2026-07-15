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

from dataclasses import dataclass
from typing import Callable, Optional

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
    voice: Optional[Voice]


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
            voice: Optional[Voice] = Voice(provider, ref, gender)
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
            ),
        ),
        Cue(
            "carrier_captain_status", COMMS, {AT_OWN_CARRIER}, cooldown_s=240.0,
            voice_role=CAPTAIN,
            phrasings=(
                "{captain}: services are online and the crew's squared away.",
                "{captain} here — she's holding steady, Commander.",
                "This is {captain}. Refuel, repair, and rearm are all standing by.",
            ),
        ),
        Cue(
            "carrier_captain_nearby", COMMS, {NEAR_OWN_CARRIER}, cooldown_s=260.0,
            voice_role=CAPTAIN,
            phrasings=(
                "{captain} here — good to see you back in the system, Commander.",
                "This is {captain}. We've got you on the scope; the deck's ready when you are.",
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
        names: Optional[dict[str, str]] = None,
        log: Optional[Callable[[str], None]] = None,
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
