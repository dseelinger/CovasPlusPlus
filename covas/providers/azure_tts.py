"""Official Azure Neural TTS provider (issue #17).

The **reliable sibling** of the Edge provider (`edge_tts.py`): the SAME Azure Neural voices, but over
the official Speech service — a real API, an SLA, and a **free monthly tier** (~0.5M chars/mo) — so it
is the shippable way to give the NPC/comms cast large voice variety at low/zero cost, WITHOUT Edge's
undocumented/no-SLA asterisk. Registered as a cast-eligible backend on the #14 registry and selectable
as `[tts].provider = "azure"` for the persona.

Uses the Speech REST endpoint via `requests` (already a dep — no Azure SDK needed). We request
`raw-24khz-16bit-mono-pcm`, so the response IS the raw 16-bit mono PCM the audio/cancel path already
expects (no decode step, unlike Edge's MP3). Streaming with prompt cancellation keeps tap-cancel and
barge-in snappy, mirroring the ElevenLabs/Piper paths; the mixer path mirrors Piper's.

Needs a key + region (`[azure].api_key_file`, DPAPI-encrypted; `[azure].region`). Fail soft: with no key
or a service error the persona degrades to text and cast voices fall silent — never crashes the loop.
"""
from __future__ import annotations

import threading
from typing import Optional
from xml.sax.saxutils import escape as _xml_escape

import requests

# We always request headerless raw PCM at 24 kHz — the shape the rest of the pipeline uses.
_OUTPUT_FORMAT = "raw-24khz-16bit-mono-pcm"
_SAMPLE_RATE = 24000
_USER_AGENT = "COVAS-Plus-Plus/0.1 (Elite Dangerous voice companion)"
_DEFAULT_VOICE = "en-US-AriaNeural"
_DEFAULT_REGION = "eastus"
# Playback chunk: ~85 ms at 24 kHz (2048 samples * 2 bytes) — small enough for a snappy cancel.
_PLAY_CHUNK = 4096


class AzureTTS:
    """TTSProvider over Azure Neural TTS. `voice` is an Azure voice ShortName (e.g.
    'en-US-AriaNeural'); optional `style` applies an SSML `mstts:express-as` speaking style
    (voice-dependent, e.g. 'cheerful', 'newscast'). Key + region come from config/env."""

    def __init__(self, cfg: dict, *, mixer=None, bus: str = "covas") -> None:  # noqa: ANN001
        self._cfg = cfg
        self._mixer = mixer
        self._bus = bus
        a = cfg.get("azure", {}) or {}
        self._region = str(a.get("region", "")).strip() or _DEFAULT_REGION
        self._voice = str(a.get("voice", "")).strip() or _DEFAULT_VOICE
        self._style = str(a.get("style", "")).strip()
        self._out_device = cfg.get("audio", {}).get("tts_output_device") or None

    def _rate(self) -> str | None:
        """The SSML prosody `rate` for the current normalized `[tts].speed` (issue #99), or None at
        normal speed so no `<prosody>` wrapper is added. Read per-call so a live speed change applies
        to the next line."""
        from .. import tts_speed
        n = tts_speed.normalized_speed(self._cfg)
        return None if tts_speed.is_default(n) else tts_speed.azure_rate(n)

    def _key(self) -> str:
        """Resolve the Azure Speech key from its (DPAPI-encrypted) key file. Raises a clear error if
        unconfigured — callers fail soft (persona -> text, cast -> silence)."""
        from ..firstrun import azure_key
        key = azure_key(self._cfg)
        if not key:
            raise RuntimeError(
                "Azure TTS selected but no key found (add it in Settings, or to [azure].api_key_file)."
            )
        return key

    # ---- synthesis --------------------------------------------------------
    def synth_pcm(self, text: str, voice_id: str | None = None) -> tuple[bytes, int]:
        """Synthesize `text` in Azure voice `voice_id` (ShortName; None/'' = the configured voice)
        to raw 16-bit mono PCM. Raises on a service/config error (CastSynth catches it and a dead
        voice degrades to silence; the persona path degrades to text)."""
        text = (text or "").strip()
        if not text:
            return b"", _SAMPLE_RATE
        voice = (voice_id or "").strip() or self._voice
        ssml = _build_ssml(text, voice, self._style, self._rate())
        pcm, _ = _collect_pcm(self._key(), self._region, ssml, None)
        if not pcm:
            raise RuntimeError("Azure TTS returned no audio")
        return pcm, _SAMPLE_RATE

    def speak(self, text: str, cancel: threading.Event) -> None:
        """Synthesize + play `text`, stopping promptly if `cancel` is set. Re-raises on a
        service/config error so the caller degrades to text — never crashes the loop."""
        text = (text or "").strip()
        if not text:
            return
        ssml = _build_ssml(text, self._voice, self._style, self._rate())
        pcm, cancelled = _collect_pcm(self._key(), self._region, ssml, cancel)
        if cancelled or not pcm:
            return  # barged in during synth, or no audio -> nothing to play
        if self._mixer is not None:
            self._play_via_mixer(pcm, _SAMPLE_RATE, cancel)
        else:
            self._play_direct(pcm, _SAMPLE_RATE, cancel)

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
        """Feed the PCM into the shared BusMixer (C9): same barge-in + drain-until-done as Piper."""
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
        """The Azure voice catalog for cast assignment, filtered to `locale_prefix` (blank = all).
        Each entry: {'ref': ShortName, 'name': DisplayName, 'gender': 'male'|'female'|'neutral',
        'locale': Locale}. Fails soft to [] when unreachable/unconfigured."""
        try:
            return list_azure_voices(self._key(), self._region, locale_prefix)
        except Exception:  # noqa: BLE001 — no catalog if the service is unreachable/unconfigured
            return []


