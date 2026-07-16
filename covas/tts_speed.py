"""Normalized, provider-agnostic voice speed (issue #99).

ONE user-facing speed lives in config as `[tts].speed`: a normalized multiplier where
**1.0 = the voice's normal pace**, `<1.0` slower, `>1.0` faster, bounded to `[0.5, 2.0]`. Each
TTS adapter maps that single number into ITS OWN native speed mechanism and clamps to that
backend's REAL limits — so an out-of-range stored value is safely capped and never sent raw to
error the API, and switching providers can't carry an out-of-range value across (the normalized
value is the only thing stored; the per-provider clamp happens at synth time).

This module is PURE — no config/network I/O beyond reading a plain dict — so the mapping and every
clamp are unit-tested offline. Provider mechanisms + bounds VERIFIED against current provider docs
(2026-07; same rigor as the #91 Gemini-id work — we do not guess these):

  * **ElevenLabs**  `voice_settings.speed`   — 1.0 = normal multiplier; clamp **0.7–1.2**. The REST
    body accepts a wider range but ElevenLabs documents 0.7–1.2 as the quality-safe band (extreme
    values degrade the voice), so we cap there — widening today's 1.0–1.2 so COVAS can slow DOWN.
  * **OpenAI**      `speed`                   — 1.0 = normal multiplier; range **0.25–4.0**.
  * **Edge**        `rate="+NN%"`             — percent change from normal (edge-tts `Communicate`
    `rate`); "+0%" = 1.0×, "+50%" = 1.5×.
  * **Azure**       `<prosody rate="+NN%">`   — Azure treats an SSML `rate` percent as a SIGNED
    RELATIVE change (its own departure from the SSML spec), so we emit the same "+NN%"/"-NN%".
  * **Cartesia**    `voice.__experimental_controls.speed` — a number in **[-1, 1]**, 0 = normal
    (negative slower, positive faster); currently experimental.
  * **Piper**       `length_scale`            — the INVERSE of speed: it's a DURATION multiplier, so
    larger = SLOWER, and "faster" must map to a SMALLER length_scale. Get the sign right.

Sources: ElevenLabs speed-control docs; OpenAI create-speech reference; edge-tts `Communicate`;
Azure SSML prosody docs; Cartesia control-speed guide; Piper1-GPL `SynthesisConfig`.
"""
from __future__ import annotations

# --- User-facing normalized bounds (mirror settings_schema `tts.speed` min/max/default) -------
SPEED_MIN = 0.5
SPEED_MAX = 2.0
SPEED_DEFAULT = 1.0

# --- Per-provider NATIVE clamps (from provider docs — see module docstring) --------------------
_EL_MIN, _EL_MAX = 0.7, 1.2            # ElevenLabs voice_settings.speed (quality-safe band)
_OPENAI_MIN, _OPENAI_MAX = 0.25, 4.0   # OpenAI audio/speech speed
_CARTESIA_MIN, _CARTESIA_MAX = -1.0, 1.0  # Cartesia __experimental_controls.speed axis
# Edge/Azure percent-rate: cap the relative change to the normalized range (±), which both
# backends comfortably support ("-50%" .. "+100%").
_RATE_MIN, _RATE_MAX = SPEED_MIN, SPEED_MAX


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def normalized_speed(cfg: dict) -> float:
    """The stored normalized speed for `cfg`, clamped to `[SPEED_MIN, SPEED_MAX]`.

    Reads `[tts].speed`; falls back to the legacy `[elevenlabs].speed` (back-compat for an older
    config that predates the normalized control) only when `[tts].speed` is absent. Returns
    `SPEED_DEFAULT` on anything unparseable, so a bad stored value never propagates into a request.
    """
    raw = None
    tts = cfg.get("tts") if isinstance(cfg, dict) else None
    if isinstance(tts, dict) and tts.get("speed") is not None:
        raw = tts.get("speed")
    else:
        el = cfg.get("elevenlabs") if isinstance(cfg, dict) else None
        if isinstance(el, dict):
            raw = el.get("speed")
    if raw is None:
        return SPEED_DEFAULT
    try:
        return _clamp(float(raw), SPEED_MIN, SPEED_MAX)
    except (TypeError, ValueError):
        return SPEED_DEFAULT


def is_default(n: float) -> bool:
    """True when the normalized speed is effectively 1.0 — the signal for an adapter to send NO
    speed param at all, so the default request stays byte-for-byte the provider's own default."""
    try:
        return abs(float(n) - 1.0) <= 1e-6
    except (TypeError, ValueError):
        return True


# --- per-provider mappings (each clamps to its backend's real limits) -------------------------

def elevenlabs_speed(n: float) -> float:
    """ElevenLabs `voice_settings.speed` (1.0 = normal). Clamp to the quality-safe 0.7–1.2."""
    return _clamp(float(n), _EL_MIN, _EL_MAX)


def openai_speed(n: float) -> float:
    """OpenAI `audio/speech` `speed` (1.0 = normal). Clamp to 0.25–4.0."""
    return _clamp(float(n), _OPENAI_MIN, _OPENAI_MAX)


def _rate_percent(n: float) -> int:
    """Percent change from normal for an SSML/edge rate: 1.0 -> 0, 1.5 -> +50, 0.8 -> -20."""
    return int(round((_clamp(float(n), _RATE_MIN, _RATE_MAX) - 1.0) * 100))


def rate_string(n: float) -> str:
    """A signed percentage rate string ('+50%', '-20%', '+0%') — shared by Edge and Azure, which
    both take a signed-relative percent."""
    return f"{_rate_percent(n):+d}%"


# Edge and Azure use the identical signed-percent rate; alias for call-site clarity.
edge_rate = rate_string
azure_rate = rate_string


def cartesia_speed(n: float) -> float:
    """Cartesia `__experimental_controls.speed` on a [-1, 1] axis (0 = normal). Maps the normalized
    range piecewise so 1.0 -> 0, the fast half [1, 2] -> [0, 1], the slow half [0.5, 1] -> [-1, 0]."""
    n = _clamp(float(n), SPEED_MIN, SPEED_MAX)
    if n >= 1.0:
        val = (n - 1.0) / (SPEED_MAX - 1.0)   # [1, 2] -> [0, 1]
    else:
        val = (n - 1.0) / (1.0 - SPEED_MIN)   # [0.5, 1] -> [-1, 0]
    return _clamp(val, _CARTESIA_MIN, _CARTESIA_MAX)


def piper_length_scale(n: float, base: float = 1.0) -> float:
    """Piper `length_scale` — the INVERSE of speed. length_scale is a DURATION multiplier (larger =
    slower), so faster (n > 1) must give a SMALLER scale and slower (n < 1) a LARGER one. `base` is
    the voice's own default length_scale (usually 1.0); we scale it by 1/n so 1.0 leaves it
    untouched. A non-positive/garbage base falls back to 1.0."""
    n = _clamp(float(n), SPEED_MIN, SPEED_MAX)
    try:
        b = float(base)
    except (TypeError, ValueError):
        b = 1.0
    if b <= 0:
        b = 1.0
    return b / n
