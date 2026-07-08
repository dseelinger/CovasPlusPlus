"""COVAS++ diagnostic: (1) play the three cues, (2) live-print key events.

Run this to isolate audio vs. keyboard problems.
"""
from __future__ import annotations
import time
import keyboard
from covas.config import load_config
from covas.audio import CuePlayer

cfg = load_config()

print("\n--- AUDIO TEST: playing the three cues (listen for chirps) ---")
cues = CuePlayer(cfg)
for name in ("listening", "processing", "done"):
    print(f"  playing {name} ...", flush=True)
    cues.play(name, wait=True)
    time.sleep(0.4)
print("Audio test done. Did you hear three chirps?\n")

print("--- KEY TEST: press keys to see what your keyboard emits ---")
print(f"  (config PTT = {cfg['keys']['push_to_talk']!r}, cancel = {cfg['keys']['cancel']!r})")
print("  Press [ and ] a few times. Press ESC to finish.\n", flush=True)


def show(e):  # noqa: ANN001
    print(f"  KEY  {e.event_type:4}  name={e.name!r:15}  scan={e.scan_code}", flush=True)


keyboard.hook(show)
keyboard.wait("esc")
print("\nDiagnostic complete. Close this window.")
