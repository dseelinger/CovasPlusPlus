"""Thargoid voice SFX synthesis + DSP engine (standalone).

Pure additive/FM synthesis in numpy, shaped by a pedalboard DSP chain, rendered
to 48 kHz peak-normalised PCM_16 mono WAV. This module has **no dependency** on
any other project -- it is the audio engine for the standalone Thargoid Voices
generator app.

The four utterance types are inspired by Elite Dangerous Thargoid vocalisations:
metallic, inharmonic, insectoid screeches. Each variant is fully determined by
its ``seed`` plus the three UI sliders (pitch / harshness / reverb), so a good
result can be reproduced by re-entering the same seed.
"""
from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
import soundfile as sf
from pedalboard import (
    Chorus,
    Distortion,
    HighpassFilter,
    LowpassFilter,
    Pedalboard,
    PitchShift,
    Reverb,
)

SAMPLE_RATE = 48_000
PEAK_TARGET = 0.891  # ~ -1 dBFS headroom after peak-normalisation


# --------------------------------------------------------------------------- #
# Utterance specs                                                             #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class UtteranceSpec:
    key: str
    label: str
    description: str
    duration: float  # nominal seconds (jittered per-variant)
    f0: float        # base fundamental in Hz
    # Default slider positions (0-100 UI scale) matching this type's character.
    pitch: int
    harshness: int
    reverb: int


SPECS: dict[str, UtteranceSpec] = {
    "hostile_shriek": UtteranceSpec(
        key="hostile_shriek",
        label="Hostile Shriek",
        description=(
            "Aggressive rising screech — a fast, harsh warning cry with a sharp "
            "attack and metallic overtones. What you hear right before it opens fire."
        ),
        duration=1.15,
        f0=430.0,
        pitch=62,
        harshness=58,
        reverb=32,
    ),
    "scan_query": UtteranceSpec(
        key="scan_query",
        label="Scan Query",
        description=(
            "Inquisitive warble — an up-and-down modulated call that lifts at the "
            "end like a question. The sound of being probed and catalogued."
        ),
        duration=1.05,
        f0=560.0,
        pitch=55,
        harshness=34,
        reverb=46,
    ),
    "distress_wail": UtteranceSpec(
        key="distress_wail",
        label="Distress Wail",
        description=(
            "Mournful falling wail — a long, vibrato-laden descent. A wounded or "
            "grieving cry that hangs in the space around you."
        ),
        duration=2.1,
        f0=620.0,
        pitch=42,
        harshness=40,
        reverb=66,
    ),
    "short_click_chirp": UtteranceSpec(
        key="short_click_chirp",
        label="Short Click-Chirp",
        description=(
            "Quick clicks then a rising chirp — a terse, insectoid burst of "
            "communication. Punchy, dry, and over in a fraction of a second."
        ),
        duration=0.42,
        f0=600.0,
        pitch=58,
        harshness=46,
        reverb=24,
    ),
}


# --------------------------------------------------------------------------- #
# Small DSP helpers                                                           #
# --------------------------------------------------------------------------- #
def _lin(x: float, in_lo: float, in_hi: float, out_lo: float, out_hi: float) -> float:
    """Clamp x to [in_lo, in_hi] and linearly map it onto [out_lo, out_hi]."""
    x = max(in_lo, min(in_hi, float(x)))
    return out_lo + (out_hi - out_lo) * (x - in_lo) / (in_hi - in_lo)


def _integrate(freq: np.ndarray) -> np.ndarray:
    """Integrate an instantaneous-frequency curve (Hz) into a phase curve (rad)."""
    return 2.0 * np.pi * np.cumsum(freq) / SAMPLE_RATE


def _sweep(n: int, start: float, end: float, curve: float = 1.0) -> np.ndarray:
    """A start->end contour of length n, warped by ``curve`` (>1 = ease-in)."""
    x = np.linspace(0.0, 1.0, n) ** curve
    return start + (end - start) * x