# ---- module helpers (pure builders; the network lives in the two request fns) -------------
def _lang_of(voice: str) -> str:
    """Best-effort BCP-47 language tag from a voice ShortName ('en-US-AriaNeural' -> 'en-US')."""
    parts = str(voice or "").split("-")
    return f"{parts[0]}-{parts[1]}" if len(parts) >= 2 else "en-US"


def _build_ssml(text: str, voice: str, style: str = "", rate: str | None = None) -> str:
    """Build the SSML request body. XML-escapes `text`; when `style` is set, wraps it in an
    `mstts:express-as` block (voice-dependent speaking style/emotion); when `rate` is set (e.g.
    '+30%' / '-20%', from the normalized voice speed #99), wraps it in a `<prosody rate=…>` block
    (Azure treats the percent as a signed-relative change). Pure — no I/O."""
    inner = _xml_escape(text)
    if style:
        inner = (f"<mstts:express-as style='{_xml_escape(style)}'>{inner}</mstts:express-as>")
    if rate:
        inner = f"<prosody rate='{_xml_escape(rate)}'>{inner}</prosody>"
    return (
        "<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' "
        "xmlns:mstts='https://www.w3.org/2001/mstts' "
        f"xml:lang='{_lang_of(voice)}'>"
        f"<voice name='{_xml_escape(voice)}'>{inner}</voice></speak>"
    )


def _collect_pcm(key: str, region: str, ssml: str,
                 cancel: Optional[threading.Event], *, timeout: float = 30.0) -> tuple[bytes, bool]:
    """POST the SSML to the Speech endpoint and stream the raw PCM back, returning (pcm, cancelled).
    Checks `cancel` between chunks so a barge-in stops the read promptly; a partial (cancelled)
    buffer is discarded by callers. Raises RuntimeError on a non-200 response."""
    url = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
    headers = {
        "Ocp-Apim-Subscription-Key": key,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": _OUTPUT_FORMAT,
        "User-Agent": _USER_AGENT,
    }
    buf = bytearray()
    with requests.post(url, data=ssml.encode("utf-8"), headers=headers,
                       stream=True, timeout=timeout) as r:
        if r.status_code != 200:
            raise RuntimeError(f"Azure TTS {r.status_code}: {r.text[:200]}")
        for chunk in r.iter_content(chunk_size=_PLAY_CHUNK):
            if cancel is not None and cancel.is_set():
                return bytes(buf), True
            if chunk:
                buf += chunk
    return bytes(buf), False


def _gender(raw: str) -> str:
    """Map an Azure Gender string to the cast's 'male'/'female'/'neutral' vocabulary."""
    g = str(raw or "").strip().lower()
    return g if g in ("male", "female") else "neutral"


def _normalize_voices(raw: list[dict], locale_prefix: str = "en-") -> list[dict]:
    """Pure: filter + normalize an Azure `voices/list` payload to the cast shape. Split out from the
    network so it's unit-testable. Filtered to `locale_prefix` (blank = all), sorted by ShortName."""
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
            "name": str(v.get("DisplayName") or v.get("LocalName") or short).strip() or short,
            "gender": _gender(v.get("Gender", "")),
            "locale": locale,
        })
    out.sort(key=lambda d: d["ref"])
    return out


def list_azure_voices(key: str, region: str, locale_prefix: str = "en-",
                      *, timeout: float = 15.0) -> list[dict]:
    """Fetch (network) + normalize the Azure voice catalog. See _normalize_voices / list_voices."""
    url = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/voices/list"
    headers = {"Ocp-Apim-Subscription-Key": key, "User-Agent": _USER_AGENT}
    r = requests.get(url, headers=headers, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"Azure voices/list {r.status_code}: {r.text[:200]}")
    return _normalize_voices(r.json(), locale_prefix)
