"""Free neural TTS provider — Edge "Read Aloud" (edge-tts).

Streams Microsoft Edge's Read-Aloud Azure Neural voices: hundreds of distinct voices, NO API
key. Ideal for the NPC/comms CAST — ambient chatter never burns ElevenLabs credits (issue #15).

⚠ CAVEAT (surfaced in docs/config): `edge-tts` rides an UNDOCUMENTED endpoint Microsoft intends
for the Edge browser. It is ToS-gray, has NO SLA, and periodically breaks when Microsoft rotates
its anti-abuse tokens. So this ships as an OPTIONAL, clearly-labeled provider — never a load-bearing
default. **Piper** stays the guaranteed free floor (`fallback=`), and official **Azure Neural TTS**
(a separate issue: the SAME voices with an API + SLA + free tier) is the version to actually depend
on. When the endpoint is down, this provider fails soft to the injected fallback (Piper), and to
text if there's no fallback — it never crashes the voice loop.

edge-tts returns MP3; soundfile (libsndfile ≥1.1) decodes it to raw 16-bit mono PCM so the rest of
the audio/cancel path is unchanged (edge's default stream is 24 kHz). Cancellation is honored both
while synthesizing (stop pulling chunks) and while playing (drop buffered audio) so tap-cancel and
barge-in stay snappy, mirroring the ElevenLabs/Piper paths.
"""
from __future__ import annotations

import asyncio
import io
import threading
from typing import Optional

# Default persona/status voice when [edge].voice is blank. A neutral US English neural voice.
_DEFAULT_VOICE = "en-US-AriaNeural"
# Playback chunk: ~85 ms at 24 kHz (2048 samples * 2 bytes) — small enough for a snappy cancel.
_PLAY_CHUNK = 4096


class EdgeTTS:
    """TTSProvider over edge-tts. `voice` is an Edge voice ShortName (e.g. 'en-US-AriaNeural').
    `fallback` (a TTSProvider, typically Piper) is used verbatim when the endpoint is unavailable —
    that's the guaranteed-free floor the issue calls for. Import of edge-tts is LAZY (constructor
    only checks it's importable) so the default offline test run never pulls the SDK unless asked."""

    def __init__(self, cfg: dict, *, mixer=None, bus: str = "covas",  # noqa: ANN001
                 fallback: Optional[object] = None) -> None:
        # Import lazily so the rest of the stack doesn't require edge-tts installed.
        import edge_tts  # noqa: F401  (import-check only; used in the helpers below)

        self._cfg = cfg
        self._mixer = mixer
        self._bus = bus
        self._fallback = fallback
        e = cfg.get("edge", {}) or {}
        self._voice = str(e.get("voice", "")).strip() or _DEFAULT_VOICE
        self._out_device = cfg.get("audio", {}).get("tts_output_device") or None

    def _rate(self) -> str | None:
        """The edge-tts `rate` string for the current normalized `[tts].speed` (issue #99), or
        None at normal speed so the request stays the library default. Read per-call so a live
        speed change (settings/voice) applies to the next line."""
        from .. import tts_speed
        n = tts_speed.normalized_speed(self._cfg)
        return None if tts_speed.is_default(n) else tts_speed.edge_rate(n)

    # ---- synthesis --------------------------------------------------------
    def synth_pcm(self, text: str, voice_id: str | None = None) -> tuple[bytes, int]:
        """Synthesize `text` in Edge voice `voice_id` (ShortName; None/'' = the configured voice)
        to raw 16-bit mono PCM. Falls back to the injected provider on any endpoint error; with no
        fallback the error propagates (CastSynth catches it and a dead voice degrades to silence)."""
        text = (text or "").strip()
        if not text:
            return b"", 24000
        voice = (voice_id or "").strip() or self._voice
        try:
            mp3, _ = _collect_mp3(text, voice, None, self._rate())
            if not mp3:
                raise RuntimeError("edge-tts returned no audio (endpoint may be blocked)")
            return _decode_pcm(mp3)
        except Exception:  # noqa: BLE001 — endpoint down / token rotation: fail soft
            if self._fallback is not None:
                return self._fallback.synth_pcm(text, None)  # fallback is a single-voice floor
            raise

    def speak(self, text: str, cancel: threading.Event) -> None:
        """Synthesize + play `text`, stopping promptly if `cancel` is set. Fails soft to the
        injected fallback (Piper) when the endpoint is unavailable, else re-raises so the caller
        degrades to text — never crashes the loop."""
        text = (text or "").strip()
        if not text:
            return
        try:
            mp3, cancelled = _collect_mp3(text, self._voice, cancel, self._rate())
            if cancelled or not mp3:
                return  # barged in during synth, or no audio -> nothing to play
            pcm, sr = _decode_pcm(mp3)
        except Exception:  # noqa: BLE001 — endpoint down: fall back to the guaranteed floor
            if self._fallback is not None:
                self._fallback.speak(text, cancel)
                return
            raise
        if self._mixer is not None:
            self._play_via_mixer(pcm, sr, cancel)
        else:
            self._play_direct(pcm, sr, cancel)

    # ---- playback ---------------------------------------------------------
    def _play_direct(self, pcm: bytes, sr: int, cancel: threading.Event) -> None:
        import sounddevice as sd

        stream = sd.RawOutputStream(samplerate=sr, channels=1, dtype="int16",
                                    device=self._out_device)
        stream.start()
        cancelled = False
        try:
            for i in range(0, len(pcm), _PLAY_CHUNK):
                if cancel.is_set():
                    cancelled = True
                    break
                stream.write(pcm[i:i + _PLAY_CHUNK])
        finally:
            if cancelled:
                stream.abort()   # drop buffered audio -> stops immediately
            else:
                stream.stop()
            stream.close()

    def _play_via_mixer(self, pcm: bytes, sr: int, cancel: threading.Event) -> None:
        """Feed the decoded PCM into the shared BusMixer (C9): same barge-in + drain-until-done
        semantics as Piper's mixer path."""
        sink = self._mixer.open_speech(self._bus, sr)
        cancelled = False
        try:
            for i in range(0, len(pcm), _PLAY_CHUNK):
                if cancel.is_set():
                    cancelled = True
                    break
                sink.feed(pcm[i:i + _PLAY_CHUNK])
        finally:
            if cancelled:
                sink.cancel()
            else:
                sink.finish()
                while not sink.wait(0.1):
                    if cancel.is_set():
                        sink.cancel()
                        break

    # ---- voice catalog ----------------------------------------------------
    def list_voices(self, locale_prefix: str = "en-") -> list[dict]:
        """The Edge voice catalog for cast assignment, filtered to `locale_prefix` (blank = all).
        Each entry: {'ref': ShortName, 'name': FriendlyName, 'gender': 'male'|'female'|'neutral',
        'locale': Locale}. Fails soft to [] when the endpoint is unreachable."""
        try:
            return list_edge_voices(locale_prefix)
        except Exception:  # noqa: BLE001 — no catalog if the endpoint is unreachable
            return []


