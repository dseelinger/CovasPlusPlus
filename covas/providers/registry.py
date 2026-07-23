"""TTS provider registry + per-role provider resolution (issue #14).

The single seam every voice-CAST TTS backend plugs into. Today ElevenLabs and Piper register
here; the Edge/OpenAI/Azure/Cartesia providers (#15–#18) are drop-ins — each registers a backend
under its name and instantly becomes castable for any role, with no change to `CastSynth`.

A *backend* is a callable `(text, ref) -> (pcm, sr)`: raw 16-bit mono PCM bytes + sample rate,
synthesizing `text` in voice `ref` (an ElevenLabs voice_id, a Piper .onnx path, an Edge voice
name, …; `''` = the provider's default voice). This mirrors the `synth_pcm(text, voice_id)` shape
on `TTSProvider` (base.py), so a provider's own `synth_pcm` adapts to a backend in one line.

`resolve_provider(cfg, role)` picks which provider a cast ROLE uses: a per-role override in
`[audio.voices.providers]`, else the cast umbrella `[audio.voices].cast_provider`. Persona/status
speech is NOT a cast role — it goes through the full `TTSProvider` (`[tts].provider`, built by
`make_tts`), because it needs streaming + prompt cancellation, not just PCM.

Pure and provider-free: importing this pulls in no SDKs. Registration happens at the app
composition root; tests register fakes. This establishes the voice ladder (Piper free/local →
cheap cloud → premium) that mirrors the LLM cost router.
"""
from __future__ import annotations

from collections.abc import Callable

# A cast synth backend: (text, ref) -> (pcm_bytes, sample_rate). ref '' = the provider default voice.
TTSBackend = Callable[[str, str], "tuple[bytes, int]"]

# Cast roles that resolve a provider. Persona/status speech is not here — it uses [tts].provider.
# "cast" is the umbrella role build_cast() resolves for the pool's default provider. The fleet-
# carrier context voices (issue #19) add "captain" and "tower" (carrier chatter reuses "chatter").
CAST_ROLES = ("cast", "comms", "chatter", "player", "interdiction", "captain", "tower")

_DEFAULT_PROVIDER = "elevenlabs"


class TTSProviderRegistry:
    """Maps a provider name -> a PCM synth backend. Names are lower-cased. A provider that isn't
    registered (no key, SDK missing, disabled) simply isn't present — `CastSynth` degrades a voice
    on that provider to silence rather than raising into the loop."""

    def __init__(self) -> None:
        self._backends: dict[str, TTSBackend] = {}

    def register(self, name: str, backend: TTSBackend) -> TTSProviderRegistry:
        """Register (or replace) a provider's backend. Returns self so registrations can chain."""
        self._backends[str(name).lower()] = backend
        return self

    def has(self, name: str) -> bool:
        return str(name).lower() in self._backends

    def names(self) -> list[str]:
        return sorted(self._backends)

    def synth(self, provider: str, text: str, ref: str = "") -> tuple[bytes, int]:
        """Synthesize `text` in `provider`'s voice `ref`. Raises KeyError if the provider isn't
        registered — callers (CastSynth) catch it and fall back to silence."""
        return self._backends[str(provider).lower()](text, ref or "")


def resolve_provider(cfg: dict, role: str, *, default: str | None = None) -> str:
    """Which TTS provider a cast ROLE should use: a `[audio.voices.providers].<role>` override if
    set, else `default`, else the cast umbrella `[audio.voices].cast_provider` (`elevenlabs` if
    unset). Always lower-cased. Pure — no I/O. Persona/status is not a cast role; use
    `[tts].provider` for it."""
    voices = (cfg.get("audio", {}) or {}).get("voices", {}) or {}
    overrides = voices.get("providers", {}) or {}
    val = overrides.get(role)
    if val:
        return str(val).lower()
    if default is not None:
        return str(default).lower()
    return str(voices.get("cast_provider", _DEFAULT_PROVIDER)).lower()
