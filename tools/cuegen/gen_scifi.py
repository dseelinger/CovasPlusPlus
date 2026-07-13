"""Re-voice the transcribed guitar phrase (E4 -> A3, descending 4th, E longer) as
original sci-fi cue tones. Detuned partials + gentle vibrato = synthetic, not guitar.
"""
import numpy as np
import soundfile as sf
import os

SR = 48000
OUT = os.path.dirname(os.path.abspath(__file__))

E4, A3 = 329.63, 220.00
E5, A4 = 659.25, 440.00


def _env(n, attack=0.006, release=0.16):
    a = min(int(SR * attack), n // 2)
    r = min(int(SR * release), n - a)
    e = np.ones(n)
    e[:a] = np.linspace(0, 1, a)
    e[-r:] = np.linspace(1, 0, r) ** 1.6
    return e


def scifi_tone(freq, dur, detune_cents=6.0, vib_hz=5.0, vib_depth=0.004):
    n = int(SR * dur)
    t = np.linspace(0, dur, n, endpoint=False)
    vib = 1 + vib_depth * np.sin(2 * np.pi * vib_hz * t)

    def osc(f):
        ph = 2 * np.pi * np.cumsum(np.full(n, f) * vib) / SR
        return (np.sin(ph) + 0.5 * np.sin(2 * ph)
                + 0.3 * np.sin(3 * ph) + 0.12 * np.sin(4 * ph))

    d = 2 ** (detune_cents / 1200)
    sig = osc(freq) + 0.6 * osc(freq * d) + 0.6 * osc(freq / d)  # shimmer
    return sig * _env(n, release=min(0.18, dur * 0.6))


def glide(f0, f1, dur):
    """Single tone that holds f0 then bends down to f1 (portamento) — very sci-fi."""
    n = int(SR * dur)
    hold = int(n * 0.6)
    freq = np.concatenate([np.full(hold, f0), np.linspace(f0, f1, n - hold)])
    ph = 2 * np.pi * np.cumsum(freq) / SR
    sig = np.sin(ph) + 0.5 * np.sin(2 * ph) + 0.3 * np.sin(3 * ph)
    d = 2 ** (6 / 1200)
    ph2 = 2 * np.pi * np.cumsum(freq * d) / SR
    sig = sig + 0.6 * (np.sin(ph2) + 0.5 * np.sin(2 * ph2))
    return sig * _env(n, release=0.20)


def phrase(fE, fA, dE=0.50, dA=0.25, gap=0.03):
    parts = [scifi_tone(fE, dE), np.zeros(int(SR * gap)), scifi_tone(fA, dA)]
    return np.concatenate(parts)


def _save(name, sig, peak=0.5):
    sig = sig / max(abs(sig).max(), 1e-9) * peak
    sf.write(os.path.join(OUT, name), sig.astype(np.float32), SR)
    print(f"  wrote {name}  ({len(sig)/SR*1000:.0f} ms)")


if __name__ == "__main__":
    print("Re-voicing E->A (descending 4th, E longer):")
    _save("scifi_EA_played.wav", phrase(E4, A3))          # exact pitches you played
    _save("scifi_EA_bright.wav", phrase(E5, A4))          # up an octave (small-speaker friendly)
    _save("scifi_EA_glide.wav", glide(E4, A3, 0.72))      # one tone, bends E->A
    print("Done.")
