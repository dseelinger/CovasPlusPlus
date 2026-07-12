"""ReceiveText capture + channel gate (C4) — the CORE SAFETY CONTRACT of the comms layer.

`ReceiveText` is comms-panel TEXT that Elite Dangerous never voices itself. Voicing it at all
is OUR feature, so deciding WHICH lines may ever be spoken is safety-critical: getting it wrong
means the companion reads a random stranger's Open-play broadcast aloud. The gate is therefore
FAIL-CLOSED — if a line can't be confidently classified as either an NPC line or the Commander's
own direct player DM, it is dropped. Silence beats voicing a stranger.

Everything here is PURE + deterministic and routes NOTHING to TTS. It only:
  1. classifies a ReceiveText event by `Channel` (+ a CMDR-prefix check for ambiguous cases),
  2. derives a template identity so repeated/renumbered station spam dedups by SOURCE TEMPLATE
     (fed to the C3 governor as the cue key), and
  3. emits a structured `VoiceableComms` record for the C5 variant pipeline.

Verified ReceiveText fields: From, From_Localised, Message, Message_Localised, Channel. Real
players carry a "CMDR"/$cmdr_decorate-prefixed From_Localised; NPCs do not.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

# ---- channels --------------------------------------------------------------------------------
PLAYER = "player"       # a real human DM'ing the Commander directly -> voice VERBATIM
NPC = "npc"             # a game NPC line -> eligible for the C5 variant pipeline
# The Open-population firehose: real players broadcasting. NEVER voiced.
FIREHOSE: frozenset[str] = frozenset({
    "local", "wing", "friend", "voicechat", "squadron", "starsystem",
})

# ---- logical voices (mapped to concrete TTS voice_ids by config/C5) --------------------------
VOICE_MALE = "male"
VOICE_FEMALE = "female"
VOICE_DEFAULT = "default"

# ---- variant ceilings (how far C5 may rework a line) -----------------------------------------
TIER_VERBATIM = "verbatim"     # exact words only (player DMs — never paraphrase a human)
TIER_RIFF = "riff"             # up to a tonal riff allowed (NPC lines; C5's cue picks <= this)

# Honorifics that reliably imply a gender, so an NPC voice is deterministic from the name when it
# carries one — else the neutral default. Deliberately conservative: a wrong guess is worse than
# the default, and we NEVER randomize.
_FEMALE_MARKERS = frozenset({
    "mrs", "ms", "miss", "madam", "madame", "lady", "queen", "princess", "baroness",
    "duchess", "dame", "sister", "mother",
})
_MALE_MARKERS = frozenset({
    "mr", "mister", "sir", "lord", "master", "king", "prince", "baron", "duke",
    "brother", "father",
})

_WORD = re.compile(r"[A-Za-z0-9']+")


@dataclass(frozen=True)
class Decision:
    """The pure classifier result for a ReceiveText event."""

    voiceable: bool      # may this line EVER be voiced?
    kind: str            # "player" | "npc" | "dropped"
    channel: str         # normalized channel as classified ("" / "unknown" for ambiguous)
    voice: str           # VOICE_MALE / VOICE_FEMALE / VOICE_DEFAULT ("" when dropped)
    max_tier: str        # variant ceiling: verbatim (player) / riff (npc) ("" when dropped)
    reason: str          # why — logged so the gate is auditable


@dataclass(frozen=True)
class VoiceableComms:
    """The structured record emitted for the C5 variant pipeline. `voiceable` is the ONLY gate
    downstream must honor; `dedup_key` is the governor cue key (template-identity cooldown)."""

    voiceable: bool
    kind: str
    channel: str
    sender: str          # From_Localised (fallback From) — for logging/attribution, never a gate
    text: str            # the source message (Message_Localised, fallback Message)
    voice: str
    max_tier: str
    dedup_key: str
    reason: str


def _norm_channel(event: dict) -> str:
    return str(event.get("Channel") or "").strip().lower()


def _from_localised(event: dict) -> str:
    return str(event.get("From_Localised") or "").strip()


def _message(event: dict) -> str:
    return str(event.get("Message_Localised") or event.get("Message") or "").strip()


def is_cmdr(event: dict) -> bool:
    """True when the sender looks like a real Commander: a "CMDR"-prefixed From_Localised or a
    $cmdr_decorate-wrapped raw From. Used ONLY to fail-close the ambiguous branch."""
    if _from_localised(event).upper().startswith("CMDR"):
        return True
    return "cmdr_decorate" in str(event.get("From") or "").lower()


def npc_voice(event: dict) -> str:
    """Deterministic NPC voice from the sender name: a gendered honorific picks male/female,
    otherwise the neutral default. Pure — same name always yields the same voice."""
    name = _from_localised(event) or str(event.get("From") or "")
    for tok in _WORD.findall(name.lower()):
        if tok in _FEMALE_MARKERS:
            return VOICE_FEMALE
        if tok in _MALE_MARKERS:
            return VOICE_MALE
    return VOICE_DEFAULT


def _drop(channel: str, reason: str) -> Decision:
    return Decision(False, "dropped", channel, "", "", reason)


def classify(event: dict) -> Decision:
    """Decide whether a ReceiveText event may be voiced, and how. PURE + deterministic +
    fail-closed. The net gate is: player OR npc OR (ambiguous AND NOT CMDR-prefixed)."""
    if not isinstance(event, dict):
        return _drop("", "not an event dict")
    channel = _norm_channel(event)

    if channel == PLAYER:
        # A real human directly messaging the Commander — always read, exactly, fixed male voice
        # (the journal carries no gender; male is right more often than a coin flip, and we do
        # NOT randomize). The CMDR prefix here is EXPECTED and does not gate it.
        return Decision(True, "player", channel, VOICE_MALE, TIER_VERBATIM, "direct player DM")

    if channel == NPC:
        return Decision(True, "npc", channel, npc_voice(event), TIER_RIFF, "npc line")

    if channel in FIREHOSE:
        # local / wing / friend / voicechat / squadron / starsystem — the Open firehose of real
        # players. Never voiced, full stop.
        return _drop(channel, f"firehose channel '{channel}'")

    # Anything else — unknown or missing channel — is AMBIGUOUS. Voice it only if it does NOT
    # look like a real Commander (treat as NPC-like); a CMDR-prefixed ambiguous line is dropped.
    if is_cmdr(event):
        return _drop(channel or "unknown", "ambiguous + CMDR-prefixed (a real commander)")
    return Decision(True, "npc", channel or "unknown", npc_voice(event), TIER_RIFF,
                    "ambiguous, non-CMDR -> treated as npc")


# ---- template-identity dedup ----------------------------------------------------------------
def _sender_tokens(event: dict) -> set[str]:
    name = f"{_from_localised(event)} {event.get('From') or ''}"
    return {t for t in _WORD.findall(name.lower()) if t != "cmdr"}


def message_template(message: str, sender_tokens: Iterable[str] = ()) -> str:
    """Normalize a message to its SOURCE TEMPLATE: numbers -> '#', the sender's own name and
    ALL-CAPS tokens (call-signs, station tags) -> '*', everything else lower-cased. So "Docking
    granted, pad 07" and "Docking granted, pad 12" collapse to one template — the cooldown then
    suppresses re-voicing the same announcement, even renumbered, per jump. Over-collapsing errs
    toward silence, which is the safe direction for an ambient layer."""
    senders = {s.lower() for s in sender_tokens if s}
    out: list[str] = []
    for tok in _WORD.findall(str(message or "")):
        if any(ch.isdigit() for ch in tok):
            out.append("#")
        elif tok.lower() in senders:
            out.append("*")
        elif len(tok) > 1 and tok.isupper():
            out.append("*")
        else:
            out.append(tok.lower())
    return " ".join(out)


def dedup_key(event: dict) -> str:
    """The governor cue key for template-identity cooldown (namespaced so it can't collide with
    other cues). Two renumbered instances of the same announcement share this key."""
    return "comms:" + message_template(_message(event), _sender_tokens(event))


# ---- capture + record -----------------------------------------------------------------------
def is_receive_text(event: dict) -> bool:
    return isinstance(event, dict) and event.get("event") == "ReceiveText"


def evaluate(event: dict) -> VoiceableComms:
    """Classify + attach the source text and dedup key -> a VoiceableComms record. An empty
    message is not voiceable (nothing to say) — another fail-closed guard."""
    d = classify(event)
    text = _message(event)
    voiceable = d.voiceable and bool(text)
    reason = d.reason if (d.voiceable and voiceable) else (
        "empty message" if d.voiceable else d.reason)
    return VoiceableComms(
        voiceable=voiceable,
        kind=d.kind,
        channel=d.channel,
        sender=_from_localised(event) or str(event.get("From") or ""),
        text=text,
        voice=d.voice if voiceable else "",
        max_tier=d.max_tier if voiceable else "",
        dedup_key=dedup_key(event),
        reason=reason,
    )


def capture(event: dict) -> Optional[VoiceableComms]:
    """The capture entry point: a VoiceableComms record for a ReceiveText event, else None. C5
    subscribes to the bus, calls this, and voices records whose `voiceable` is True."""
    if not is_receive_text(event):
        return None
    return evaluate(event)
