"""Manual check for the C5 comms variant pipeline: hear three canned ReceiveText lines voiced
through the Comms bus (radio-treated) — a safe NPC riff, a TAMPERED variant that falls back to
verbatim, and a player DM read verbatim.

    .venv\\Scripts\\python.exe scripts\\demo_comms_variants.py

Uses the configured TTS provider + real audio device (ElevenLabs key or a Piper voice needed).
No LLM cost: the "generator" here is a canned stub so the fallback is deterministic. The speaker
voice is cast by identity through the C10 voice cast (a random-but-sticky pool voice), so a given
NPC keeps its voice across lines; configure the pool in [audio.voices].
"""
from __future__ import annotations

import time

from covas.config import load_config
from covas.mixer import (
    COMMS,
    BusMixer,
    CommsVoicer,
    build_cast,
    evaluate,
    pcm16_to_float,
)
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

    # C10 voice cast: route each chosen Voice through the configured TTS provider. With no pool in
    # [audio.voices] this degrades to the single provider/persona voice — same as the live app.
    cast = build_cast(cfg, synth=lambda voice, text: tts.synth_pcm(text, voice.ref or None))

    def play(text: str, record) -> bool:   # C10: play receives the whole record, casts by identity
        pcm, sr = cast.synth(cast.for_record(record), text)
        mixer.submit(COMMS, pcm16_to_float(pcm), sr)
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
