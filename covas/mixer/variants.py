"""Comms variant generator + validator + guaranteed verbatim fallback (C5).

Voices the gated comms lines from C4:
  * player DMs -> read VERBATIM (a human's exact words, never reworded), fixed male voice;
  * NPC lines -> at their allowed tier — verbatim / paraphrase (same meaning, reworded) /
    riff (same intent, tonal flavor, asserts nothing checkable).

The LLM produces candidate TEXT ONLY; it is never in the realtime audio path. Every generated
variant passes a VALIDATOR before it may be spoken, and ANY failure falls back to voicing the
verbatim source line — the guaranteed-safe default. The validator rejects a variant that:
  * introduces a proper noun not in the source,
  * changes or invents a number, or
  * invents a threat / instruction / alarm the source didn't contain.

Routing (the mixer + governor wiring) is injected so the default test run stays offline and
never opens an audio device or hits the network. The shared C3 governor throttles comms
alongside chatter, keyed by the C4 dedup template.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .buses import COMMS
from .comms import TIER_PARAPHRASE, TIER_RIFF, TIER_VERBATIM, VoiceableComms
from .cues import Cue
from .governor import CueGovernor

_WORD = re.compile(r"[A-Za-z']+")
_NUM = re.compile(r"\d+(?:\.\d+)?")
_SENTENCE = re.compile(r"[.!?]+")

# Words a riff/paraphrase may NOT invent: threats, coercive instructions, and fabricated alarm.
# Only rejected when present in the VARIANT but NOT the source — a source that already says
# "under attack" can be reworded with that word; a benign source can't have one bolted on.
_CONTROLLED_WORDS: frozenset[str] = frozenset({
    # threats / combat
    "attack", "attacking", "fire", "firing", "kill", "killing", "destroy", "destroyed",
    "eject", "surrender", "hostile", "hostiles", "weapons", "enemy", "die", "flee", "threat",
    "engage", "shoot", "shooting", "boarding", "interdict", "interdiction", "hostage", "ransom",
    # coercion / instruction
    "comply", "submit", "halt", "jettison", "evade", "bounty", "wanted",
    # fabricated alarm
    "warning", "danger", "alert", "mayday", "emergency", "evacuate",
})

# Tier ordering, so a requested tier can be CLAMPED to a line's ceiling (player -> verbatim).
_TIER_LEVEL = {TIER_VERBATIM: 0, TIER_PARAPHRASE: 1, TIER_RIFF: 2}
_LEVEL_TIER = {0: TIER_VERBATIM, 1: TIER_PARAPHRASE, 2: TIER_RIFF}


def clamp_tier(requested: str, ceiling: str) -> str:
    """The lower of `requested` and `ceiling` — so a player line (ceiling verbatim) is never
    paraphrased, whatever tier a cue asks for."""
    lvl = min(_TIER_LEVEL.get(requested, 0), _TIER_LEVEL.get(ceiling, 0))
    return _LEVEL_TIER[lvl]


def _tokens_lower(text: str) -> set[str]:
    return {t.lower() for t in _WORD.findall(text or "")}


def _numbers(text: str) -> set[float]:
    out: set[float] = set()
    for m in _NUM.findall(text or ""):
        try:
            out.add(float(m))
        except ValueError:  # pragma: no cover - regex already guarantees a float
            pass
    return out


def _proper_nouns(text: str) -> set[str]:
    """Capitalized tokens that look like proper nouns (names, places, call-signs): Title-case or
    ALL-CAPS words that are NOT sentence-initial (where capitalization is ambiguous)."""
    nouns: set[str] = set()
    for sentence in _SENTENCE.split(text or ""):
        toks = _WORD.findall(sentence)
        for i, tok in enumerate(toks):
            if i == 0 or len(tok) < 2 or not tok[0].isupper():
                continue
            if tok.istitle() or tok.isupper():
                nouns.add(tok.lower())
    return nouns


def validate_variant(source: str, candidate: str) -> tuple[bool, str]:
    """Is `candidate` a SAFE re-voicing of `source`? Returns (ok, reason). Fails (so the caller
    falls back to verbatim) if the variant invents a proper noun, changes/adds a number, or
    bolts on a threat/instruction/alarm the source didn't have. Pure + deterministic."""
    cand = candidate or ""
    if not cand.strip():
        return False, "empty variant"
    src_tokens = _tokens_lower(source)

    for noun in _proper_nouns(cand):
        if noun not in src_tokens:
            return False, f"introduced proper noun '{noun}'"

    src_nums = _numbers(source)
    for n in _numbers(cand):
        if n not in src_nums:
            return False, f"introduced/changed number '{n:g}'"

    for w in (_tokens_lower(cand) & _CONTROLLED_WORDS):
        if w not in src_tokens:
            return False, f"invented threat/instruction '{w}'"

    return True, "ok"


def build_variant_prompt(source: str, tier: str) -> str:
    """The user-message prompt for the (cheap-tier) LLM. It rewords ONE comms line; the
    validator is the real guard, but the prompt still tells the model the rules."""
    if tier == TIER_PARAPHRASE:
        how = ("Reword it in different words but keep the SAME meaning and every fact. Do not add "
               "names, numbers, threats, or instructions that aren't in the original.")
    else:  # riff
        how = ("Re-voice it with a little in-character radio personality, same intent. Assert "
               "NOTHING new — no names, numbers, threats, instructions, or alarm beyond the "
               "original. Flavor only.")
    return (
        "You are rewording a single line of Elite Dangerous comms chatter for text-to-speech. "
        f"{how}\n"
        "Reply with ONLY the reworded line — no quotes, no preamble.\n"
        f"Original: {source}"
    )


