"""Decode a guitar recording and transcribe it: onsets, durations, pitches.
Monophonic autocorrelation pitch detection. Reports what was played so we can re-voice it.
"""
import sys

import av
import numpy as np

PATH = sys.argv[1]
SR = 44100

# --- decode (m4a/mp3/wav) to mono float via PyAV ---
container = av.open(PATH)
stream = container.streams.audio[0]
resampler = av.AudioResampler(format="flt", layout="mono", rate=SR)
chunks = []
for frame in container.decode(stream):
    out = resampler.resample(frame)
    for rf in (out if isinstance(out, list) else [out]):
        if rf is not None:
            chunks.append(rf.to_ndarray().reshape(-1))
sig = np.concatenate(chunks).astype(np.float64)
sig /= max(abs(sig).max(), 1e-9)
dur = len(sig) / SR
print(f"Decoded {dur:.2f}s @ {SR}Hz  ({len(sig)} samples)")

# --- RMS envelope ---
win = int(0.030 * SR)
hop = int(0.010 * SR)
rms = np.array([np.sqrt(np.mean(sig[i:i + win] ** 2))
                for i in range(0, len(sig) - win, hop)])
rms /= max(rms.max(), 1e-9)
thr = max(0.12, rms.mean() * 0.6)

# --- segment voiced regions ---
voiced = rms > thr
segs = []
i = 0
while i < len(voiced):
    if voiced[i]:
        j = i
        while j < len(voiced) and voiced[j]:
            j += 1
        t0, t1 = i * hop / SR, j * hop / SR
        if t1 - t0 >= 0.05:  # drop blips
            segs.append((t0, t1))
        i = j
    else:
        i += 1


def pitch(x):
    """Autocorrelation f0 over 70-1200 Hz."""
    x = x - x.mean()
    if np.sqrt(np.mean(x ** 2)) < 1e-4:
        return None
    ac = np.correlate(x, x, "full")[len(x) - 1:]
    lo, hi = int(SR / 1200), int(SR / 70)
    seg = ac[lo:hi]
    if len(seg) == 0:
        return None
    lag = lo + int(np.argmax(seg))
    return SR / lag if lag else None


NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def note(f):
    if not f:
        return "?", 0
    m = round(69 + 12 * np.log2(f / 440.0))
    return f"{NAMES[m % 12]}{m // 12 - 1}", m


print(f"\n{'#':>2}  {'onset':>6}  {'dur':>5}  {'note':>5}  {'freq':>7}")
print("-" * 36)
prev_end = 0.0
for k, (t0, t1) in enumerate(segs, 1):
    a, b = int(t0 * SR), int(t1 * SR)
    mid = sig[a:b]
    # pitch from the sustained middle third (most stable)
    third = len(mid) // 3
    f = pitch(mid[third:2 * third] if third > SR // 100 else mid)
    nm, _ = note(f)
    gap = t0 - prev_end
    gaptxt = f"  (gap {gap*1000:.0f}ms)" if gap > 0.03 and k > 1 else ""
    print(f"{k:>2}  {t0:6.2f}  {t1-t0:5.2f}  {nm:>5}  {f or 0:7.1f}{gaptxt}")
    prev_end = t1

print(f"\nTotal voiced segments: {len(segs)}")