def _adsr(n: int, a: float, d: float, s: float, r: float) -> np.ndarray:
    """A simple ADSR amplitude envelope of length n. a/d/r are seconds, s is 0-1."""
    a_n = int(a * SAMPLE_RATE)
    d_n = int(d * SAMPLE_RATE)
    r_n = int(r * SAMPLE_RATE)
    s_n = max(0, n - a_n - d_n - r_n)
    parts = []
    if a_n:
        parts.append(np.linspace(0.0, 1.0, a_n, endpoint=False))
    if d_n:
        parts.append(np.linspace(1.0, s, d_n, endpoint=False))
    parts.append(np.full(s_n, s))
    if r_n:
        parts.append(np.linspace(s, 0.0, r_n))
    env = np.concatenate(parts) if parts else np.ones(n)
    if env.size < n:
        env = np.pad(env, (0, n - env.size))
    return env[:n]


def _alien_tone(
    f0,
    t: np.ndarray,
    rng: np.random.Generator,
    *,
    partials,
    fm_ratio: float,
    fm_index,
    ring_hz: float,
    noise: float = 0.0,
) -> np.ndarray:
    """Core 'alien voice': a stack of inharmonic FM partials, ring-modulated.

    ``f0`` may be a scalar or a per-sample contour. ``partials`` is a list of
    ``(ratio, amplitude)`` pairs; non-integer ratios give the metallic,
    bell-like inharmonicity. ``fm_index`` may be scalar or a contour.
    """
    n = t.size
    f0 = np.asarray(f0, dtype=np.float64)
    if f0.ndim == 0:
        f0 = np.full(n, float(f0))
    modulator = np.sin(_integrate(f0 * fm_ratio))
    fm_index = np.asarray(fm_index, dtype=np.float64)

    sig = np.zeros(n)
    for ratio, amp in partials:
        phase = _integrate(f0 * ratio) + fm_index * modulator
        sig += amp * np.sin(phase)

    if ring_hz:
        sig *= 0.55 + 0.45 * np.sin(2.0 * np.pi * ring_hz * t)

    if noise > 0.0:
        # Noise shaped to follow the tone's own amplitude, so it reads as breath
        # / grit on the voice rather than a constant hiss bed.
        env = np.convolve(np.abs(sig) + 1e-6, np.ones(64) / 64.0, mode="same")
        env /= env.max() + 1e-9
        sig = sig + noise * rng.standard_normal(n) * env

    return sig


