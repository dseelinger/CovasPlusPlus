"""Multi-bus audio mixer + per-bus DSP (C-series infrastructure, DESIGN §7).

The shared foundation for comms, chatter, SFX, music, and alerts: a set of named
buses, each with its own tonal DSP chain and independent volume, mixed to one output
device. The DSP and the mix are PURE functions on PCM buffers (unit-testable, no
device); only `BusMixer.start()` opens audio hardware.

COVAS's own reply path (covas/tts.py) is deliberately left untouched — its bus is the
clean, full-volume passthrough, so existing speech is unchanged.
"""
from __future__ import annotations

from . import dsp
from .buses import (
    ALERT,
    AMBIENT,
    BUS_NAMES,
    COMMS,
    COVAS,
    MUSIC,
    BusConfig,
    comms_params,
    load_bus_configs,
    process,
)
from .comms import (
    Decision,
    VoiceableComms,
    capture,
    classify,
    dedup_key,
    evaluate,
    is_receive_text,
    message_template,
)
from .cues import Cue, CueRegistry, cue_problems, validate_cue
from .driver import CueDriver
from .eligibility import STATES, EligibilityEngine, flag_states, fuel_states, journal_states
from .governor import CueGovernor, GovernorConfig
from .mixer import (
    BusMixer,
    bus_gains,
    float_to_pcm16,
    mix_buffers,
    pcm16_to_float,
    resample,
    speak_on_bus,
)

__all__ = [
    "dsp",
    "ALERT",
    "AMBIENT",
    "BUS_NAMES",
    "COMMS",
    "COVAS",
    "MUSIC",
    "BusConfig",
    "comms_params",
    "load_bus_configs",
    "process",
    "Cue",
    "CueRegistry",
    "cue_problems",
    "validate_cue",
    "STATES",
    "EligibilityEngine",
    "flag_states",
    "fuel_states",
    "journal_states",
    "CueGovernor",
    "GovernorConfig",
    "CueDriver",
    "Decision",
    "VoiceableComms",
    "capture",
    "classify",
    "dedup_key",
    "evaluate",
    "is_receive_text",
    "message_template",
    "BusMixer",
    "bus_gains",
    "float_to_pcm16",
    "mix_buffers",
    "pcm16_to_float",
    "resample",
    "speak_on_bus",
]
