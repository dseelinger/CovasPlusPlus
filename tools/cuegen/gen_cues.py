"""Generate ORIGINAL, royalty-free UI cue candidates for COVAS++ (audition only).

All synthesized from scratch (sine partials + envelopes) — nothing sampled, nothing
copyrighted. numpy + soundfile are already project deps.
"""
import numpy as np
import soundfile as sf
import os

SR = 48000
OUT = os.path.dirname(os.path.abspath(__file__))


def _env(n, attack=0.008, release=0.12):
    """Fast-attack / smooth-release amplitude envelope, click-free."""
    a = min(int(SR * attack), n // 2)
    r = min(int(SR * release), n - a)
    e = np.ones(n)
    e[:a] = np.linspace(0, 1, a)
    e[-r:] = np.linspace(1, 0, r) ** 1.6
    return e


def _tone(freq, dur, partials=(1.0, 0.25, 0.1)):
    t = np.linspace(0, dur, int(SR * dur), endpoint=False)
    wave = sum(amp * np.sin(2 * np.pi * freq * (i + 1) * t)
               for i, amp in enumerate(partials))
    return wave / max(abs(wave).max(), 1e-9)


def _save(name, sig, peak=0.5):
    sig = sig / max(abs(sig).max(), 1e-9) * peak
    path = os.path.join(OUT, name)
    sf.write(path, sig.astype(np.float32), SR)
    print(f"  wrote {name}  ({len(sig)/SR*1000:.0f} ms)")


# --- LISTENING cues (played when PTT opens the mic): bright, rising, inviting ---

def listen_a():  # clean two-tone step up (classic "ready" blip)
    lo = _tone(660, 0.09) * _env(int(SR * 0.09))
    hi = _tone(988, 0.13) * _env(int(SR * 0.13))
    return np.concatenate([lo, hi])


def listen_b():  # quick upward chirp (whoosh-y, sci-fi)
    dur = 0.20
    t = np.linspace(0, dur, int(SR * dur), endpoint=False)
    freq = np.linspace(600, 1150, t.size)
    sig = np.sin(2 * np.pi * np.cumsum(freq) / SR)
    return sig * _env(t.size, attack=0.006, release=0.10)


def listen_c():  # soft single "boop" with a fifth above (warm, subtle)
    dur = 0.16
    sig = _tone(784, dur, partials=(1.0, 0.4, 0.15)) + 0.3 * _tone(1176, dur)
    return sig * _env(int(SR * dur), attack=0.01, release=0.11)


# --- PROCESSING cue (played while the LLM thinks): lower, unobtrusive tick ---

def process_a():  # low double-tick
    tick = _tone(420, 0.05) * _env(int(SR * 0.05), attack=0.004, release=0.04)
    gap = np.zeros(int(SR * 0.05))
    return np.concatenate([tick, gap, tick * 0.85])


# Note frequencies (equal temperament, A4 = 440).
E5, A5 = 659.25, 880.00
E4, A4 = 329.63, 440.00


def two_tone(f1, f2, d1=0.50, d2=0.25, gap=0.0):
    """Two tones back-to-back; first (E) held longer than second (A) -> 'EEEE AA'."""
    a = _tone(f1, d1) * _env(int(SR * d1), attack=0.01, release=0.10)
    b = _tone(f2, d2) * _env(int(SR * d2), attack=0.01, release=0.12)
    parts = [a]
    if gap > 0:
        parts.append(np.zeros(int(SR * gap)))
    parts.append(b)
    return np.concatenate(parts)


if __name__ == "__main__":
    print("Generating E->A listen cue candidates (~0.75s, E longer than A):")
    _save("cand_EA_hi.wav", two_tone(E5, A5, d1=0.50, d2=0.25))            # bright
    _save("cand_EA_lo.wav", two_tone(E4, A4, d1=0.50, d2=0.25))            # warm
    _save("cand_EA_hi_gap.wav", two_tone(E5, A5, d1=0.48, d2=0.24, gap=0.04))  # slight gap
    _save("cand_process_a.wav", process_a())
    print("Done.")
