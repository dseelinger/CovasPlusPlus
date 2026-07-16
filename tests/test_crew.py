"""Unit tests for interactive crew (issue #69) — offline, no device/network.

Two layers, both pure/hermetic:
  * the `[Name]`-prefix SEGMENT PARSER (`covas.crew.parse_segments`) — exhaustively, including
    every malformed-bracket / multi-line / merge / disabled edge case;
  * the segment DISPATCHER (`covas.crew.speak_segments`) + the static system instruction +
    enablement — routing persona vs crew with FAKES that record `(speaker, text)`, asserting the
    persona-default rule, the fail-soft degrade, and barge-in between segments;
  * a focused `AudioLayer.speak_crew` check — a `[Name]` line routes through the DETERMINISTIC
    cast onto the radio-treated comms bus (same name -> same voice), with a FAKE synth + a real
    device-free BusMixer (no synthesis, no audio device).
"""
from __future__ import annotations

import threading

from covas import crew
from covas.crew import Segment, parse_segments, speak_segments


# ============================================================================================
# 1. Segment parser — the pure, exhaustively-tested core
# ============================================================================================

def test_no_prefix_is_a_single_persona_segment_identical_to_the_reply():
    reply = "All systems nominal, Commander.\nFuel is at ninety percent."
    assert parse_segments(reply) == [Segment(None, reply)]


def test_single_crew_line_routes_to_that_character():
    segs = parse_segments("[Nyx] Contact off the port bow.")
    assert segs == [Segment("Nyx", "Contact off the port bow.")]


def test_persona_then_crew_is_two_ordered_segments():
    segs = parse_segments("Bringing us out of jump.\n[Nyx] Scanners are clear.")
    assert segs == [Segment(None, "Bringing us out of jump."),
                    Segment("Nyx", "Scanners are clear.")]


def test_mixed_persona_and_multiple_crew_preserve_order():
    reply = ("Dropping in now.\n"
             "[Nyx] Three signals, bearing two-seven-zero.\n"
             "[Vela] I've got the shields.\n"
             "Take us in.")
    assert parse_segments(reply) == [
        Segment(None, "Dropping in now."),
        Segment("Nyx", "Three signals, bearing two-seven-zero."),
        Segment("Vela", "I've got the shields."),
        Segment(None, "Take us in."),
    ]


def test_consecutive_same_speaker_lines_merge():
    reply = "[Nyx] First line.\n[Nyx] Second line.\nAll clear.\nStanding by."
    assert parse_segments(reply) == [
        Segment("Nyx", "First line.\nSecond line."),
        Segment(None, "All clear.\nStanding by."),
    ]


def test_different_crew_names_do_not_merge():
    segs = parse_segments("[Nyx] Here.\n[Vela] Also here.")
    assert segs == [Segment("Nyx", "Here."), Segment("Vela", "Also here.")]


def test_name_is_trimmed_so_spacing_variants_share_a_voice_key():
    # "[ Nyx ]" and "[Nyx]" must produce the SAME speaker key (deterministic voice depends on it).
    assert parse_segments("[ Nyx ] Hi")[0].speaker == "Nyx"
    assert parse_segments("[Nyx]Hi")[0] == Segment("Nyx", "Hi")


def test_prefix_swallows_one_space_only():
    # A single space after the bracket is consumed; extra spaces are kept (author's intent).
    assert parse_segments("[Nyx]  double")[0].text == " double"


def test_malformed_brackets_are_plain_persona_text_never_crash():
    for bad in ("[unclosed text", "no brackets here", "]backwards[", "text [Nyx] mid-line"):
        segs = parse_segments(bad)
        assert segs == [Segment(None, bad)], bad


def test_empty_name_brackets_are_not_a_prefix():
    for bad in ("[] nobody", "[   ] spaces only"):
        segs = parse_segments(bad)
        assert segs == [Segment(None, bad)], bad


def test_overlong_name_is_not_treated_as_a_prefix():
    long_name = "N" * 41
    line = f"[{long_name}] too long"
    assert parse_segments(line) == [Segment(None, line)]


