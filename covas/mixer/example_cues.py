"""Worked-example cues (C8) — proving the layered-cue pattern with real cues.

Two shapes, both governed by C3 so nothing over-talks:

  * InterdictionCue — an EVENT-triggered LAYERED cue (off journal Interdiction / UnderAttack).
    It fires three layers, in order, to three different buses: a warning sting on the ALERT bus,
    the assistant's threat-assessment line on the COVAS bus (clean, deterministic pool rotation),
    and the pirate's own line on the COMMS bus (radio/static, ducked). This is the composite
    pattern — one game event driving a coordinated multi-bus moment.

  * Ambient SFX cues — eligibility-gated one-shots on the AMBIENT bus (Thargoid voices,
    hyperspace weirdness, a space-radiation bed), picked and rate-governed by the C3 driver and
    played sample-by-sample. This is the simple pattern — state makes a cue eligible, the
    governor lets one through.

Everything opt-in and off by default; the LLM is not involved (curated pools + samples only).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

from .buses import ALERT, AMBIENT, COMMS, COVAS
from .cues import Cue
from .eligibility import DEEP_SPACE, HYPERSPACE, UNPOPULATED
from .governor import CueGovernor

_INTERDICTION_EVENTS = {"Interdiction", "UnderAttack"}
DEFAULT_STING = "sounds/interdiction_sting.wav"   # alert-bus sting (git-ignored asset)

# Curated pools — deterministic rotation, no LLM. The threat line is the assistant (clean COVAS
# voice); the pirate line is voiced on the radio-treated comms bus.
DEFAULT_THREAT_LINES: tuple[str, ...] = (
    "Hostile on our tail — shields up.",
    "We're being pulled out of frame shift. Brace.",
    "Interdiction detected. Weapons hot.",
)
DEFAULT_PIRATE_LINES: tuple[str, ...] = (
    "Drop your cargo and you get to live.",
    "Nowhere left to run.",
    "Hand it over and this stays civil.",
)


@dataclass(frozen=True)
class Layer:
    """One layer of a composite cue: what to play, on which bus. `kind` is 'sfx' (payload is a
    sample path) or 'line' (payload is spoken text); `voice` applies to a comms line."""

    bus: str
    kind: str
    payload: str
    voice: str = ""


class InterdictionCue:
    """Event-triggered layered cue. `emit(layer) -> bool` routes one layer to its bus (the app
    wires it to the mixer / TTS); the shared C3 governor + a cooldown collapse a burst of
    UnderAttack ticks into a single sequence."""

    def __init__(
        self,
        emit: Callable[[Layer], bool],
        *,
        governor: Optional[CueGovernor] = None,
        enabled: bool = True,
        sting: str = DEFAULT_STING,
        threat_lines: tuple[str, ...] = DEFAULT_THREAT_LINES,
        pirate_lines: tuple[str, ...] = DEFAULT_PIRATE_LINES,
        pirate_voice: str = "male",
        cooldown_s: float = 45.0,
        clock: Callable[[], float] = time.monotonic,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._emit = emit
        self._governor = governor
        self._enabled = enabled
        self._sting = str(sting or "").strip() or DEFAULT_STING
        self._threat = tuple(threat_lines)
        self._pirate = tuple(pirate_lines)
        self._pirate_voice = pirate_voice
        self._cooldown_s = cooldown_s
        self._clock = clock
        self._log = log
        self._rot_threat = 0
        self._rot_pirate = 0

    @classmethod
    def from_cfg(cls, cfg: dict, emit: Callable[[Layer], bool], *,
                 governor: Optional[CueGovernor] = None,
                 clock: Callable[[], float] = time.monotonic) -> "InterdictionCue":
        i = (cfg.get("audio", {}) or {}).get("interdiction", {}) or {}
        return cls(emit, governor=governor, enabled=bool(i.get("enabled", False)),
                   sting=str(i.get("sting", "")), clock=clock)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, on: bool) -> None:
        self._enabled = bool(on)

    def _governor_cue(self) -> Cue:
        return Cue("interdiction", ALERT, frozenset(), cooldown_s=self._cooldown_s)

    def layers(self) -> list[Layer]:
        """The three layers in play order — sting (alert), threat (COVAS), pirate (comms) — using
        the current rotation positions. Pure; does not advance the rotation."""
        out: list[Layer] = [Layer(ALERT, "sfx", self._sting)]
        if self._threat:
            out.append(Layer(COVAS, "line", self._threat[self._rot_threat % len(self._threat)]))
        if self._pirate:
            out.append(Layer(COMMS, "line",
                             self._pirate[self._rot_pirate % len(self._pirate)],
                             voice=self._pirate_voice))
        return out

    def on_event(self, event: dict) -> list[Layer]:
        """React to a journal Interdiction/UnderAttack: if the governor allows, emit the three
        layers in order and advance the pools. Returns the layers actually emitted. Never raises
        (shares the event pump)."""
        try:
            if not self._enabled:
                return []
            if not isinstance(event, dict) or event.get("event") not in _INTERDICTION_EVENTS:
                return []
            now = self._clock()
            gcue = self._governor_cue()
            if self._governor is not None and not self._governor.allow(gcue, now)[0]:
                return []
            emitted = [layer for layer in self.layers() if bool(self._emit(layer))]
            self._rot_threat += 1
            self._rot_pirate += 1
            if self._governor is not None:
                self._governor.mark_fired(gcue, now)
            if self._log is not None:
                self._log(f"interdiction: emitted {len(emitted)} layers")
            return emitted
        except Exception:  # noqa: BLE001 — a bad event must not take down the pump
            return []


# ---- ambient SFX cues (C3-driven) -----------------------------------------------------------
class SfxPlayer:
    """The C3 driver's play callback for SFX cues: pick a sample (deterministic rotation) and
    route it to the cue's bus via `play_sample(sample, bus) -> bool`."""

    def __init__(
        self,
        play_sample: Callable[[str, str], bool],
        *,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._play = play_sample
        self._log = log
        self._rot: dict[str, int] = {}

    def __call__(self, cue: Cue) -> bool:
        if not cue.samples:
            return False
        idx = self._rot.get(cue.name, 0)
        sample = cue.samples[idx % len(cue.samples)]
        started = bool(self._play(sample, cue.bus))
        if started:
            self._rot[cue.name] = idx + 1
            if self._log is not None:
                self._log(f"sfx {cue.name} -> {cue.bus}: {sample}")
        return started


# name -> (eligibility states, cooldown seconds). Samples come from [audio.sfx].<name>.
_SFX_DEFS: dict[str, tuple[set[str], float]] = {
    "thargoid_voices": ({HYPERSPACE}, 180.0),
    "hyperspace_weirdness": ({HYPERSPACE}, 120.0),
    "space_radiation": ({DEEP_SPACE, UNPOPULATED}, 200.0),
}


def sfx_cues(cfg: Optional[dict] = None) -> list[Cue]:
    """The shipped ambient SFX cues, on the ambient bus, eligibility-gated. Sample sets come from
    [audio.sfx].<name> (git-ignored local assets); an empty set is valid but silent."""
    sfx = ((cfg or {}).get("audio", {}) or {}).get("sfx", {}) or {}
    return [
        Cue(name, AMBIENT, frozenset(states), cooldown_s=cd,
            samples=tuple(sfx.get(name, []) or []))
        for name, (states, cd) in _SFX_DEFS.items()
    ]


def register_sfx(registry, cfg: Optional[dict] = None) -> None:  # noqa: ANN001 — a CueRegistry
    for cue in sfx_cues(cfg):
        registry.register(cue)
