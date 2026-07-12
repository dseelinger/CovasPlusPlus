"""Pure PCM DSP building blocks for the per-bus processing chains.

Everything here is a pure function on a mono float32 buffer in [-1, 1] — no audio
device, no shared state — so the mix and the comms "radio" treatment are fully
unit-testable offline (DESIGN §9). The radio sound is produced by these at RUNTIME,
never by pre-editing audio files: a comms line is synthesized clean, then `comms_radio()`
is applied before it reaches the Comms bus.

The biquads run a straight difference-equation loop over the WHOLE buffer (a spoken
line or a stinger — tens of thousands of samples), not a per-callback realtime block,
so filter state need not be threaded across chunks. That keeps them pure and exact.
"""
from __future__ import annotations

import numpy as np

# Butterworth Q (maximally flat passband) for the 2nd-order sections.
_BUTTERWORTH_Q = 0.70710678


def db_to_linear(db: float) -> float:
    """Convert a decibel gain to a linear amplitude multiplier (0 dB -> 1.0)."""
    return float(10.0 ** (db / 20.0))


def apply_gain(x: np.ndarray, db: float) -> np.ndarray:
    """Scale a buffer by a decibel gain. Negative dB ducks it (gain/duck are the same op)."""
    return (np.asarray(x, dtype=np.float32) * db_to_linear(db)).astype(np.float32)


def tone(freq_hz: float, seconds: float, sr: int, amplitude: float = 0.5) -> np.ndarray:
    """A pure sine tone — a deterministic test/demo signal (used by the bandpass tests and
    the comms-bus demo script)."""
    n = int(round(seconds * sr))
    t = np.arange(n, dtype=np.float64) / float(sr)
    return (amplitude * np.sin(2.0 * np.pi * freq_hz * t)).astype(np.float32)


def _biquad_coeffs(kind: str, sr: int, f0: float, q: float):
    """RBJ audio-EQ-cookbook coefficients for a 2nd-order low/high-pass, normalized by a0."""
    w0 = 2.0 * np.pi * (float(f0) / float(sr))
    cos_w0 = np.cos(w0)
    sin_w0 = np.sin(w0)
    alpha = sin_w0 / (2.0 * q)
    if kind == "lowpass":
        b0 = (1.0 - cos_w0) / 2.0
        b1 = 1.0 - cos_w0
        b2 = (1.0 - cos_w0) / 2.0
    elif kind == "highpass":
        b0 = (1.0 + cos_w0) / 2.0
        b1 = -(1.0 + cos_w0)
        b2 = (1.0 + cos_w0) / 2.0
    else:  # pragma: no cover - guarded by the two public wrappers below
        raise ValueError(f"unknown biquad kind: {kind!r}")
    a0 = 1.0 + alpha
    a1 = -2.0 * cos_w0
    a2 = 1.0 - alpha
    return (b0 / a0, b1 / a0, b2 / a0), (a1 / a0, a2 / a0)


def _apply_biquad(x: np.ndarray, b, a) -> np.ndarray:
    b0, b1, b2 = b
    a1, a2 = a
    x = np.asarray(x, dtype=np.float64)
    if x.shape[0] == 0:
        return x.astype(np.float32)
    y = np.empty_like(x)
    x1 = x2 = y1 = y2 = 0.0
    for n in range(x.shape[0]):
        xn = x[n]
        yn = b0 * xn + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        y[n] = yn
        x2, x1 = x1, xn
        y2, y1 = y1, yn
    return y.astype(np.float32)


def highpass(x: np.ndarray, sr: int, cutoff_hz: float, q: float = _BUTTERWORTH_Q) -> np.ndarray:
    """2nd-order high-pass: cut everything below `cutoff_hz` (12 dB/octave)."""
    return _apply_biquad(x, *_biquad_coeffs("highpass", sr, cutoff_hz, q))


def lowpass(x: np.ndarray, sr: int, cutoff_hz: float, q: float = _BUTTERWORTH_Q) -> np.ndarray:
    """2nd-order low-pass: cut everything above `cutoff_hz` (12 dB/octave)."""
    return _apply_biquad(x, *_biquad_coeffs("lowpass", sr, cutoff_hz, q))


def bandpass(
    x: np.ndarray, sr: int, low_hz: float, high_hz: float, q: float = _BUTTERWORTH_Q
) -> np.ndarray:
    """Band-limit to roughly [low_hz, high_hz] by cascading a high-pass and a low-pass.
    The classic comms band is ~300-3000 Hz — that's what makes a voice sound "over the radio"."""
    return lowpass(highpass(x, sr, low_hz, q), sr, high_hz, q)


def add_noise(x: np.ndarray, level: float, *, seed: int = 0) -> np.ndarray:
    """Mix in a bed of white noise at `level` (linear amplitude) — the static hiss under a
    radio voice. Deterministic given `seed` so tests stay stable; `level <= 0` is a no-op."""
    x = np.asarray(x, dtype=np.float32)
    if level <= 0.0 or x.shape[0] == 0:
        return x.copy()
    rng = np.random.default_rng(seed)
    noise = rng.uniform(-1.0, 1.0, size=x.shape[0]).astype(np.float32) * float(level)
    return (x + noise).astype(np.float32)


def compress(x: np.ndarray, threshold_db: float = -12.0, ratio: float = 4.0) -> np.ndarray:
    """Hard-knee peak compressor: samples whose magnitude exceeds the threshold are pushed
    back toward it by `ratio`, so loud peaks are tamed while quiet passages pass through
    untouched. Sample-local (no attack/release) — enough to even out comms levels, and pure.
    `ratio <= 1` is a no-op."""
    x = np.asarray(x, dtype=np.float32)
    if ratio <= 1.0 or x.shape[0] == 0:
        return x.copy()
    thresh = db_to_linear(threshold_db)
    mag = np.abs(x)
    over = mag > thresh
    out = x.copy()
    out[over] = np.sign(x[over]) * (thresh + (mag[over] - thresh) / ratio).astype(np.float32)
    return out.astype(np.float32)


def comms_radio(
    x: np.ndarray,
    sr: int,
    *,
    low_hz: float = 300.0,
    high_hz: float = 3000.0,
    noise_level: float = 0.02,
    threshold_db: float = -12.0,
    ratio: float = 4.0,
    seed: int = 0,
) -> np.ndarray:
    """The Comms-bus voice treatment, applied at runtime: band-limit to the radio band,
    compress, then lay in a light static bed. Produces the "over the radio" character with
    no pre-edited assets. Pure + deterministic (fixed noise seed)."""
    y = bandpass(x, sr, low_hz, high_hz)
    y = compress(y, threshold_db, ratio)
    y = add_noise(y, noise_level, seed=seed)
    return y