def test_empty_crew_text_segments_are_dropped():
    # "[Nyx]" alone (no spoken text) has nothing to voice -> dropped; the persona line survives.
    segs = parse_segments("[Nyx]\nCarry on.")
    assert segs == [Segment(None, "Carry on.")]


def test_whitespace_only_reply_yields_no_speakable_crew_segments():
    # A lone crew prefix with only whitespace after it -> nothing to say.
    assert parse_segments("[Nyx]    ") == []


def test_disabled_returns_whole_reply_verbatim_even_with_brackets():
    reply = "[Nyx] this should NOT be parsed when crew is off."
    assert parse_segments(reply, enabled=False) == [Segment(None, reply)]


def test_non_string_input_is_coerced_not_crashed():
    assert parse_segments(None) == [Segment(None, "")]


# ============================================================================================
# 2. Dispatcher — persona-default routing, fail-soft degrade, barge-in
# ============================================================================================

class _Recorder:
    """Records the ordered (speaker, text) calls. `crew_fail` names crew members whose voice is
    'unavailable' (crew_speak returns False) so we can assert the degrade-to-persona path."""

    def __init__(self, crew_fail=()):
        self.calls: list[tuple[str | None, str]] = []
        self._fail = set(crew_fail)

    def persona(self, text: str) -> None:
        self.calls.append((None, text))

    def crew(self, name: str, text: str) -> bool:
        if name in self._fail:
            return False  # voice unavailable -> caller should degrade to persona
        self.calls.append((name, text))
        return True


def _dispatch(reply, *, crew_fail=(), cancel=None):
    rec = _Recorder(crew_fail=crew_fail)
    speak_segments(parse_segments(reply), persona_speak=rec.persona,
                   crew_speak=rec.crew, cancel=cancel or threading.Event())
    return rec.calls


def test_dispatch_routes_persona_and_crew_in_order():
    calls = _dispatch("Dropping in.\n[Nyx] Contact.\nEngaging.")
    assert calls == [(None, "Dropping in."), ("Nyx", "Contact."), (None, "Engaging.")]


def test_dispatch_persona_is_the_default_for_an_unprefixed_reply():
    calls = _dispatch("Just me talking here.")
    assert calls == [(None, "Just me talking here.")]


def test_dispatch_crew_failure_degrades_to_persona_voice():
    # Nyx's cast voice is unavailable -> the line is still spoken, in the PERSONA voice, in place.
    calls = _dispatch("[Nyx] Say this anyway.", crew_fail={"Nyx"})
    assert calls == [(None, "Say this anyway.")]


def test_dispatch_stops_at_segment_boundary_when_cancelled():
    cancel = threading.Event()
    cancel.set()
    assert _dispatch("Line one.\n[Nyx] Line two.", cancel=cancel) == []


class _CancelAfter:
    """A cancel event that reports 'set' only after N `is_set()` checks — models a barge-in that
    lands PART-WAY through a multi-segment reply."""

    def __init__(self, after: int):
        self._after, self._n = after, 0

    def is_set(self) -> bool:
        self._n += 1
        return self._n > self._after


def test_dispatch_barge_in_midway_stops_remaining_segments():
    rec = _Recorder()
    # is_set() is checked once per segment; fire it before the 3rd segment.
    speak_segments(parse_segments("A.\n[Nyx] B.\nC."), persona_speak=rec.persona,
                   crew_speak=rec.crew, cancel=_CancelAfter(after=2))
    assert rec.calls == [(None, "A."), ("Nyx", "B.")]


# ============================================================================================
# 3. Enablement + static system instruction
# ============================================================================================

def test_is_enabled_defaults_off():
    assert crew.is_enabled({}) is False
    assert crew.is_enabled({"crew": {}}) is False
    assert crew.is_enabled({"crew": {"enabled": True}}) is True


def test_system_instruction_is_none_when_off():
    assert crew.system_instruction({"crew": {"enabled": False}}) is None


