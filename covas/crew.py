"""Crew (issue #69) — interactive multi-character voicing on the CONVERSATION path.

COVAS++ normally speaks in ONE voice: the ship persona. Crew lets the LLM voice a NAMED
character inside an ordinary reply, each line attributed and voiced distinctly, by prefixing a
line with `[<name>]`. The persona (ship COVAS++) stays the DEFAULT speaker; a crew member speaks
only for lines they're explicitly given. This closes the attribution gap from the
context-voice-quality work (issue #57): an "our"-perspective aside can now come from a NAMED crew
member instead of an anonymous radioed cast voice.

Two pure pieces live here so the reply path stays thin and the whole thing is offline-testable:

  * `parse_segments()` — split a reply into an ordered list of `(speaker, text)` segments from
    `[Name]` LINE prefixes. `speaker is None` means the ship persona. PURE + total: malformed
    brackets are treated as ordinary persona text (never a crash), a reply with no prefixes is a
    single persona segment identical to the whole reply, and consecutive same-speaker lines are
    merged into one segment so a multi-line utterance is one synth call.
  * `speak_segments()` — walk the segments in order, routing persona segments to `persona_speak`
    and crew segments to `crew_speak(name, text) -> bool`, honoring barge-in (`cancel`) BETWEEN
    segments. A crew line that couldn't be voiced (returns False) degrades to the persona voice —
    fail soft, exactly like the rest of the loop.

The actual voice routing (VoiceCast.assign -> CastSynth on the radio-treated comms bus) lives in
`mixer/runtime.py::AudioLayer.speak_crew`; this module never touches audio, so it stays pure.

Enablement is `[crew].enabled` (DEFAULT OFF). When ON, a STATIC instruction (see
`system_instruction`) is folded into the cached system prefix telling the model it MAY use the
`[Name]` prefix; when OFF, replies are spoken exactly as before (any stray `[...]` is just read
as text — the parser is never even invoked).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional

# A LINE prefix like "[Nyx] ...". The name is 1..40 chars with no newline or ']' inside; a single
# optional space after the bracket is swallowed so "[Nyx] hi" -> "hi" (not " hi"). Anchored to the
# START of a line only — a bracket mid-sentence is left as ordinary text.
_PREFIX = re.compile(r"^\[([^\]\r\n]{1,40})\]\s?(.*)$")

# Longest crew name we honor when weaving the roster into the system instruction (defensive — a
# config typo shouldn't dump a wall of text into the cached prefix).
_MAX_ROSTER = 12


@dataclass(frozen=True)
class Segment:
    """One contiguous run of the reply attributed to a single speaker. `speaker is None` is the
    ship persona (COVAS++); any other value is a crew character's display name, used verbatim as
    the identity key for the DETERMINISTIC cast-voice assignment (so it's case-sensitive — the
    same spelling always maps to the same voice)."""

    speaker: Optional[str]
    text: str


def parse_segments(reply: str, *, enabled: bool = True) -> list[Segment]:
    """Split `reply` into ordered `Segment`s by `[Name]` line prefixes.

    Rules (all total — this never raises):
      * `enabled=False` -> a single persona segment holding the whole reply verbatim (today's
        behaviour: the reply is spoken as-is, brackets and all).
      * No valid prefix anywhere -> a single persona segment identical to the whole reply (so the
        overwhelmingly common case is byte-for-byte what the direct speak path would say).
      * A line beginning `[<name>] rest` attributes `rest` to `<name>` (name trimmed of
        surrounding spaces); a line with no prefix is a persona line.
      * Malformed / empty-name brackets (`[unclosed`, `[]`, `[   ]`) do NOT match -> the line is
        persona text kept verbatim (the `[` is spoken/shown as-is), never a crash.
      * Consecutive lines with the SAME speaker are merged into one segment (joined with '\\n'),
        so a multi-line crew utterance is a single synth call and runs of persona lines stay one
        speak. Empty / whitespace-only segments are dropped (nothing to voice)."""
    text = reply if isinstance(reply, str) else str(reply or "")
    if not enabled:
        return [Segment(None, text)]

    tagged: list[tuple[Optional[str], str]] = []
    any_prefix = False
    for line in text.split("\n"):
        m = _PREFIX.match(line)
        if m:
            name = m.group(1).strip()
            if name:  # a blank name ("[   ]") is not a real prefix -> fall through to persona
                any_prefix = True
                tagged.append((name, m.group(2)))
                continue
        tagged.append((None, line))

    # Nothing was actually attributed -> preserve the whole reply exactly (see rule above).
    if not any_prefix:
        return [Segment(None, text)]

    segments: list[Segment] = []
    for speaker, line in tagged:
        if segments and segments[-1].speaker == speaker:
            prev = segments[-1]
            segments[-1] = Segment(speaker, prev.text + "\n" + line)
        else:
            segments.append(Segment(speaker, line))
    return [s for s in segments if s.text.strip()]


def speak_segments(
    segments: list[Segment],
    *,
    persona_speak: Callable[[str], None],
    crew_speak: Callable[[str, str], bool],
    cancel,  # noqa: ANN001 — a threading.Event (kept generic so the parser stays dependency-free)
) -> None:
    """Speak `segments` in order, honoring barge-in BETWEEN segments.

    `persona_speak(text)` voices the ship persona (its own direct TTS path, which handles
    mid-line barge-in itself). `crew_speak(name, text) -> bool` voices a crew member's line and
    returns False when it couldn't (dead provider / empty cast) — in which case that one line
    DEGRADES to the persona voice so the Commander still hears it. A set `cancel` stops the walk
    at the next segment boundary (tap-cancel/barge-in), mirroring the existing speak path."""
    for seg in segments:
        if cancel.is_set():
            return
        if seg.speaker is None:
            persona_speak(seg.text)
        elif not crew_speak(seg.speaker, seg.text):
            persona_speak(seg.text)  # fail soft: crew voice unavailable -> persona voice


def is_enabled(cfg: dict) -> bool:
    """Whether crew voicing is on. DEFAULT OFF (opt-in, like the other atmosphere features)."""
    return bool((cfg.get("crew", {}) or {}).get("enabled", False))


def roster(cfg: dict) -> list[str]:
    """The optional configured crew roster (`[crew].roster`) — a hint list of names. A free-form
    name the model invents still gets a deterministic voice, so this is purely advisory."""
    names = (cfg.get("crew", {}) or {}).get("roster", []) or []
    out: list[str] = []
    for n in names:
        s = str(n).strip()
        if s and s not in out:
            out.append(s)
    return out[:_MAX_ROSTER]


def system_instruction(cfg: dict) -> Optional[str]:
    """The STATIC crew instruction folded into the cached system prefix when crew is enabled, else
    None. It's a constant for a given config (the only variable is the roster, itself static), so
    it never busts the prompt cache turn-to-turn — the cache only rewrites the once when the
    setting or roster changes. Returns None when crew is off (nothing added, prefix unchanged)."""
    if not is_enabled(cfg):
        return None
    names = roster(cfg)
    crew_line = (
        f" Your crew: {', '.join(names)}." if names else
        " You may use any short crew name that fits the moment."
    )
    return (
        "CREW VOICING: You may voice a named crew member by starting a line with their name in "
        "square brackets, e.g. `[Nyx] Picking up a contact off the port bow.` Each such line is "
        "spoken aloud in that character's OWN distinct voice." + crew_line + " Lines with NO "
        "bracket prefix are spoken by you, the ship's companion — that stays the default. Use "
        "crew sparingly and only when it adds something; put the bracket at the very start of the "
        "line, and keep each character's spoken text on its own line."
    )
