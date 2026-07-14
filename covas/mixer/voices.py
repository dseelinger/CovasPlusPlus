"""Voice cast (C10) — deterministic identity->voice assignment + provider routing.

Everything the audio layer speaks gets a voice from a configurable POOL. `VoiceCast.assign()` is
DETERMINISTIC (a stable identity -> a stable pool voice) and still backs the interdiction cue; the
comms/chatter/player paths in the AudioLayer layer a random-but-sticky memory over the same pool
(see `voice_memory.StickyVoicePool`). Routing keeps COVAS on the ElevenLabs persona voice while the
NPC/comms/chatter CAST now defaults to ElevenLabs too (random voices per the user's request) — set
`[audio.voices].cast_provider = "piper"` (with local .onnx pool entries) to go back to the free,
game-friendly local path.

Three pieces, split so the assignment is pure/offline-testable and only real synthesis needs
providers:
  * `Voice` — (provider, ref, gender). `VoiceCast` — the pure pool + `assign()` + `for_record()`.
  * `build_cast()` — builds the pool from [audio.voices], applying the exclusion hook so an
    unusable voice (an ElevenLabs 'famous'/™ voice) can never be selected — in the pool OR picker.
    When no pool is configured and `random_el` is on, it seeds a RANDOM default pool from the live
    ElevenLabs library (minus the persona voice) so the cast has many distinct voices out of the box.
  * `CastSynth` — routes a chosen Voice to the right provider (ElevenLabs voice_id / a cached
    Piper model) to produce PCM. Injected, so the default test run never loads a model or hits
    the network. A synth failure fails soft to silence.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Callable, Optional

EL = "elevenlabs"
PIPER = "piper"


def _stable_hash(s: str) -> int:
    """Process-stable hash of an identity string (Python's built-in hash() is salted per run, so
    it can't give a consistent voice across sessions). SHA-1 of the bytes, first 8 as an int."""
    return int.from_bytes(hashlib.sha1(s.encode("utf-8")).digest()[:8], "big")


@dataclass(frozen=True)
class Voice:
    """One castable voice. `ref` is an ElevenLabs voice_id or a Piper .onnx model path ('' = the
    provider's default voice). `gender` lets a gendered NPC honorific map to a matching voice."""

    provider: str
    ref: str = ""
    gender: str = "neutral"


class VoiceCast:
    """Pure assignment over a voice pool. Construct with the pool + the persona (COVAS) and player
    (DM) voices + an injected `synth`. Deterministic: same identity -> same voice."""

    def __init__(self, pool, *, persona: Voice, player: Voice,
                 cast_provider: str = EL,
                 synth: Optional[Callable[[Voice, str], tuple[bytes, int]]] = None) -> None:
        self._pool = list(pool)
        self._persona = persona
        self._player = player
        self._cast_provider = cast_provider
        self._synth = synth or (lambda _v, _t: (b"", 16000))

    @property
    def pool(self) -> list[Voice]:
        return list(self._pool)

    def persona(self) -> Voice:
        """COVAS's own voice (ElevenLabs persona) — used for the assistant's lines."""
        return self._persona

    def player(self) -> Voice:
        """The fixed voice for direct player DMs (C4: a real human, read verbatim)."""
        return self._player

    def assign(self, identity: str, gender_hint: Optional[str] = None) -> Voice:
        """Deterministically map an identity to a pool voice. A male/female `gender_hint` narrows
        to matching voices when the pool has any; an empty pool degrades to the persona voice
        (i.e. today's single-voice behaviour). Same identity -> same voice, always."""
        candidates = self._pool
        if not candidates:
            return self._persona
        if gender_hint in ("male", "female"):
            gendered = [v for v in candidates if v.gender == gender_hint]
            if gendered:
                candidates = gendered
        return candidates[_stable_hash(str(identity or "")) % len(candidates)]

    def for_record(self, record) -> Voice:  # noqa: ANN001 — a VoiceableComms
        """The voice for a gated comms record: player DMs get the fixed player voice; NPC and
        ambiguous lines are assigned by the SENDER identity, narrowed by the C4 logical voice."""
        if getattr(record, "kind", "") == "player":
            return self._player
        hint = getattr(record, "voice", "") or ""
        if hint not in ("male", "female"):
            hint = None
        identity = getattr(record, "sender", "") or getattr(record, "channel", "") or "npc"
        return self.assign(identity, gender_hint=hint)

    def synth(self, voice: Voice, text: str) -> tuple[bytes, int]:
        return self._synth(voice, text)


def build_cast(
    cfg: dict,
    *,
    synth: Optional[Callable[[Voice, str], tuple[bytes, int]]] = None,
    el_voices: Optional[list[dict]] = None,
    exclude: Optional[Callable[[Voice], bool]] = None,
) -> VoiceCast:
    """Build the cast from [audio.voices]. The EXCLUSION HOOK: an ElevenLabs voice not in the
    allowed `el_voices` list (which already filters 'famous'/™ voices at elevenlabs.list_voices)
    is dropped, as is anything the injected `exclude` predicate flags — so an unusable voice can
    never be selected. COVAS's persona is [elevenlabs].voice_id; the player DM voice is
    [audio.voices].player_ref, else the first male pool voice, else the persona.

    RANDOM DEFAULT POOL: when no pool is configured in [audio.voices].pool and `random_el` is on
    (the default) and the live `el_voices` list is available, the pool is seeded from the whole
    ElevenLabs library MINUS the persona voice — so the comms/chatter/player cast has many
    distinct random voices with zero configuration, and always sounds like someone other than COVAS."""
    v = (cfg.get("audio", {}) or {}).get("voices", {}) or {}
    cast_provider = str(v.get("cast_provider", EL)).lower()
    allowed_el = ({x["voice_id"] for x in el_voices} if el_voices is not None else None)
    extra = exclude or (lambda _voice: False)

    def _excluded(voice: Voice) -> bool:
        if voice.provider == EL and allowed_el is not None and voice.ref:
            if voice.ref not in allowed_el:
                return True
        return bool(extra(voice))

    persona = Voice(EL, str((cfg.get("elevenlabs", {}) or {}).get("voice_id", "")).strip(),
                    "neutral")

    pool: list[Voice] = []
    for e in (v.get("pool", []) or []):
        if not isinstance(e, dict):
            continue
        voice = Voice(str(e.get("provider", cast_provider)).lower(),
                      str(e.get("ref", "")).strip(),
                      str(e.get("gender", "neutral")).lower())
        if voice.ref and not _excluded(voice):
            pool.append(voice)

    # No configured pool -> seed a random default pool from the live EL library (minus the persona
    # and the injected excludes). Only possible once el_voices has been fetched; until then the
    # pool is empty and the cast degrades to the single persona voice (see `assign`).
    random_el = bool(v.get("random_el", True))
    if not pool and random_el and el_voices is not None:
        for e in el_voices:
            vid = str((e or {}).get("voice_id", "")).strip()
            if not vid or vid == persona.ref:
                continue
            voice = Voice(EL, vid, "neutral")
            if not extra(voice):
                pool.append(voice)
    player_ref = str(v.get("player_ref", "")).strip()
    if player_ref:
        player = Voice(cast_provider, player_ref, "male")
    else:
        males = [x for x in pool if x.gender == "male"]
        player = males[0] if males else (pool[0] if pool else persona)
    return VoiceCast(pool, persona=persona, player=player,
                     cast_provider=cast_provider, synth=synth)


class CastSynth:
    """Routes a Voice to the right TTS provider and returns raw PCM. Backends are injected:
      * `el_synth(text, voice_id|None) -> (pcm, sr)` — the ElevenLabs path;
      * `piper_loader(model_path) -> obj with synth_pcm(text) -> (pcm, sr)` — cached per model.
    Either may be absent; a voice whose provider has no backend, or any synth error, fails soft to
    silence rather than raising into the loop."""

    def __init__(
        self,
        *,
        el_synth: Optional[Callable[[str, Optional[str]], tuple[bytes, int]]] = None,
        piper_loader: Optional[Callable[[str], object]] = None,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._el = el_synth
        self._piper_loader = piper_loader
        self._piper: dict[str, object] = {}
        self._log = log or (lambda _m: None)

    def __call__(self, voice: Voice, text: str) -> tuple[bytes, int]:
        try:
            if voice.provider == EL and self._el is not None:
                return self._el(text, voice.ref or None)
            if voice.provider == PIPER and self._piper_loader is not None:
                model = self._piper.get(voice.ref)
                if model is None:
                    model = self._piper_loader(voice.ref)
                    self._piper[voice.ref] = model
                return model.synth_pcm(text)  # type: ignore[attr-defined]
        except Exception as e:  # noqa: BLE001 — a dead voice degrades to silence, never crashes
            self._log(f"cast synth failed ({voice.provider}:{voice.ref}): {e}")
        return b"", 16000