def test_system_instruction_present_and_static_when_on():
    inst = crew.system_instruction({"crew": {"enabled": True}})
    assert inst and "bracket" in inst and "[Nyx]" in inst
    # Static: same config -> byte-identical text (so the cached prefix isn't busted turn-to-turn).
    assert inst == crew.system_instruction({"crew": {"enabled": True}})


def test_system_instruction_weaves_the_configured_roster():
    inst = crew.system_instruction({"crew": {"enabled": True, "roster": ["Nyx", "Vela"]}})
    assert "Nyx" in inst and "Vela" in inst


def test_roster_dedupes_trims_and_caps():
    r = crew.roster({"crew": {"roster": ["  Nyx ", "Nyx", "", "Vela"]}})
    assert r == ["Nyx", "Vela"]


def test_build_system_appends_crew_instruction_statically():
    # With personality off there's no base system, so the crew instruction rides alongside the
    # always-on ship-spec guardrail (issue #83) in the (still static) system prompt.
    from covas.llm import build_system
    cfg = {"personality": {"enabled": False}, "crew": {"enabled": True}}
    sys1 = build_system(cfg)
    assert sys1 and "[Nyx]" in sys1
    assert sys1 == build_system(cfg)  # static -> cache-safe
    # Personality AND crew off: the ship-spec grounding guardrail is still present (never None),
    # so the model always knows not to invent ship specs, regardless of persona config.
    bare = build_system({"personality": {"enabled": False}, "crew": {"enabled": False}})
    assert bare is not None and "ship_spec" in bare


# ============================================================================================
# 3b. Crew EDITOR roster (issue #70) — CrewMember model, file load/save, persona folding,
#     voice_ref lookup. All pure/hermetic: a tmp JSON file, no network/device.
# ============================================================================================

def test_crew_member_from_obj_accepts_string_and_table():
    assert crew.CrewMember.from_obj("Nyx") == crew.CrewMember("Nyx", "", "")
    m = crew.CrewMember.from_obj({"name": " Vela ", "persona": " Warm engineer. ",
                                  "voice_ref": " VID "})
    assert m == crew.CrewMember("Vela", "Warm engineer.", "VID")   # trimmed


def test_crew_member_from_obj_drops_nameless_and_junk():
    for junk in ({"persona": "no name"}, {"name": "   "}, "", 42, None, ["Nyx"]):
        assert crew.CrewMember.from_obj(junk) is None


def test_crew_member_persona_is_capped():
    big = "x" * 999
    assert len(crew.CrewMember.from_obj({"name": "N", "persona": big}).persona) == crew._MAX_PERSONA


def test_load_members_prefers_the_roster_file(tmp_path):
    import json
    f = tmp_path / "crew.json"
    f.write_text(json.dumps([{"name": "Nyx", "persona": "Terse.", "voice_ref": "V1"},
                             {"name": "Vela"}]), encoding="utf-8")
    cfg = {"crew": {"file": str(f), "roster": ["Ignored"]}}   # file wins over legacy roster
    members = crew.load_members(cfg)
    assert members == [crew.CrewMember("Nyx", "Terse.", "V1"), crew.CrewMember("Vela", "", "")]


def test_load_members_falls_back_to_legacy_roster_when_no_file(tmp_path):
    cfg = {"crew": {"file": str(tmp_path / "absent.json"), "roster": ["Nyx", "Nyx", "Vela"]}}
    assert [m.name for m in crew.load_members(cfg)] == ["Nyx", "Vela"]   # deduped


def test_load_members_supports_inline_member_tables_in_legacy_roster():
    cfg = {"crew": {"roster": [{"name": "Nyx", "persona": "P", "voice_ref": "V"}]}}
    assert crew.load_members(cfg) == [crew.CrewMember("Nyx", "P", "V")]


def test_load_members_corrupt_file_degrades_to_config_fallback(tmp_path):
    f = tmp_path / "crew.json"
    f.write_text("{not json", encoding="utf-8")
    cfg = {"crew": {"file": str(f), "roster": ["Backup"]}}
    assert [m.name for m in crew.load_members(cfg)] == ["Backup"]   # never crashes