def make_variant_generator(
    llm, *, model: Optional[str] = None, max_tokens: int = 80  # noqa: ANN001 — LLMProvider
) -> Callable[[str, str], str]:
    """Adapt an LLMProvider into a `generate(source, tier) -> text` callable by accumulating its
    streamed reply. Kept thin; the app injects the cheap tier via `model`. Faked in unit tests
    (no network), so this adapter's only job is prompt + accumulation."""
    def generate(source: str, tier: str) -> str:
        messages = [{"role": "user", "content": build_variant_prompt(source, tier)}]
        parts: list[str] = []
        for kind, chunk in llm.stream_reply(
            messages, threading.Event(), lambda *_a: None, model=model, max_tokens=max_tokens
        ):
            if kind == "text":
                parts.append(chunk)
        return "".join(parts).strip()

    return generate


@dataclass(frozen=True)
class VoicedComms:
    """What the voicer did with one record — for logging/tests. `spoken` is whether it reached
    the play callback; `tier` is what was ACTUALLY applied (verbatim on a fallback)."""

    spoken: bool
    text: str
    tier: str
    voice: str
    reason: str


class CommsVoicer:
    """Turns a VoiceableComms record into a validated, governed, spoken line.

    Injected seams keep it offline-testable:
      * `generate(source, tier) -> str` — the LLM variant generator (None => always verbatim);
      * `play(final_text, record) -> bool` — routes the final text to the comms bus; it gets the
        whole VoiceableComms so the caller can pick the voice by sender identity (C10 cast) and
        returns True only if playback started;
      * `governor` — the SHARED C3 CueGovernor, so comms respect the same rate budget and the
        C4 dedup cooldown (keyed by the record's template).
    """

    def __init__(
        self,
        play: Callable[[str, "VoiceableComms"], bool],
        *,
        generate: Optional[Callable[[str, str], str]] = None,
        governor: Optional[CueGovernor] = None,
        clock: Callable[[], float] = time.monotonic,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._play = play
        self._generate = generate
        self._governor = governor
        self._clock = clock
        self._log = log

    def resolve_text(self, record: VoiceableComms, tier: Optional[str]) -> tuple[str, str, str]:
        """Decide the final SAFE text to voice and the tier actually applied. Verbatim needs no
        generation; a generated variant that fails validation falls back to the verbatim source.
        Returns (text, applied_tier, reason). Pure aside from the injected generator."""
        want = clamp_tier(tier or record.max_tier, record.max_tier)
        if want == TIER_VERBATIM or self._generate is None:
            return record.text, TIER_VERBATIM, "verbatim"
        candidate = ""
        try:
            candidate = self._generate(record.text, want)
        except Exception as e:  # noqa: BLE001 — a generator failure must degrade, not crash
            return record.text, TIER_VERBATIM, f"fallback: generator error ({type(e).__name__})"
        ok, why = validate_variant(record.text, candidate)
        if ok:
            return candidate.strip(), want, f"{want} ok"
        return record.text, TIER_VERBATIM, f"fallback: {why}"

    def _governor_cue(self, record: VoiceableComms) -> Cue:
        # A throwaway cue used only for the governor's per-key cooldown + global budget: the
        # governor reads name + cooldown_s, never the (empty) eligibility set.
        return Cue(record.dedup_key, COMMS, frozenset())

    def voice(self, record: VoiceableComms, *, tier: Optional[str] = None) -> VoicedComms:
        """Validate, govern, and route one record. Never raises. A non-voiceable or governed or
        empty line is simply not spoken."""
        if not record.voiceable:
            return VoicedComms(False, "", "", "", record.reason or "not voiceable")
        now = self._clock()
        gcue = self._governor_cue(record) if self._governor is not None else None
        if self._governor is not None:
            ok, why = self._governor.allow(gcue, now)
            if not ok:
                return VoicedComms(False, "", "", record.voice, f"governed: {why}")
        text, applied, reason = self.resolve_text(record, tier)
        if not text.strip():
            return VoicedComms(False, "", applied, record.voice, reason)
        started = False
        try:
            started = bool(self._play(text, record))
        except Exception as e:  # noqa: BLE001 — a dead TTS degrades, never crashes the loop
            return VoicedComms(False, text, applied, record.voice, f"play error ({type(e).__name__})")
        if started and self._governor is not None:
            self._governor.mark_fired(gcue, now)
        if started and self._log is not None:
            self._log(f"comms[{applied}/{record.voice}]: {text}")
        return VoicedComms(started, text, applied, record.voice, reason)


def comms_voice_id(cfg: dict, logical: str) -> str | None:
    """Map a logical comms voice (male/female/default from C4) to a configured ElevenLabs voice
    id via [audio.comms.voices]. Falls back to the configured default, then None (provider
    default). This is the C1 voice-selection bridge the app's play callback uses."""
    voices = ((cfg.get("audio", {}) or {}).get("comms", {}) or {}).get("voices", {}) or {}
    vid = str(voices.get(logical) or "").strip()
    if vid:
        return vid
    dflt = str(voices.get("default") or "").strip()
    return dflt or None
