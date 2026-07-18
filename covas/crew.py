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

CREW EDITOR (issue #70) — the roster grows up from "a hint list of names" to a first-class,
Commander-editable CAST: each character is a `CrewMember(name, persona, voice_ref)`. The `persona`
folds into the SAME static crew instruction (so a character has consistent, self-authored
personality) and `voice_ref` gives an EXPLICIT voice assignment that overrides the deterministic
auto-assign from #69 (blank `voice_ref` = keep the auto voice). The roster persists to a small JSON
file (`[crew].file`, git-ignored — persona text is personal) editable in the control panel; when
the file is absent we fall back to the legacy `[crew].roster` (names, or inline member tables) so
existing configs keep working unchanged. All of this stays STATIC for a given saved roster, so the
personas ride the prompt cache and only rewrite it the once, when the Commander saves an edit.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

# A LINE prefix like "[Nyx] ...". The name is 1..40 chars with no newline or ']' inside; a single
# optional space after the bracket is swallowed so "[Nyx] hi" -> "hi" (not " hi"). Anchored to the
# START of a line only — a bracket mid-sentence is left as ordinary text.
_PREFIX = re.compile(r"^\[([^\]\r\n]{1,40})\]\s?(.*)$")

# Longest crew name we honor when weaving the roster into the system instruction (defensive — a
# config typo shouldn't dump a wall of text into the cached prefix). Also caps the editable roster.
_MAX_ROSTER = 12
# A persona line is trimmed to this in the (cached) system prefix so one runaway paragraph can't
# blow up the prompt — the Commander gets consistent flavor, not an essay billed every session.
_MAX_PERSONA = 400
# A role is a short free-text FUNCTION label ("Fighter pilot", "Quartermaster") — capped tight so
# it stays a phrase, not prose, in the cached crew instruction (issue #125).
_MAX_ROLE = 60


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


@dataclass(frozen=True)
class CrewMember:
    """One editable crew character (issue #70). `name` is the identity key — case-sensitive, used
    verbatim both for the `[Name]` prefix the model writes and for the deterministic voice fallback,
    so it must match the spelling the model uses. `persona` is optional free-form flavor folded into
    the static crew instruction; `voice_ref` is an optional EXPLICIT voice id (an ElevenLabs
    voice_id or a Piper .onnx path) that overrides the auto-assigned voice — blank = auto-assign.
    `role` (issue #125) is an optional free-text FUNCTION label ("Fighter pilot", "Ship's cook")
    woven into the static instruction so the model plays the character's *role*, not just their
    temperament — legacy files with no `role` key load unchanged (default '')."""

    name: str
    persona: str = ""
    voice_ref: str = ""
    role: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "persona": self.persona,
                "voice_ref": self.voice_ref, "role": self.role}

    @classmethod
    def from_obj(cls, obj: object) -> Optional["CrewMember"]:
        """Coerce one roster entry into a member, fail-soft. Accepts a bare string (a name, for the
        legacy `[crew].roster = ["Nyx", ...]` form) or a table `{name, persona, voice_ref, role}`.
        A missing `role` key defaults to '' so pre-#125 roster files load unchanged. Returns None
        for anything with no usable name so a typo can't inject a nameless voice."""
        if isinstance(obj, str):
            name, persona, voice_ref, role = obj, "", "", ""
        elif isinstance(obj, dict):
            name = str(obj.get("name", "")).strip()
            persona = str(obj.get("persona", "") or "").strip()
            voice_ref = str(obj.get("voice_ref", "") or "").strip()
            role = str(obj.get("role", "") or "").strip()
        else:
            return None
        name = str(name).strip()
        if not name:
            return None
        return cls(name=name, persona=persona[:_MAX_PERSONA], voice_ref=voice_ref,
                   role=role[:_MAX_ROLE])


def is_enabled(cfg: dict) -> bool:
    """Whether crew voicing is on. DEFAULT OFF (opt-in, like the other atmosphere features)."""
    return bool((cfg.get("crew", {}) or {}).get("enabled", False))


def roster_file(cfg: dict) -> Optional[Path]:
    """Absolute path to the JSON crew-roster file (`[crew].file`), already resolved under the
    writable data dir by config._resolve_paths, or None when unconfigured. The editor and the
    voice/prompt paths all read/write THIS one file so a saved edit applies everywhere live."""
    raw = str((cfg.get("crew", {}) or {}).get("file", "") or "").strip()
    return Path(raw) if raw else None


def voice_pairings_file(cfg: dict) -> Path:
    """Absolute path to the crew best-fit-voice pairing CACHE (`[crew].voice_pairings_file`,
    issue #124), already resolved under the writable data dir by `config._resolve_paths`. Kept
    SEPARATE from the shipped-persona cache (`voice_pairing.default_cache_path`) so editing the
    roster never busts the persona pairing and vice versa. Falls back to a sane default name if
    unconfigured (defensive — normal configs always set it via config.toml)."""
    raw = str((cfg.get("crew", {}) or {}).get("voice_pairings_file", "") or "").strip()
    return Path(raw or "crew_voice_pairings.json")