# --------------------------------------------------------------------------- #
# Per-type synthesisers (return mono float64, pre-DSP)                        #
# --------------------------------------------------------------------------- #
def _syn_shriek(spec: UtteranceSpec, rng: np.random.Generator) -> np.ndarray:
    dur = spec.duration * rng.uniform(0.85, 1.15)
    n = int(dur * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    f_start = spec.f0 * rng.uniform(0.9, 1.1)
    f0 = _sweep(n, f_start, f_start * rng.uniform(2.4, 3.2), curve=0.6)
    f0 *= 1.0 + 0.03 * np.sin(2.0 * np.pi * rng.uniform(18, 30) * t)  # tremor
    partials = [(1.0, 1.0), (2.76, 0.6), (5.40, 0.4), (8.93, 0.25), (11.3, 0.15)]
    fm_index = _sweep(n, 1.5, rng.uniform(4.0, 7.0), curve=1.5)
    sig = _alien_tone(
        f0, t, rng,
        partials=partials, fm_ratio=rng.uniform(1.4, 1.7),
        fm_index=fm_index, ring_hz=rng.uniform(90, 170), noise=0.18,
    )
    return sig * _adsr(n, 0.010, 0.08, 0.75, 0.25)


def _syn_query(spec: UtteranceSpec, rng: np.random.Generator) -> np.ndarray:
    dur = spec.duration * rng.uniform(0.85, 1.15)
    n = int(dur * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    base = spec.f0 * rng.uniform(0.9, 1.1)
    cycles = rng.uniform(2.0, 3.0)
    warble = base * (1.0 + 0.18 * np.sin(np.linspace(0.0, cycles * 2 * np.pi, n)))
    ramp = np.ones(n)                        # rising question inflection at the tail
    m = int(0.3 * n)
    if m:
        ramp[-m:] = np.linspace(1.0, 1.35, m)
    f0 = warble * ramp
    partials = [(1.0, 1.0), (2.1, 0.55), (3.3, 0.35), (4.7, 0.2)]
    sig = _alien_tone(
        f0, t, rng,
        partials=partials, fm_ratio=rng.uniform(1.0, 1.3),
        fm_index=_sweep(n, 1.0, 2.2, 1.0), ring_hz=rng.uniform(45, 85), noise=0.06,
    )
    return sig * _adsr(n, 0.03, 0.05, 0.85, 0.25)


def _syn_wail(spec: UtteranceSpec, rng: np.random.Generator) -> np.ndarray:
    dur = spec.duration * rng.uniform(0.85, 1.2)
    n = int(dur * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    f_start = spec.f0 * rng.uniform(1.0, 1.2)
    f0 = _sweep(n, f_start, f_start * rng.uniform(0.35, 0.5), curve=1.4)
    f0 *= 1.0 + 0.05 * np.sin(2.0 * np.pi * rng.uniform(4.0, 7.0) * t)  # vibrato
    partials = [(1.0, 1.0), (2.4, 0.6), (4.1, 0.4), (6.2, 0.25), (7.9, 0.12)]
    sig = _alien_tone(
        f0, t, rng,
        partials=partials, fm_ratio=rng.uniform(0.9, 1.2),
        fm_index=_sweep(n, 1.2, 2.0, 1.0), ring_hz=rng.uniform(30, 55), noise=0.08,
    )
    return sig * _adsr(n, 0.08, 0.15, 0.80, 0.50)


def _syn_chirp(spec: UtteranceSpec, rng: np.random.Generator) -> np.ndarray:
    dur = spec.duration * rng.uniform(0.8, 1.2)
    n = int(dur * SAMPLE_RATE)
    sig = np.zeros(n)

    # A short train of resonant clicks in the first ~half.
    for _ in range(int(rng.integers(3, 7))):
        idx = int(rng.uniform(0.0, 0.55) * n)
        clen = int(rng.uniform(0.004, 0.012) * SAMPLE_RATE)
        if idx + clen >= n:
            continue
        ct = np.arange(clen) / SAMPLE_RATE
        click = np.sin(2 * np.pi * rng.uniform(800, 2200) * ct) * np.exp(
            -ct * rng.uniform(250, 500)
        )
        sig[idx:idx + clen] += click * rng.uniform(0.6, 1.0)

    # A rising chirp tail.
    cstart = int(rng.uniform(0.5, 0.65) * n)
    clen = n - cstart
    if clen > 100:
        ct = np.arange(clen) / SAMPLE_RATE
        f0 = _sweep(clen, rng.uniform(500, 700), rng.uniform(1300, 1900), curve=0.7)
        chirp = _alien_tone(
            f0, ct, rng,
            partials=[(1.0, 1.0), (3.0, 0.4), (5.0, 0.2)], fm_ratio=1.5,
            fm_index=_sweep(clen, 1.0, 3.0, 1.0), ring_hz=rng.uniform(120, 200),
            noise=0.10,
        )
        sig[cstart:] += chirp * _adsr(clen, 0.005, 0.02, 0.70, 0.08) * 0.9

    return sig


_SYNTH = {
    "hostile_shriek": _syn_shriek,
    "scan_query": _syn_query,
    "distress_wail": _syn_wail,
    "short_click_chirp": _syn_chirp,
}


# --------------------------------------------------------------------------- #
# DSP chain + render                                                          #
# --------------------------------------------------------------------------- #
def _dsp_chain(pitch: float, harshness: float, reverb: float) -> Pedalboard:
    """Build the pedalboard chain driven by the three UI sliders (0-100).

    - pitch     -> PitchShift semitones  (larger/lower <-> smaller/alien)
    - harshness -> Distortion drive (dB)
    - reverb    -> Reverb room_size + wet level (space / size)
    """
    semitones = _lin(pitch, 0, 100, -10.0, 12.0)
    drive_db = _lin(harshness, 0, 100, 0.0, 38.0)
    room = _lin(reverb, 0, 100, 0.05, 0.90)
    wet = _lin(reverb, 0, 100, 0.0, 0.45)
    return Pedalboard([
        HighpassFilter(cutoff_frequency_hz=90.0),
        PitchShift(semitones=semitones),
        Distortion(drive_db=drive_db),
        LowpassFilter(cutoff_frequency_hz=11_000.0),
        Chorus(rate_hz=0.8, depth=0.25, mix=0.28),
        Reverb(room_size=room, wet_level=wet, dry_level=max(0.0, 1.0 - wet * 0.6),
               width=1.0),
    ])


def _load_source(path: str, seconds: float) -> np.ndarray:
    """Optional real-sample excitation seam: load a WAV, force mono @ SR, trim/pad."""
    data, sr = sf.read(path, dtype="float64")
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != SAMPLE_RATE:  # cheap linear resample; good enough as an excitation
        idx = np.linspace(0, data.size - 1, int(data.size * SAMPLE_RATE / sr))
        data = np.interp(idx, np.arange(data.size), data)
    n = int(seconds * SAMPLE_RATE)
    if data.size < n:
        data = np.pad(data, (0, n - data.size))
    return data[:n]


def render_variant(
    utterance_type: str,
    *,
    pitch: float,
    harshness: float,
    reverb: float,
    seed: int,
    source_path: str | None = None,
) -> np.ndarray:
    """Render one variant to a mono float32 array, peak-normalised to PEAK_TARGET."""
    if utterance_type not in SPECS:
        raise ValueError(f"unknown utterance_type: {utterance_type!r}")
    spec = SPECS[utterance_type]
    rng = np.random.default_rng(int(seed))

    if source_path:
        dur = spec.duration
        dry = _load_source(source_path, dur)
        # Give the real sample a Thargoid edge before the shared DSP chain.
        t = np.arange(dry.size) / SAMPLE_RATE
        dry = dry * (0.55 + 0.45 * np.sin(2 * np.pi * rng.uniform(40, 90) * t))
    else:
        dry = _SYNTH[utterance_type](spec, rng)

    peak = np.max(np.abs(dry))
    if peak > 0:
        dry = dry / peak * 0.7

    board = _dsp_chain(pitch, harshness, reverb)
    wet = board(dry.astype(np.float32), SAMPLE_RATE).astype(np.float64).flatten()

    peak = np.max(np.abs(wet))
    if peak > 0:
        wet = wet / peak * PEAK_TARGET
    return wet.astype(np.float32)


def to_wav_bytes(samples: np.ndarray) -> bytes:
    """Encode float samples as 48 kHz PCM_16 WAV bytes."""
    buf = io.BytesIO()
    sf.write(buf, samples, SAMPLE_RATE, subtype="PCM_16", format="WAV")
    return buf.getvalue()


def measure(samples: np.ndarray) -> dict:
    """Return duration / peak / rms (and dBFS) for level metering."""
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    rms = float(np.sqrt(np.mean(np.square(samples)))) if samples.size else 0.0
    return {
        "duration": round(samples.size / SAMPLE_RATE, 3),
        "peak": round(peak, 4),
        "rms": round(rms, 4),
        "peak_db": round(20 * np.log10(peak), 1) if peak > 0 else -120.0,
        "rms_db": round(20 * np.log10(rms), 1) if rms > 0 else -120.0,
    }
