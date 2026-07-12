"""Manual check for the C8 layered interdiction cue: fire a fake Interdiction and hear the three
layers — a warning sting (alert bus), the assistant's threat line (COVAS bus, clean), and the
pirate's line (comms bus, radio-treated).

    .venv\\Scripts\\python.exe scripts\\demo_interdiction.py

Uses the configured TTS + real audio device. The sting needs a local sample at the configured
[audio.interdiction].sting path (git-ignored) — if it's missing, that layer is skipped with a note.
"""
from __future__ import annotations

import time
from pathlib import Path

import soundfile as sf

from covas.config import load_config
from covas.mixer import (
    COMMS,
    BusMixer,
    InterdictionCue,
    Layer,
    comms_voice_id,
    speak_on_bus,
)
from covas.providers.factory import make_tts


def main() -> None:
    cfg = load_config()
    tts = make_tts(cfg)
    mixer = BusMixer(cfg)
    mixer.start()

    def emit(layer: Layer) -> bool:
        print(f"  layer -> {layer.bus}: {layer.kind} {layer.payload!r}")
        if layer.kind == "sfx":
            path = Path(layer.payload)
            if not path.exists():
                print(f"    (sting file missing at {layer.payload} — skipping this layer)")
                return False
            data, sr = sf.read(str(path), dtype="float32", always_2d=False)
            mixer.submit(layer.bus, data, sr)
            return True
        voice_id = comms_voice_id(cfg, layer.voice) if layer.bus == COMMS else None
        speak_on_bus(mixer, tts, layer.payload, bus=layer.bus, voice_id=voice_id)
        return True

    cue = InterdictionCue.from_cfg(cfg, emit)
    try:
        print("Firing a fake Interdiction...")
        cue.on_event({"event": "Interdiction", "IsPlayer": False, "Interdictor": "Pirate"})
        time.sleep(6.0)
    finally:
        mixer.stop()
        print("done.")


if __name__ == "__main__":
    main()
