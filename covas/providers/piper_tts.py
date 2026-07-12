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


class PiperTTS:
    def __init__(self, cfg: dict, *, mixer=None, bus: str = "covas") -> None:  # noqa: ANN001
        # Import lazily so the cloud stack doesn't need piper installed.
        from piper import PiperVoice

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
        self._out_device = cfg.get("audio", {}).get("tts_output_device") or None

    def synth_pcm(self, text: str, voice_id: str | None = None) -> tuple[bytes, int]:
        # Piper loads a single voice model; `voice_id` (a cloud concept) is ignored here.
        buf = bytearray()
        for chunk in self.voice.synthesize(text):
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
            for chunk in self.voice.synthesize(text):
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
            for chunk in self.voice.synthesize(text):
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
