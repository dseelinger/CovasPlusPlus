"""Manual check for the C5 comms variant pipeline: hear three canned ReceiveText lines voiced
through the Comms bus (radio-treated) — a safe NPC riff, a TAMPERED variant that falls back to
verbatim, and a player DM read verbatim.

    .venv\\Scripts\\python.exe scripts\\demo_comms_variants.py

Uses the configured TTS provider + real audio device (ElevenLabs key or a Piper voice needed).
No LLM cost: the "generator" here is a canned stub so the fallback is deterministic. Set voices
in [audio.comms.voices] to hear male/female/default differ.
"""
from __future__ import annotations

import time

from covas.config import load_config
from covas.mixer import COMMS, BusMixer, CommsVoicer, comms_voice_id, evaluate, speak_on_bus
from covas.providers.factory import make_tts


def _rt(channel, msg, from_localised):
    return {"event": "ReceiveText", "From": from_localised, "From_Localised": from_localised,
            "Message": msg, "Message_Localised": msg, "Channel": channel}


# A canned "LLM": a safe riff for the clean line, a tampered variant (new name + threat) for the
# second — so you can hear the validator reject it and fall back to the verbatim source.
_CANNED = {
    "Docking request granted, proceed to pad 7.": "You're cleared in, proceed to pad 7.",
    "Cargo scan complete. You're clean.": "Scan's done at Ackerman — prepare to be destroyed.",
}


def main() -> None:
    cfg = load_config()
    tts = make_tts(cfg)
    mixer = BusMixer(cfg)
    mixer.start()

    def play(text: str, record) -> bool:   # C10: play receives the whole record
        speak_on_bus(mixer, tts, text, bus=COMMS, voice_id=comms_voice_id(cfg, record.voice))
        return True

    voicer = CommsVoicer(play, generate=lambda src, tier: _CANNED.get(src, src))

    lines = [
        ("NPC, clean -> safe riff", _rt("npc", "Docking request granted, proceed to pad 7.", "Station Control")),
        ("NPC, tampered -> verbatim fallback", _rt("npc", "Cargo scan complete. You're clean.", "Security")),
        ("Player DM -> verbatim, male", _rt("player", "Hey, need backup at Sol.", "CMDR Ada")),
    ]
    try:
        for label, ev in lines:
            rec = evaluate(ev)
            print(f"\n{label}")
            out = voicer.voice(rec)
            print(f"  tier={out.tier} voice={out.voice} spoken={out.spoken}\n  said: {out.text!r}\n  ({out.reason})")
            time.sleep(3.5)
    finally:
        mixer.stop()
        print("\ndone.")


if __name__ == "__main__":
    main()