def test_save_members_round_trips_and_is_atomic(tmp_path):
    f = tmp_path / "sub" / "crew.json"            # parent auto-created
    members = [crew.CrewMember("Nyx", "Terse.", "V1"), crew.CrewMember("", "dropped", "")]
    crew.save_members(f, members)                 # nameless entry is dropped on save
    reloaded = crew.load_members({"crew": {"file": str(f)}})
    assert reloaded == [crew.CrewMember("Nyx", "Terse.", "V1")]
    assert not f.with_suffix(".json.tmp").exists()  # temp file cleaned up (atomic replace)


def test_system_instruction_folds_personas_and_stays_static(tmp_path):
    import json
    f = tmp_path / "crew.json"
    f.write_text(json.dumps([{"name": "Nyx", "persona": "Sharp sensor officer"},
                             {"name": "Vela", "persona": "Warm engineer."}]), encoding="utf-8")
    cfg = {"crew": {"enabled": True, "file": str(f)}}
    inst = crew.system_instruction(cfg)
    assert "Nyx" in inst and "Vela" in inst
    assert "Sharp sensor officer." in inst and "Warm engineer." in inst   # trailing period added
    assert inst == crew.system_instruction(cfg)                            # cache-safe: byte-stable


def test_system_instruction_omits_persona_clause_when_none_have_one():
    inst = crew.system_instruction({"crew": {"enabled": True, "roster": ["Nyx"]}})
    assert "In character:" not in inst and "Nyx" in inst


def test_voice_ref_for_returns_explicit_ref_else_blank(tmp_path):
    import json
    f = tmp_path / "crew.json"
    f.write_text(json.dumps([{"name": "Nyx", "voice_ref": "V1"}, {"name": "Vela"}]),
                 encoding="utf-8")
    cfg = {"crew": {"file": str(f)}}
    assert crew.voice_ref_for(cfg, "Nyx") == "V1"    # explicit assignment
    assert crew.voice_ref_for(cfg, "Vela") == ""     # left on auto-assign
    assert crew.voice_ref_for(cfg, "Unknown") == ""  # not in roster -> auto


# ============================================================================================
# 3c. VoiceCast.for_crew — explicit voice_ref overrides the deterministic auto-assign
# ============================================================================================

def _cast(pool_refs):
    from covas.mixer.voices import Voice, VoiceCast
    pool = [Voice("elevenlabs", r, "neutral") for r in pool_refs]
    return VoiceCast(pool, persona=Voice("elevenlabs", "PERSONA"),
                     player=Voice("elevenlabs", "PLAYER"), cast_provider="elevenlabs")


def test_for_crew_blank_ref_falls_back_to_deterministic_assign():
    cast = _cast(["VA", "VB", "VC"])
    # No explicit ref -> identical to assign() (same name -> same voice).
    assert cast.for_crew("Nyx", "") == cast.assign("Nyx")


def test_for_crew_explicit_ref_matching_pool_reuses_that_entry():
    cast = _cast(["VA", "VB", "VC"])
    v = cast.for_crew("Nyx", "VB")
    assert v.ref == "VB" and v.provider == "elevenlabs"   # the pool entry, kept intact


def test_for_crew_explicit_ref_outside_pool_routes_via_cast_provider():
    cast = _cast(["VA"])
    v = cast.for_crew("Nyx", "CUSTOM")
    assert v.ref == "CUSTOM" and v.provider == "elevenlabs"   # raw ref on the cast provider


# ============================================================================================
# 4. AudioLayer.speak_crew — deterministic cast voice on the radio-treated comms bus
# ============================================================================================

class _FakeTTS:
    def synth_pcm(self, text, voice_id=None):
        return b"", 16000

    def speak(self, text, cancel):
        pass


