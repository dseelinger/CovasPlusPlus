"""Local TTS provider — Piper (offline neural TTS).

Runs entirely on the machine, no per-character cost. Streams synthesis chunk by
chunk into a low-latency PCM output stream so it stays promptly cancellable,
mirroring the ElevenLabs path. Emits the same raw 16-bit mono PCM shape, so the
rest of the audio/cancel code is unchanged — only the sample rate differs
(Piper medium voices are typically 22.05 kHz vs ElevenLabs' 16 kHz).

Voice models: download on your machine, e.g.
    python -m piper.download_voices en_US-lessac-medium
then point [piper].model at the resulting .onnx (its .onnx.json sits beside it).
"""
from __future__ import annotations

import threading
from pathlib import Path

import sounddevice as sd


def list_piper_voices(voices_dir: str) -> list[dict]:
    """Scan `voices_dir` for local Piper voices (issue #120): every ``*.onnx`` that has its sibling
    ``*.onnx.json`` config beside it, as ``[{value: <onnx path>, label: <filename>}]`` sorted by
    filename. PURE filesystem read (no `piper` import needed) and FAIL-SOFT: a blank / missing /
    unreadable dir returns ``[]`` so the settings picker degrades to the type-a-path escape hatch.
    Unit-tested offline against a temp dir."""
    out: list[dict] = []
    try:
        if not voices_dir:
            return []
        d = Path(voices_dir)
        if not d.is_dir():
            return []
        for onnx in sorted(d.glob("*.onnx")):
            if onnx.with_name(onnx.name + ".json").exists():  # <name>.onnx.json sits beside it
                out.append({"value": str(onnx), "label": onnx.name})
    except Exception:  # noqa: BLE001 — a catalog scan must never raise (CLAUDE.md fail-soft)
        return []
    return out


class PiperTTS:
    def __init__(self, cfg: dict, *, mixer=None, bus: str = "covas") -> None:  # noqa: ANN001
        # Import lazily so the cloud stack doesn't need piper installed.
        from piper import PiperVoice

        self._cfg = cfg
        self._mixer = mixer
        self._bus = bus
        p = cfg.get("piper", {})
        model = str(p.get("model", "")).strip()
        if not model:
            raise RuntimeError(
                "Piper TTS selected but [piper].model is empty. Download a voice "
                "(python -m piper.download_voices en_US-lessac-medium) and set "
                "[piper].model to its .onnx path."
            )
        if not Path(model).exists():
            raise RuntimeError(f"Piper voice not found: {model}")
        # config_path defaults to '<model>.json' beside the .onnx.
        self.voice = PiperVoice.load(model)
        self.sample_rate = int(getattr(self.voice.config, "sample_rate", 22050))
        # The voice's own default length_scale (usually 1.0) — we scale it by 1/speed (#99) so a
        # normal speed leaves the voice exactly as configured.
        self._base_length_scale = float(getattr(self.voice.config, "length_scale", 1.0) or 1.0)
        self._out_device = cfg.get("audio", {}).get("tts_output_device") or None

    def _synth(self, text):  # noqa: ANN001, ANN202
        """Synthesize `text`, applying the normalized voice speed (#99) via Piper's `length_scale`
        (the INVERSE of speed — larger = slower). At normal speed we call `synthesize(text)`
        untouched, so the default path is byte-for-byte unchanged. Read per-call so a live speed
        change applies to the next line. Fails soft to the plain call if this Piper build lacks
        `SynthesisConfig`."""
        from .. import tts_speed
        n = tts_speed.normalized_speed(self._cfg)
        if tts_speed.is_default(n):
            return self.voice.synthesize(text)
        try:
            from piper import SynthesisConfig
            length_scale = tts_speed.piper_length_scale(n, self._base_length_scale)
            return self.voice.synthesize(text, syn_config=SynthesisConfig(length_scale=length_scale))
        except (ImportError, TypeError):  # older piper w/o SynthesisConfig / syn_config kwarg
            return self.voice.synthesize(text)

    def synth_pcm(self, text: str, voice_id: str | None = None) -> tuple[bytes, int]:
        # Piper loads a single voice model; `voice_id` (a cloud concept) is ignored here.
        buf = bytearray()
        for chunk in self._synth(text):
            buf += chunk.audio_int16_bytes
        return bytes(buf), self.sample_rate

    def speak(self, text: str, cancel: threading.Event) -> None:
        text = text.strip()
        if not text:
            return
        if self._mixer is not None:
            self._speak_via_mixer(text, cancel)
            return
        stream = sd.RawOutputStream(
            samplerate=self.sample_rate, channels=1, dtype="int16",
            device=self._out_device,
        )
        stream.start()
        cancelled = False
        try:
            for chunk in self._synth(text):
                if cancel.is_set():
                    cancelled = True
                    break
                stream.write(chunk.audio_int16_bytes)
        finally:
            if cancelled:
                stream.abort()   # drop buffered audio -> stops immediately
            else:
                stream.stop()
            stream.close()

    def _speak_via_mixer(self, text: str, cancel: threading.Event) -> None:
        """Feed Piper's synthesized chunks into the shared BusMixer (C9), same barge-in +
        drain-until-done semantics as the direct path."""
        sink = self._mixer.open_speech(self._bus, self.sample_rate)
        cancelled = False
        try:
            for chunk in self._synth(text):
                if cancel.is_set():
                    cancelled = True
                    break
                sink.feed(chunk.audio_int16_bytes)
        finally:
            if cancelled:
                sink.cancel()
            else:
                sink.finish()
                while not sink.wait(0.1):
                    if cancel.is_set():
                        sink.cancel()
                        break