def _dedupe(members: list[CrewMember]) -> list[CrewMember]:
    """First-wins de-dupe by (case-sensitive) name, capped at `_MAX_ROSTER` — a stable, bounded
    roster so the cached instruction can't balloon from a copy-paste."""
    out: list[CrewMember] = []
    seen: set[str] = set()
    for m in members:
        if m.name not in seen:
            seen.add(m.name)
            out.append(m)
    return out[:_MAX_ROSTER]


def load_members(cfg: dict) -> list[CrewMember]:
    """The current crew CAST, fail-soft. Prefers the JSON roster file (`[crew].file`); when it's
    absent or unreadable, falls back to the legacy in-config `[crew].roster` (names or member
    tables) so existing setups keep working. Order is preserved (file order, then de-duped) so the
    derived system instruction is byte-stable for a given saved roster — the prompt-cache guarantee.

    A corrupt file degrades to the config fallback rather than crashing the reply loop (fail soft),
    exactly like the rest of the voice path."""
    path = roster_file(cfg)
    if path is not None and path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8") or "[]")
        except (OSError, json.JSONDecodeError, ValueError) as e:
            _warn(f"could not read crew roster {path} ({e}); using config fallback")
        else:
            if isinstance(data, list):
                members = [m for m in (CrewMember.from_obj(o) for o in data) if m is not None]
                return _dedupe(members)
            _warn(f"crew roster {path} is not a JSON list; using config fallback")
    raw = (cfg.get("crew", {}) or {}).get("roster", []) or []
    members = [m for m in (CrewMember.from_obj(o) for o in raw) if m is not None]
    return _dedupe(members)


def save_members(path: Path | str, members: list[CrewMember]) -> None:
    """Persist the whole roster to the JSON file atomically (temp-then-replace, so a crash
    mid-write can't corrupt the existing roster), fail-soft on I/O error — mirrors MemoryStore."""
    p = Path(path)
    clean = _dedupe([m for m in members if m.name.strip()])
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps([m.to_dict() for m in clean], ensure_ascii=False, indent=2)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(body + "\n", encoding="utf-8")
        tmp.replace(p)  # atomic on the same filesystem
    except OSError as e:
        _warn(f"could not save crew roster {p} ({e})")


def roster(cfg: dict) -> list[str]:
    """The crew names (from the roster file or the legacy config list) — a HINT the model reaches
    for. A free-form name the model invents still gets a voice, so this is purely advisory."""
    return [m.name for m in load_members(cfg)]


def voice_ref_for(cfg: dict, name: str) -> str:
    """The EXPLICIT voice_ref configured for crew member `name`, or '' when the member is unknown or
    left on auto-assign. Case-sensitive to match the `[Name]` identity key. Used by the audio layer
    to override the deterministic auto voice (issue #70) while falling back to it when blank."""
    key = str(name or "").strip()
    for m in load_members(cfg):
        if m.name == key:
            return m.voice_ref
    return ""


def system_instruction(cfg: dict) -> Optional[str]:
    """The STATIC crew instruction folded into the cached system prefix when crew is enabled, else
    None. It's a constant for a given saved roster (the only variable), so it never busts the prompt
    cache turn-to-turn — the cache only rewrites the once when the setting or roster changes.
    Returns None when crew is off (nothing added, prefix unchanged).

    Each member's `persona` (issue #70) is woven in as a short "Name (Role) — persona." clause so
    the model voices a consistent character playing a consistent FUNCTION; a member with a `role`
    (issue #125) but no persona still contributes "Name (Role)." Members with neither just
    contribute their name to the roster hint. Voice assignment (`voice_ref`) is a synthesis-side
    concern and deliberately NOT put in the prompt (the model needn't know which voice maps to a
    name)."""
    if not is_enabled(cfg):
        return None
    members = load_members(cfg)
    names = [m.name for m in members]
    crew_line = (
        f" Your crew: {', '.join(names)}." if names else
        " You may use any short crew name that fits the moment."
    )
    # "Name (Role) — persona." / "Name (Role)." / "Name — persona." — role and persona each optional,
    # so a member contributes a clause here only when they have at least one of the two.
    def _clause(m: CrewMember) -> str:
        head = f"{m.name} ({m.role})" if m.role else m.name
        return f"{head} — {m.persona}" if m.persona else head
    personas = [_clause(m) for m in members if m.persona or m.role]
    persona_line = (" In character: " + " ".join(
        p if p.endswith(".") else p + "." for p in personas)) if personas else ""
    return (
        "CREW VOICING: You may voice a named crew member by starting a line with their name in "
        "square brackets, e.g. `[Nyx] Picking up a contact off the port bow.` Each such line is "
        "spoken aloud in that character's OWN distinct voice." + crew_line + persona_line +
        " Lines with NO bracket prefix are spoken by you, the ship's companion — that stays the "
        "default. Use crew sparingly and only when it adds something; put the bracket at the very "
        "start of the line, and keep each character's spoken text on its own line."
    )


def _warn(msg: str) -> None:
    """Fail-soft diagnostic to stderr (matches app.py / MemoryStore) — never an exception upward."""
    print(f"!! [crew] {msg}", file=sys.stderr, flush=True)