def _crew_layer(recorder):
    """An AudioLayer with an EL cast pool and a synth that RECORDS (voice.ref, text) and returns a
    short non-empty PCM buffer, over a real device-free BusMixer."""
    from covas.mixer import AudioLayer, BusMixer

    cfg = {"elevenlabs": {"voice_id": "PERSONA"},
           "audio": {"mix_sample_rate": 16000,
                     "cues": {"enabled": False}, "comms": {"enabled": True},
                     "voices": {"cast_provider": "elevenlabs",
                                "pool": [{"provider": "elevenlabs", "ref": r}
                                         for r in ("VA", "VB", "VC", "VD", "VE")]}}}

    def synth(voice, text):
        recorder.append((voice.ref, text))
        return b"\x00\x00" * 160, 16000  # 160 samples @ 16 kHz ~= 10 ms

    layer = AudioLayer(cfg, BusMixer(cfg), _FakeTTS(), llm=None,
                       cast_synth=synth, clock=lambda: 0.0)
    return layer


def test_speak_crew_routes_to_the_comms_bus_via_the_cast():
    said: list[tuple[str, str]] = []
    layer = _crew_layer(said)
    ok = layer.speak_crew("Nyx", "Contact.", threading.Event())
    assert ok is True
    assert said == [(said[0][0], "Contact.")]          # went through the cast synth
    assert said[0][0] in {"VA", "VB", "VC", "VD", "VE"}  # a POOL voice, not the persona
    # The line landed on the radio-treated COMMS bus (not COVAS's own clean bus).
    assert any(s["bus"] == "comms" for s in layer.mixer._sources)  # noqa: SLF001


def test_speak_crew_honors_an_explicit_voice_ref(tmp_path):
    # An explicit [crew].file voice_ref (issue #70) overrides the deterministic pick: Nyx is pinned
    # to pool voice "VD" regardless of what assign() would have chosen for that name.
    import json

    said: list[tuple[str, str]] = []
    layer = _crew_layer(said)
    f = tmp_path / "crew.json"
    f.write_text(json.dumps([{"name": "Nyx", "voice_ref": "VD"}]), encoding="utf-8")
    layer.cfg["crew"] = {"file": str(f)}
    layer.speak_crew("Nyx", "Contact.", threading.Event())
    assert said[0][0] == "VD"                            # pinned voice, not the auto-assigned one


def test_speak_crew_is_deterministic_per_name():
    said: list[tuple[str, str]] = []
    layer = _crew_layer(said)
    # Same name, twice -> the SAME voice both times (the core determinism guarantee). Distinct
    # names MAY collide in a small pool, so we assert spread across many names, not per-pair.
    layer.speak_crew("Nyx", "one", threading.Event())
    layer.speak_crew("Nyx", "two", threading.Event())
    for name in ("Vela", "Orin", "Kael", "Suri", "Renn"):
        layer.speak_crew(name, name, threading.Event())
    first = {t: r for r, t in said}
    assert first["one"] == first["two"]                # same name -> same voice, always
    distinct = {r for r, _t in said}
    assert len(distinct) >= 2                           # different names spread across the pool


def test_speak_crew_empty_synth_degrades_to_false():
    layer = _crew_layer([])

    def dead(voice, text):
        return b"", 16000  # provider produced nothing

    layer._cast._synth = dead  # noqa: SLF001
    assert layer.speak_crew("Nyx", "nope", threading.Event()) is False


def test_speak_crew_empty_text_is_handled_without_synth():
    said: list[tuple[str, str]] = []
    layer = _crew_layer(said)
    assert layer.speak_crew("Nyx", "   ", threading.Event()) is True
    assert said == []                                  # nothing synthesized for empty text


def test_speak_crew_barge_in_clears_the_comms_bus():
    said: list[tuple[str, str]] = []
    layer = _crew_layer(said)
    cancel = threading.Event()
    cancel.set()                                       # already barging in
    assert layer.speak_crew("Nyx", "cut me off", cancel) is True
    assert not any(s["bus"] == "comms" for s in layer.mixer._sources)  # noqa: SLF001 — dropped