# ---- module helpers (pure-ish; the network lives here) --------------------
def _collect_mp3(text: str, voice: str,
                 cancel: Optional[threading.Event],
                 rate: Optional[str] = None) -> tuple[bytes, bool]:
    """Drive edge-tts to completion, returning (mp3_bytes, cancelled). Pulls audio chunks in a
    fresh event loop (the app is thread-based, no running loop), checking `cancel` between chunks
    so a barge-in stops synthesis promptly. A partial (cancelled) buffer is discarded by callers.
    `rate` (e.g. '+30%' / '-20%') applies the normalized voice speed (#99); None = the default pace."""
    import edge_tts

    async def _run() -> tuple[bytes, bool]:
        buf = bytearray()
        comm = (edge_tts.Communicate(text, voice, rate=rate) if rate
                else edge_tts.Communicate(text, voice))
        async for chunk in comm.stream():
            if cancel is not None and cancel.is_set():
                return bytes(buf), True
            if chunk.get("type") == "audio" and chunk.get("data"):
                buf += chunk["data"]
        return bytes(buf), False

    return asyncio.run(_run())


def _decode_pcm(mp3: bytes) -> tuple[bytes, int]:
    """Decode MP3 bytes to (raw 16-bit mono PCM, sample_rate) via soundfile/libsndfile."""
    import soundfile as sf

    data, sr = sf.read(io.BytesIO(mp3), dtype="int16")
    if getattr(data, "ndim", 1) > 1:   # collapse any stereo to mono (edge is mono, but be safe)
        data = data[:, 0]
    return data.tobytes(), int(sr)


def _gender(raw: str) -> str:
    """Map an Edge Gender string to the cast's 'male'/'female'/'neutral' vocabulary."""
    g = str(raw or "").strip().lower()
    return g if g in ("male", "female") else "neutral"


def _normalize_voices(raw: list[dict], locale_prefix: str = "en-") -> list[dict]:
    """Pure: filter + normalize an Edge catalog to the cast shape. Split out from the network so
    it's unit-testable with no event loop. Filtered to `locale_prefix` (blank = all), sorted by
    ShortName for a stable, deterministic pool."""
    out: list[dict] = []
    for v in raw or []:
        short = str((v or {}).get("ShortName", "")).strip()
        if not short:
            continue
        locale = str(v.get("Locale", "")).strip()
        if locale_prefix and not locale.startswith(locale_prefix):
            continue
        out.append({
            "ref": short,
            "name": str(v.get("FriendlyName", short)).strip() or short,
            "gender": _gender(v.get("Gender", "")),
            "locale": locale,
        })
    out.sort(key=lambda d: d["ref"])
    return out


def list_edge_voices(locale_prefix: str = "en-") -> list[dict]:
    """Fetch (network) + normalize the Edge voice catalog. See _normalize_voices / EdgeTTS.list_voices."""
    import edge_tts

    raw = asyncio.run(edge_tts.list_voices())
    return _normalize_voices(raw, locale_prefix)
