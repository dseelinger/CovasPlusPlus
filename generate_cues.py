"""
Generate the three COVAS++ sound cues as clean TNG/LCARS-style sine chirps.
Re-runnable: overwrites sounds/listening.wav, processing.wav, done.wav.
Swap in your own files anytime — just point config.toml's [sound_cues] at them.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent
SR = 44100


def tone(freq: float, ms: float, *, harmonic: float = 0.22) -> np.ndarray:
    """A single sine 'blip' with a raised-cosine envelope (no clicks) and a
    touch of 2nd harmonic for a richer 'computer' timbre."""
    n = int(SR * ms / 1000.0)
    t = np.arange(n) / SR
    wave = np.sin(2 * np.pi * freq * t) + harmonic * np.sin(2 * np.pi * 2 * freq * t)
    # raised-cosine attack/release (~8 ms each side)
    edge = max(1, int(SR * 0.008))
    env = np.ones(n)
    ramp = 0.5 * (1 - np.cos(np.linspace(0, np.pi, edge)))
    env[:edge] = ramp
    env[-edge:] = ramp[::-1]
    return wave * env


def gap(ms: float) -> np.ndarray:
    return np.zeros(int(SR * ms / 1000.0))


def build(segments: list[np.ndarray]) -> np.ndarray:
    sig = np.concatenate(segments)
    peak = np.max(np.abs(sig)) or 1.0
    sig = sig / peak * 0.70          # normalize to ~ -3 dBFS
    return sig.astype(np.float32)


def main() -> None:
    with open(ROOT / "config.toml", "rb") as f:
        cfg = tomllib.load(f)["sound_cues"]

    cues = {
        # rising two-tone: "I'm listening"
        "listening":  build([tone(880, 70), gap(30), tone(1175, 90)]),
        # soft neutral mid tone: "working"
        "processing": build([tone(620, 110), gap(25), tone(620, 110)]),
        # resolved ascending pair (a fifth up): "ready / affirmative"
        "done":       build([tone(784, 80), gap(20), tone(1319, 120)]),
    }

    for name, sig in cues.items():
        out = Path(cfg[name])
        out.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(out), sig, SR, subtype="PCM_16")
        print(f"  wrote {out}  ({len(sig)/SR*1000:.0f} ms)")

    print("Done. Three cues generated.")


if __name__ == "__main__":
    main()
