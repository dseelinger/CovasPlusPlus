"""Unit tests for crew best-fit voice pairing (issue #124) — the crew analogue of #96's persona
pairing. The pure cache/matching mechanics (`pairing_key`, `pair_voices`, fail-soft rules) are
already exhaustively covered by `tests/test_voice_pairing.py`; this file locks the CREW-SPECIFIC
wiring in `covas/bootstrap.py`:

  * the pairing INPUT is filtered to persona-bearing, un-refed members only (voice_ref'd and
    persona-less members are excluded entirely, per the issue's non-goals);
  * it uses its OWN cache file, separate from the shipped-persona cache, keyed by the SAME
    `pairing_key` mechanics (so an edited persona / added member recomputes, reordering doesn't);
  * a successful pairing lands on `app._crew_voice_pairings` and is pushed to the audio layer via
    `AudioLayer.set_crew_pairings`;
  * every failure path (no persona'd members, no catalog, a raising generator, an empty reply)
    fails soft — no crash, and existing state is left exactly as `pair_voices` itself dictates.

All offline: `app.llm` is a scripted FakeLLM, `elevenlabs.list_voices_detailed` is monkeypatched to
a fixed catalog, and the app's own AudioLayer is real but device-free (no network, no audio).
"""
from __future__ import annotations

import json

import pytest

from covas import bootstrap
from covas import crew as crew_mod
from covas import elevenlabs as el
from covas import voice_pairing as vp
from covas.app import App
from tests.fakes import FakeLLM, FakeSTT, FakeTTS

_VOICES = [
    {"voice_id": "v_warm", "name": "Sarah", "labels": {"gender": "female"}, "description": "warm"},
    {"voice_id": "v_gruff", "name": "Bruno", "labels": {"gender": "male"},
     "description": "gravelly"},
]


def _cfg(tmp_path, **crew_over) -> dict:
    checklist = tmp_path / "checklist.md"
    checklist.write_text("- [ ] Scoop fuel\n", encoding="utf-8")
    crew = {"enabled": True, "file": str(tmp_path / "crew.json"),
            "voice_pairings_file": str(tmp_path / "crew_voice_pairings.json")}
    crew.update(crew_over)
    return {
        "llm": {"provider": "anthropic"},
        "anthropic": {"model": "claude-haiku-4-5", "max_tokens": 1024,
                      "available_models": ["claude-haiku-4-5", "claude-sonnet-5"],
                      "thinking": {"default": "Off"}, "cache_ttl": "1h"},
        "tts": {"provider": "elevenlabs"},
        "elevenlabs": {"model": "eleven_flash_v2_5", "voice_id": "v_classic",
                       "voice_name": "Sarah", "speed": 1.0},
        "web_search": {"enabled": False},
        # auto_voice_pairing OFF by default here so App construction's OWN startup pairing kick
        # (bootstrap.build_crew_voice_pairing, wired into the MANIFEST) never races the explicit,
        # synchronous bootstrap.pair_crew_voices(app) calls each test makes below. The ONE test that
        # exercises the startup/kick wiring itself flips this back on deliberately.
        "personality": {"enabled": True, "persona": "Classic", "auto_voice_pairing": False},
        "crew": crew,
        "checklist": {"file": str(checklist)},
        "conversation": {"max_turns": 20},
        "logging": {"dir": str(tmp_path / "logs")},
        # [audio].enabled builds a REAL (device-free) AudioLayer via bootstrap.build_audio_layer,
        # so app.audio exists for the "pushed to the audio layer" assertions below. content_root
        # keeps the C11 drop-in skeleton scan out of the repo (mirrors test_app_audio_wiring.py).
        "audio": {"enabled": True, "sample_rate": 16000, "mix_sample_rate": 16000,
                  "input_device": "", "cues": {"enabled": False}, "comms": {"enabled": False},
                  "content_root": str(tmp_path)},
        "whisper": {"model": "small", "n_threads": 4},
        "keys": {"push_to_talk": "right ctrl"},
    }


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    import covas.app as app_mod
    monkeypatch.setattr(app_mod, "save_overrides", lambda o: None)
    monkeypatch.setattr(app_mod, "make_tts", lambda cfg, mixer=None: FakeTTS())
    monkeypatch.setattr(app_mod, "make_llm", lambda cfg: FakeLLM())


@pytest.fixture()
def make_app():
    """Factory for a real App with the crew audio layer wired (device-free), auto-shut-down at the
    end of the test (mirrors tests/test_app_audio_wiring.py) so the event pump / cast-exclusions
    background threads it starts don't leak between tests."""
    apps: list[App] = []

    def factory(tmp_path, members, **crew_over):
        crew_mod.save_members(tmp_path / "crew.json", members)
        app = App(_cfg(tmp_path, **crew_over), llm=FakeLLM(), tts=FakeTTS(), stt=FakeSTT())
        app.text_only = False
        apps.append(app)
        return app

    yield factory
    for a in apps:
        a.shutdown()


