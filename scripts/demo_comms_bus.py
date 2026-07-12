"""Manual check for the C1 audio-bus mixer: play a test tone CLEAN (COVAS bus) and then
RADIO-FILTERED (Comms bus) so you can hear the comms treatment.

    .venv\\Scripts\\python.exe scripts\\demo_comms_bus.py

Opens the default output device via the mixer. No API calls, no ED, no cost.
"""
from __future__ import annotations

import time

from covas.config import load_config
from covas.mixer import COMMS, COVAS, BusMixer, dsp


def main() -> None:
    cfg = load_config()
    sr = int(cfg.get("audio", {}).get("mix_sample_rate", 16000))
    # A little chord so the band-limiting is audible (a 200 Hz + 1 kHz + 6 kHz mix: the
    # comms band keeps ~1 kHz and drops the 200 Hz body and the 6 kHz sparkle).
    chord = (
        dsp.tone(200.0, 1.5, sr, amplitude=0.25)
        + dsp.tone(1000.0, 1.5, sr, amplitude=0.25)
        + dsp.tone(6000.0, 1.5, sr, amplitude=0.25)
    )

    mixer = BusMixer(cfg, sample_rate=sr)
    mixer.start()
    try:
        print("COVAS bus (clean)...")
        mixer.submit(COVAS, chord, sr)
        time.sleep(2.0)
        print("Comms bus (radio-filtered: bandpassed + compressed + static)...")
        mixer.submit(COMMS, chord, sr)
        time.sleep(2.0)
    finally:
        mixer.stop()
        print("done.")


if __name__ == "__main__":
    main()
