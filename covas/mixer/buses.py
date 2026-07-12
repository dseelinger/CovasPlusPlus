"""Named audio buses: their level/enable config and their fixed tonal character.

The mixer (mixer.py) owns realtime playback; this module is the pure declarative layer —
the bus names, the [audio.buses] config, and the DSP each bus applies to a submitted
buffer. All pure/offline.

Bus roles (C1):
  * COVAS   — the ship's own assistant: clean, full volume, NO processing.
  * Comms   — radio-treated NPC/player comms (bandpass + static + compression), ducked a
              few dB under COVAS via its default volume.
  * Ambient — SFX layers.   Music — ambient music.   Alert — warning stings.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import dsp

COVAS = "covas"
COMMS = "comms"
AMBIENT = "ambient"
MUSIC = "music"
ALERT = "alert"

BUS_NAMES = (COVAS, COMMS, AMBIENT, MUSIC, ALERT)


@dataclass(frozen=True)
class BusConfig:
    """A bus's mix settings. Tonal DSP is fixed by bus role (see `process`); this is just
    the level trim and the mute switch."""

    name: str
    volume_db: float = 0.0
    enabled: bool = True


# Sensible defaults. Comms sits a few dB under COVAS so a radio line never buries the
# assistant; ambient/music sit further back.
_DEFAULTS: dict[str, BusConfig] = {
    COVAS: BusConfig(COVAS, 0.0, True),
    COMMS: BusConfig(COMMS, -3.0, True),
    AMBIENT: BusConfig(AMBIENT, -6.0, True),
    MUSIC: BusConfig(MUSIC, -12.0, True),
    ALERT: BusConfig(ALERT, 0.0, True),
}


def load_bus_configs(cfg: dict) -> dict[str, BusConfig]:
    """Read [audio.buses.<name>] {volume_db, enabled} for every bus, falling back to the
    defaults above for anything unset. Always returns an entry for each of BUS_NAMES."""
    section = (cfg.get("audio", {}) or {}).get("buses", {}) or {}
    out: dict[str, BusConfig] = {}
    for name in BUS_NAMES:
        d = _DEFAULTS[name]
        raw = section.get(name, {}) or {}
        out[name] = BusConfig(
            name=name,
            volume_db=float(raw.get("volume_db", d.volume_db)),
            enabled=bool(raw.get("enabled", d.enabled)),
        )
    return out


def comms_params(cfg: dict) -> dict:
    """The Comms-bus radio-treatment parameters from [audio.comms], as kwargs for
    dsp.comms_radio. Missing keys fall back to the DSP defaults."""
    c = (cfg.get("audio", {}) or {}).get("comms", {}) or {}
    params: dict = {}
    if "band_low_hz" in c:
        params["low_hz"] = float(c["band_low_hz"])
    if "band_high_hz" in c:
        params["high_hz"] = float(c["band_high_hz"])
    if "noise_level" in c:
        params["noise_level"] = float(c["noise_level"])
    if "compress_threshold_db" in c:
        params["threshold_db"] = float(c["compress_threshold_db"])
    if "compress_ratio" in c:
        params["ratio"] = float(c["compress_ratio"])
    return params


def process(
    name: str, buffer: np.ndarray, sr: int, *, comms_params: dict | None = None
) -> np.ndarray:
    """Apply a bus's tonal DSP to a mono float32 buffer. Bus VOLUME is applied later at mix
    time, not here. Only the Comms bus is processed today (radio treatment); every other bus
    passes through unchanged — COVAS is deliberately clean."""
    if name == COMMS:
        return dsp.comms_radio(buffer, sr, **(comms_params or {}))
    return np.asarray(buffer, dtype=np.float32)