def _reply(pairs: dict) -> str:
    return json.dumps({"pairings": [{"persona": n, "voice_id": v} for n, v in pairs.items()]})


# ---- input filtering: only persona-bearing, un-refed members are paired -----------------------

def test_pairing_input_excludes_voice_refed_and_persona_less_members(
        tmp_path, monkeypatch, make_app):
    members = [
        crew_mod.CrewMember("Nyx", "Sharp-eyed sensor officer, terse and dry."),
        crew_mod.CrewMember("Vela", "", ""),                     # no persona -> excluded
        crew_mod.CrewMember("Kael", "Warm engineer.", "VPINNED"),  # explicit ref -> excluded
    ]
    app = make_app(tmp_path, members)
    monkeypatch.setattr(el, "list_voices_detailed", lambda cfg: _VOICES)
    seen: dict = {}

    def fake_gen(prompt):
        seen["prompt"] = prompt
        return _reply({"Nyx": "v_gruff"})

    monkeypatch.setattr(vp, "make_pairing_generator", lambda llm, model=None: fake_gen)

    bootstrap.pair_crew_voices(app)

    assert "Nyx" in seen["prompt"]
    assert "Vela" not in seen["prompt"]       # no persona -> never offered to the model
    assert "Kael" not in seen["prompt"]       # pinned voice -> never offered to the model
    assert app._crew_voice_pairings == {"nyx": "v_gruff"}


def test_no_persona_bearing_members_clears_pairings_without_a_call(tmp_path, monkeypatch, make_app):
    app = make_app(tmp_path, [crew_mod.CrewMember("Vela", "", "")])  # no persona
    app._crew_voice_pairings = {"stale": "v_warm"}  # a leftover from a prior roster

    called = []
    monkeypatch.setattr(el, "list_voices_detailed", lambda cfg: (called.append(1) or _VOICES))

    bootstrap.pair_crew_voices(app)

    assert not called                    # never even reached the catalog fetch
    assert app._crew_voice_pairings == {}  # stale mapping cleared


# ---- applies the result + pushes it to the audio layer -----------------------------------------

def test_successful_pairing_lands_on_app_and_is_pushed_to_the_audio_layer(
        tmp_path, monkeypatch, make_app):
    members = [crew_mod.CrewMember("Nyx", "Blunt, gravelly, no nonsense.")]
    app = make_app(tmp_path, members)
    monkeypatch.setattr(el, "list_voices_detailed", lambda cfg: _VOICES)
    app.llm = FakeLLM(text=_reply({"Nyx": "v_gruff"}))

    bootstrap.pair_crew_voices(app)

    assert app._crew_voice_pairings == {"nyx": "v_gruff"}
    assert app._voice_names.get("v_gruff") == "Bruno"      # display name merged in
    assert app.audio is not None
    assert app.audio._crew_pairings == {"nyx": "v_gruff"}  # noqa: SLF001 — pushed live


# ---- cache-keyed: recompute only when the persona set changes ----------------------------------

def test_second_run_with_unchanged_roster_is_a_cache_hit(tmp_path, monkeypatch, make_app):
    members = [crew_mod.CrewMember("Nyx", "Blunt, gravelly, no nonsense.")]
    app = make_app(tmp_path, members)
    monkeypatch.setattr(el, "list_voices_detailed", lambda cfg: _VOICES)
    calls = {"n": 0}

    def counting_gen(prompt):
        calls["n"] += 1
        return _reply({"Nyx": "v_gruff"})

    monkeypatch.setattr(vp, "make_pairing_generator", lambda llm, model=None: counting_gen)

    bootstrap.pair_crew_voices(app)
    assert calls["n"] == 1
    bootstrap.pair_crew_voices(app)                        # same roster -> cache hit, no 2nd call
    assert calls["n"] == 1
    assert app._crew_voice_pairings == {"nyx": "v_gruff"}


def test_editing_a_persona_busts_the_cache_and_recomputes(tmp_path, monkeypatch, make_app):
    members = [crew_mod.CrewMember("Nyx", "Blunt, gravelly, no nonsense.")]
    app = make_app(tmp_path, members)
    monkeypatch.setattr(el, "list_voices_detailed", lambda cfg: _VOICES)
    calls = {"n": 0}

    def counting_gen(prompt):
        calls["n"] += 1
        return _reply({"Nyx": "v_gruff"})

    monkeypatch.setattr(vp, "make_pairing_generator", lambda llm, model=None: counting_gen)
    bootstrap.pair_crew_voices(app)
    assert calls["n"] == 1

    # Rewrite the roster with an EDITED persona for the same member -> the pairing_key changes.
    crew_mod.save_members(app.cfg["crew"]["file"],
                          [crew_mod.CrewMember("Nyx", "A totally different personality now.")])
    bootstrap.pair_crew_voices(app)
    assert calls["n"] == 2


