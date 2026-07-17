"""Unit tests for context-grounded space chatter (issue #85).

Flavor musings are now seeded from a COMPACT live-ED slice so each line has a real reason, while
staying fact-safe (the output validator still strips names/numbers). These tests cover the pure
situation slice, its injection into the flavor prompt, the de-dupe window, and fail-soft grounding
— all offline, no LLM/network.
"""
from __future__ import annotations

from covas.mixer import COMMS, ChatterPlayer, Cue, build_chatter_prompt, situation_context


class _Speak:
    def __init__(self, ok=True):
        self.ok = ok
        self.said: list[tuple[str, str]] = []

    def __call__(self, text, bus):
        self.said.append((text, bus))
        return self.ok


def _snap(**over):
    base = {
        "system": None, "station": None, "body": None, "ship": None, "ship_name": None,
        "docked": False, "landing_gear": False, "supercruise": False, "hardpoints": False,
        "in_danger": False, "being_interdicted": False, "overheating": False, "game_mode": None,
        "low_fuel": False, "fuel_pct": None, "cargo": None,
    }
    base.update(over)
    return base


# ---- the pure situation slice ---------------------------------------------------------------

def test_situation_context_reflects_population_and_activity():
    ctx = situation_context(_snap(ship="Anaconda", supercruise=True), recent=[],
                            population=5_000_000_000)
    assert "bustling" in ctx and "densely populated" in ctx
    assert "supercruise" in ctx
    assert "flying a Anaconda" in ctx


def test_situation_context_uninhabited_and_danger():
    ctx = situation_context(_snap(being_interdicted=True, in_danger=True, low_fuel=True),
                            recent=[], population=0)
    assert "unpopulated" in ctx
    assert "interdiction" in ctx and "under threat" in ctx and "fuel is running low" in ctx


def test_situation_context_on_foot_skips_ship_clause():
    ctx = situation_context(_snap(ship="Anaconda", game_mode="on_foot"), recent=[], population=1000)
    assert "on foot" in ctx
    assert "flying a" not in ctx           # ship clause suppressed out of the cockpit
    assert "sparse" in ctx                 # 1000 pop -> sparse/remote


def test_situation_context_includes_recent_beats():
    recent = [{"desc": "Jumped to Sol"}, {"desc": "Docked at Abraham Lincoln"},
              {"desc": "Collected 50000 credits"}]
    ctx = situation_context(_snap(), recent=recent, population=None)
    # Only the two freshest beats, and their raw text is allowed here (mood only).
    assert "Docked at Abraham Lincoln" in ctx and "Collected 50000 credits" in ctx
    assert "Jumped to Sol" not in ctx


def test_situation_context_empty_when_nothing_known():
    assert situation_context(_snap(), recent=[], population=None) == ""


# ---- the flavor prompt carries the slice AFTER a stable prefix -------------------------------

def test_prompt_appends_situation_after_static_prefix():
    cue = Cue("muse", COMMS, {"populated"}, fact_bearing=False, phrasings=("A.", "B."))
    bare = build_chatter_prompt(cue)
    grounded = build_chatter_prompt(cue, "in a bustling, densely populated system")
    # The cacheable head is byte-stable: the grounded prompt starts with the bare prompt's prefix.
    assert grounded.startswith(build_chatter_prompt.__globals__["_CHATTER_PREFIX"])
    assert "bustling, densely populated" in grounded
    assert "bustling" not in bare              # no situation clause without context


def test_generator_receives_the_situation():
    cue = Cue("muse", COMMS, {"populated"}, fact_bearing=False, phrasings=("A.", "B."))
    seen: list[str] = []

    def gen(prompt):
        seen.append(prompt)
        return "quiet drift through the dark"

    player = ChatterPlayer(_Speak(), generate=gen,
                           context=lambda: "docked at a station")
    text, source = player.line_for(cue)
    assert source == "flavor" and text == "quiet drift through the dark"
    assert seen and "docked at a station" in seen[0]


# ---- de-dupe: the flavor path won't repeat itself -------------------------------------------

def test_repeat_flavor_line_is_rejected_to_pool():
    cue = Cue("muse", COMMS, {"populated"}, fact_bearing=False, phrasings=("Pool A.", "Pool B."))
    speak = _Speak()
    player = ChatterPlayer(speak, generate=lambda p: "same old quiet out here")
    player(cue)                                   # first: the flavor line plays
    assert speak.said[0][0] == "same old quiet out here"
    player(cue)                                   # exact repeat -> rejected -> pool
    assert speak.said[1][0] == "Pool A."


def test_near_repeat_flavor_line_is_rejected():
    cue = Cue("muse", COMMS, {"populated"}, fact_bearing=False, phrasings=("Pool A.",))
    speak = _Speak()
    scripted = iter(["just the quiet void and me out here",
                     "just the quiet void and me, out here"])   # trivially reworded -> near-repeat
    player = ChatterPlayer(speak, generate=lambda p: next(scripted))
    player(cue)
    player(cue)
    assert speak.said[0][0].startswith("just the quiet void")
    assert speak.said[1][0] == "Pool A."          # near-duplicate fell back to the pool


def test_distinct_flavor_lines_are_not_de_duped():
    cue = Cue("muse", COMMS, {"populated"}, fact_bearing=False, phrasings=("Pool A.",))
    speak = _Speak()
    scripted = iter(["a hush across the stars", "restless engines humming low"])
    player = ChatterPlayer(speak, generate=lambda p: next(scripted))
    player(cue)
    player(cue)
    assert [t for t, _ in speak.said] == ["a hush across the stars", "restless engines humming low"]


# ---- fail-soft: grounding never breaks a musing ---------------------------------------------

def test_context_error_yields_ungrounded_prompt():
    cue = Cue("muse", COMMS, {"populated"}, fact_bearing=False, phrasings=("Pool A.",))

    def boom():
        raise RuntimeError("context glitch")

    seen: list[str] = []
    player = ChatterPlayer(_Speak(), generate=lambda p: seen.append(p) or "peaceful drift",
                           context=boom)
    text, source = player.line_for(cue)
    assert source == "flavor" and text == "peaceful drift"   # still produced a line
    assert seen                                               # prompt was built (ungrounded)


def test_fact_bearing_never_consults_context_or_generator():
    cue = Cue("facty", COMMS, {"populated"}, fact_bearing=True, phrasings=("Pool line.",))
    touched = {"ctx": False, "gen": False}
    player = ChatterPlayer(
        _Speak(),
        generate=lambda p: touched.__setitem__("gen", True) or "x",
        context=lambda: touched.__setitem__("ctx", True) or "in a busy system",
    )
    text, source = player.line_for(cue)
    assert source == "pool" and text == "Pool line."
    assert touched == {"ctx": False, "gen": False}


# ---- the AudioLayer wires the grounding seam ------------------------------------------------

def test_audio_layer_grounds_both_chatter_players():
    from covas.ed.context import EDContext
    from covas.mixer import AudioLayer, BusMixer

    class _FakeTTS:
        def synth_pcm(self, text, voice_id=None):
            return b"", 16000

    cfg = {"audio": {"mix_sample_rate": 16000, "cues": {"enabled": True, "flavor": True}}}
    ed = EDContext()
    ed.update(system="Sol", ship="Sidewinder", supercruise=True)
    layer = AudioLayer(cfg, BusMixer(cfg), _FakeTTS(), ed_ctx=ed, llm=None,
                       allow_chatter_flavor=True)
    # Both players carry the grounding seam, and it reads from the shared EDContext.
    assert layer._chatter._context is not None
    assert layer._persona_chatter._context is not None
    assert "supercruise" in layer._chatter_context()
