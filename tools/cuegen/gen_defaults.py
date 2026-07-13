"""Generate the SHIPPED default UI-cue families for COVAS++ (I8).

Everything here is synthesized from scratch (detuned sine partials + envelopes) in the same
"sci-fi voice" as gen_scifi.py — nothing sampled, nothing copyrighted. Output lands directly
under covas/assets/cues/<type>/ (the read-only bundled defaults the app ships). The LOCKED
listen cue (covas/assets/cues/listen/listen_ea.wav) is NOT touched here.

Run from the repo root:  .venv\\Scripts\\python.exe tools\\cuegen\\gen_defaults.py
Regenerate any time the voice changes; the emitted .wav files are tracked/shipped.

Cue families (design INSTALLER_DESIGN.md §"Shippable default assets"):
  * processing = low, unobtrusive tick (played while the LLM thinks / searches)
  * completed  = resolved rising interval (answer ready, just before speech)
  * failure    = soft descending minor (no speech heard, or a service error)
  * interdiction_sting = an original alert sting (bundled fallback for the C8 cue)
Each family ships a few originals so the folder-discovery rotation is visible out of the box.
"""
from __future__ import annotations

import os

import numpy as np
import soundfile as sf

SR = 48000
# covas/assets/cues/ — two levels up from tools/cuegen/, then into the package assets.
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CUES = os.path.join(_REPO, "covas", "assets", "cues")

# Equal-temperament note frequencies (A4 = 440).
E4, A4, E5, A5 = 329.63, 440.00, 659.25, 880.00
C5, G5 = 523.25, 783.99
Fs4, D4 = 369.99, 293.66  # F#4, D4


def _env(n: int, attack: float = 0.006, release: float = 0.16) -> np.ndarray:
    a = min(int(SR * attack), n // 2)
    r = min(int(SR * release), n - a)
    e = np.ones(n)
    e[:a] = np.linspace(0, 1, a)
    e[-r:] = np.linspace(1, 0, r) ** 1.6
    return e


def scifi_tone(freq: float, dur: float, *, detune_cents: float = 6.0, vib_hz: float = 5.0,
               vib_depth: float = 0.004, brightness: float = 1.0) -> np.ndarray:
    """The shared voice: three detuned oscillators (shimmer) with a gentle vibrato — synthetic,
    not a pure sine. `brightness` scales the upper partials (lower = softer/duller)."""
    n = int(SR * dur)
    t = np.linspace(0, dur, n, endpoint=False)
    vib = 1 + vib_depth * np.sin(2 * np.pi * vib_hz * t)

    def osc(f: float) -> np.ndarray:
        ph = 2 * np.pi * np.cumsum(np.full(n, f) * vib) / SR
        return (np.sin(ph) + 0.5 * brightness * np.sin(2 * ph)
                + 0.3 * brightness * np.sin(3 * ph) + 0.12 * brightness * np.sin(4 * ph))

    d = 2 ** (detune_cents / 1200)
    sig = osc(freq) + 0.6 * osc(freq * d) + 0.6 * osc(freq / d)
    return sig * _env(n, release=min(0.18, dur * 0.6))


def _interval(f1: float, f2: float, d1: float, d2: float, gap: float = 0.03,
              **kw) -> np.ndarray:
    """Two tones back-to-back (f1 then f2), a short gap between."""
    parts = [scifi_tone(f1, d1, **kw), np.zeros(int(SR * gap)), scifi_tone(f2, d2, **kw)]
    return np.concatenate(parts)


# --- processing: low, unobtrusive ticks (must NOT distract while you wait) ---
def process_tick() -> np.ndarray:
    tick = scifi_tone(300, 0.05, vib_depth=0.0, brightness=0.5)
    return np.concatenate([tick, np.zeros(int(SR * 0.05)), tick * 0.8])


def process_low() -> np.ndarray:
    return scifi_tone(260, 0.12, vib_depth=0.002, brightness=0.4) * 0.9


# --- completed: resolved RISING interval (bright, "answer ready") ---
def completed_fifth() -> np.ndarray:  # C5 -> G5, rising fifth
    return _interval(C5, G5, 0.14, 0.22, brightness=1.0)


def completed_octave() -> np.ndarray:  # A4 -> A5, rising octave (open, resolved)
    return _interval(A4, A5, 0.13, 0.24, brightness=0.9)


def completed_fourth() -> np.ndarray:  # E4 -> A4, rising fourth (echoes the listen cue's shape)
    return _interval(E4, A4, 0.14, 0.24, brightness=0.85)


# --- failure: soft DESCENDING minor (gentle, unmistakably "no") ---
def failure_minor_third() -> np.ndarray:  # A4 -> F#4, descending minor third, soft
    return _interval(A4, Fs4, 0.16, 0.30, brightness=0.5, vib_depth=0.003)


def failure_low() -> np.ndarray:  # E4 -> D4, small descending step, softer/duller
    return _interval(E4, D4, 0.16, 0.32, brightness=0.4, vib_depth=0.003)


# --- interdiction sting: an original alert (bright, urgent, but short) ---
def interdiction_sting() -> np.ndarray:
    """A tense two-tone alert: a bright tone bent up into a held dissonant partner."""
    n = int(SR * 0.22)
    hold = int(n * 0.35)
    freq = np.concatenate([np.linspace(A4, E5, n - hold), np.full(hold, E5)])
    ph = 2 * np.pi * np.cumsum(freq) / SR
    d = 2 ** (12 / 1200)  # a wide, uneasy detune
    ph2 = 2 * np.pi * np.cumsum(freq * d) / SR
    sig = (np.sin(ph) + 0.5 * np.sin(2 * ph)) + 0.7 * (np.sin(ph2) + 0.4 * np.sin(2 * ph2))
    sig = sig * _env(n, attack=0.004, release=0.10)
    return np.concatenate([sig, np.zeros(int(SR * 0.02)), sig[::-1][:int(SR * 0.06)] * 0.4])


def _save(cue_type: str, name: str, sig: np.ndarray, peak: float = 0.5) -> None:
    folder = os.path.join(CUES, cue_type)
    os.makedirs(folder, exist_ok=True)
    sig = sig / max(abs(sig).max(), 1e-9) * peak
    path = os.path.join(folder, name)
    sf.write(path, sig.astype(np.float32), SR)
    print(f"  wrote {cue_type}/{name}  ({len(sig)/SR*1000:.0f} ms)")


_FAMILIES = {
    "processing": {"proc_tick.wav": process_tick, "proc_low.wav": process_low},
    "completed": {"done_fifth.wav": completed_fifth, "done_octave.wav": completed_octave,
                  "done_fourth.wav": completed_fourth},
    "failure": {"fail_minor.wav": failure_minor_third, "fail_low.wav": failure_low},
    "interdiction_sting": {"interdiction_sting.wav": interdiction_sting},
}


if __name__ == "__main__":
    print(f"Writing shipped default cues under {CUES} :")
    for cue_type, gens in _FAMILIES.items():
        for fname, fn in gens.items():
            _save(cue_type, fname, fn())
    print("Done. (listen/ is LOCKED and untouched.)")