def test_reordering_the_roster_stays_a_cache_hit(tmp_path, monkeypatch, make_app):
    m1 = crew_mod.CrewMember("Nyx", "Blunt, gravelly, no nonsense.")
    m2 = crew_mod.CrewMember("Vela", "Warm and steady.")
    app = make_app(tmp_path, [m1, m2])
    monkeypatch.setattr(el, "list_voices_detailed", lambda cfg: _VOICES)
    calls = {"n": 0}

    def counting_gen(prompt):
        calls["n"] += 1
        return _reply({"Nyx": "v_gruff", "Vela": "v_warm"})

    monkeypatch.setattr(vp, "make_pairing_generator", lambda llm, model=None: counting_gen)
    bootstrap.pair_crew_voices(app)
    assert calls["n"] == 1

    crew_mod.save_members(app.cfg["crew"]["file"], [m2, m1])   # same members, reversed order
    bootstrap.pair_crew_voices(app)
    assert calls["n"] == 1                                     # order-independent -> still a hit


# ---- fail-soft --------------------------------------------------------------------------------

def test_generator_error_leaves_deterministic_fallback_in_place(tmp_path, monkeypatch, make_app):
    members = [crew_mod.CrewMember("Nyx", "Blunt, gravelly, no nonsense.")]
    app = make_app(tmp_path, members)
    monkeypatch.setattr(el, "list_voices_detailed", lambda cfg: _VOICES)

    def boom(prompt):
        raise RuntimeError("llm down")

    monkeypatch.setattr(vp, "make_pairing_generator", lambda llm, model=None: boom)

    bootstrap.pair_crew_voices(app)                      # must not raise

    assert app._crew_voice_pairings == {}                # no pairing -> Auto stays deterministic
    assert app.audio._crew_pairings == {}                # noqa: SLF001 — never pushed a bad map


def test_empty_reply_leaves_deterministic_fallback_in_place(tmp_path, monkeypatch, make_app):
    members = [crew_mod.CrewMember("Nyx", "Blunt, gravelly, no nonsense.")]
    app = make_app(tmp_path, members)
    monkeypatch.setattr(el, "list_voices_detailed", lambda cfg: _VOICES)
    app.llm = FakeLLM(text="not json at all")

    bootstrap.pair_crew_voices(app)

    assert app._crew_voice_pairings == {}


def test_empty_catalog_leaves_deterministic_fallback_in_place(tmp_path, monkeypatch, make_app):
    members = [crew_mod.CrewMember("Nyx", "Blunt, gravelly, no nonsense.")]
    app = make_app(tmp_path, members)
    monkeypatch.setattr(el, "list_voices_detailed", lambda cfg: [])

    bootstrap.pair_crew_voices(app)

    assert app._crew_voice_pairings == {}


# ---- separate cache file: a persona-cache-busting change here doesn't touch the crew cache -----

def test_crew_cache_file_is_separate_from_the_persona_cache(tmp_path, monkeypatch, make_app):
    members = [crew_mod.CrewMember("Nyx", "Blunt, gravelly, no nonsense.")]
    app = make_app(tmp_path, members)
    monkeypatch.setattr(el, "list_voices_detailed", lambda cfg: _VOICES)
    app.llm = FakeLLM(text=_reply({"Nyx": "v_gruff"}))

    bootstrap.pair_crew_voices(app)

    crew_cache = tmp_path / "crew_voice_pairings.json"
    assert crew_cache.exists()
    assert not (tmp_path / "voice_pairings.json").exists()   # never touched the persona cache path


# ---- the startup + roster-save entry points respect the SAME gate as #96 ----------------------

def test_build_and_kick_are_gated_by_voice_pairing_allowed(tmp_path, monkeypatch, make_app):
    members = [crew_mod.CrewMember("Nyx", "Blunt, gravelly, no nonsense.")]
    app = make_app(tmp_path, members)
    calls: list = []
    monkeypatch.setattr(bootstrap, "pair_crew_voices", lambda a: calls.append(a))

    # Gated OFF -> the gate check happens BEFORE a thread is even started, so nothing runs.
    app.cfg["personality"]["auto_voice_pairing"] = False
    bootstrap.build_crew_voice_pairing(app)
    bootstrap.kick_crew_voice_pairing(app)
    assert calls == []

    # Gated ON -> a background thread is started and (once joined) has run the worker.
    app.cfg["personality"]["auto_voice_pairing"] = True
    threads: list = []
    orig_thread = bootstrap.threading.Thread
    monkeypatch.setattr(bootstrap.threading, "Thread",
                       lambda **kw: threads.append(orig_thread(**kw)) or threads[-1])
    bootstrap.kick_crew_voice_pairing(app)
    for t in threads:
        t.join(timeout=2)
    assert calls == [app]
