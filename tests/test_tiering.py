"""Unit tests for capability/token tiering (issue #84) — pure, offline, free.

Two axes are tiered together: the tool set advertised each turn (a token budget packed by group
priority) and the background LLM calls (proactive callouts, chatter flavor, comms variants). These
assert the five named levels resolve to the documented capability sets, per-provider auto-selection
picks the right level, the choice is stable within a session, and the background-call flags gate the
LLM-generated paths (a disabled path falls back to the canned/verbatim one with NO generator wired).
"""
from __future__ import annotations

import pytest

from covas import settings_schema as schema
from covas import tiering
from covas.capabilities import CapabilityRegistry

# --- fakes -----------------------------------------------------------------------------------

class _FakeCap:
    """A capability that advertises one named tool and declares a tiering group (or none)."""

    def __init__(self, tool_name: str, group: str | None):
        self._name = tool_name
        if group is not None:
            self.TIERING_GROUP = group

    def tools(self) -> list[dict]:
        return [{"name": self._name, "input_schema": {"type": "object", "properties": {}}}]

    def run_tool(self, name, inp):  # pragma: no cover - not exercised here
        return "ok"


def _registry_one_per_group() -> tuple[CapabilityRegistry, dict[str, str]]:
    """A registry with exactly one capability per tiering group, plus one UNTAGGED capability
    (which must resolve to the default group). Returns (registry, {group: tool_name})."""
    names = {g: f"tool_{g}" for g in tiering.GROUPS}
    caps = [_FakeCap(name, g) for g, name in names.items()]
    caps.append(_FakeCap("tool_untagged", None))  # no TIERING_GROUP -> DEFAULT_GROUP
    return CapabilityRegistry(caps), names


# --- budget packing produces the documented capability sets ----------------------------------

def test_full_includes_every_group():
    assert tiering.included_groups_for_level("Full") == set(tiering.GROUPS)


def test_standard_drops_search_and_engineering():
    got = tiering.included_groups_for_level("Standard")
    assert "search" not in got and "engineering" not in got
    assert {"core", "checklist", "commander_state", "memory", "location", "keybinds"} <= got


def test_lean_is_core_checklist_commander_state():
    assert tiering.included_groups_for_level("Lean") == {"core", "checklist", "commander_state"}


def test_minimal_is_core_and_checklist_only():
    assert tiering.included_groups_for_level("Minimal") == {"core", "checklist"}


def test_bare_has_no_groups():
    assert tiering.included_groups_for_level("Bare") == set()


@pytest.mark.parametrize("level,expected_groups", [
    ("Full", set(tiering.GROUPS)),
    ("Standard", {"core", "checklist", "commander_state", "memory", "location", "keybinds"}),
    ("Lean", {"core", "checklist", "commander_state"}),
    ("Minimal", {"core", "checklist"}),
    ("Bare", set()),
])
def test_registry_tools_for_each_level(level, expected_groups):
    """The registry advertises exactly the tools of the included groups at each level. The untagged
    capability tracks the default group (core), so it appears iff core is included."""
    reg, names = _registry_one_per_group()
    lvl = tiering.LEVELS[level]
    got = {t["name"] for t in reg.tools_for_level(lvl)}
    expected = {names[g] for g in expected_groups}
    if "core" in expected_groups:
        expected.add("tool_untagged")   # untagged -> DEFAULT_GROUP == core
    assert got == expected


def test_bare_advertises_no_tools():
    reg, _ = _registry_one_per_group()
    assert reg.tools_for_level(tiering.LEVELS["Bare"]) == []


def test_untagged_capability_defaults_to_core_group():
    assert tiering.group_of_capability(_FakeCap("x", None)) == tiering.DEFAULT_GROUP
    # an unknown/misdeclared group also falls back to the default
    assert tiering.group_of_capability(_FakeCap("x", "nonsense")) == tiering.DEFAULT_GROUP


def test_falsy_level_returns_unfiltered_tools():
    """Defensive: a missing level must not silently drop every tool."""
    reg, _ = _registry_one_per_group()
    assert len(reg.tools_for_level(None)) == len(reg.tools())


# --- stability within a session --------------------------------------------------------------

def test_tool_ids_and_groups_stable_across_calls():
    reg, _ = _registry_one_per_group()
    lvl = tiering.LEVELS["Standard"]
    first = [t["name"] for t in reg.tools_for_level(lvl)]
    second = [t["name"] for t in reg.tools_for_level(lvl)]
    assert first == second  # same ids, same order, no per-call recomputation drift


def test_resolve_level_is_deterministic():
    cfg = {"llm": {"provider": "openai", "optimization_level": "auto"},
           "openai": {"base_url": "https://api.groq.com/openai/v1"}}
    assert tiering.resolve_level(cfg) is tiering.resolve_level(cfg)  # same singleton Level


# --- per-provider auto-selection (the shipped default table) ---------------------------------

def test_auto_anthropic_is_full():
    assert tiering.auto_level_name("anthropic") == "Full"


def test_auto_gemini_is_full():
    assert tiering.auto_level_name("gemini") == "Full"


@pytest.mark.parametrize("base_url", [
    "https://api.openai.com/v1",
    "https://api.deepseek.com/v1",
    "https://openrouter.ai/api/v1",
])
def test_auto_openai_family_is_full(base_url):
    assert tiering.auto_level_name("openai", base_url) == "Full"


def test_auto_groq_free_is_minimal():
    assert tiering.auto_level_name("openai", "https://api.groq.com/openai/v1") == "Minimal"


def test_auto_unknown_custom_base_url_is_full():
    assert tiering.auto_level_name("openai", "https://llm.mycorp.internal/v1") == "Full"


def test_custom_tpm_overrides_and_maps_to_level():
    # A user-entered TPM wins for ANY endpoint (even one that would otherwise be Full).
    assert tiering.auto_level_name("anthropic", "", custom_tpm=12000) == "Minimal"
    assert tiering.auto_level_name("openai", "https://api.openai.com/v1", custom_tpm=25000) == "Lean"


@pytest.mark.parametrize("tpm,level", [
    (12000, "Minimal"), (14999, "Minimal"),
    (15000, "Lean"), (29999, "Lean"),
    (30000, "Standard"), (59999, "Standard"),
    (60000, "Full"), (200000, "Full"),
    (0, "Full"),
])
def test_level_for_tpm_thresholds(tpm, level):
    assert tiering.level_for_tpm(tpm) == level


def test_resolve_level_auto_groq_free():
    cfg = {"llm": {"provider": "openai", "optimization_level": "auto"},
           "openai": {"base_url": "https://api.groq.com/openai/v1"}}
    assert tiering.resolve_level(cfg).name == "Minimal"


def test_resolve_level_manual_override_wins_over_provider():
    cfg = {"llm": {"provider": "anthropic", "optimization_level": "Bare"}}
    assert tiering.resolve_level(cfg).name == "Bare"


def test_resolve_level_manual_is_case_insensitive():
    cfg = {"llm": {"provider": "anthropic", "optimization_level": "minimal"}}
    assert tiering.resolve_level(cfg).name == "Minimal"


def test_resolve_level_custom_tpm_in_auto_mode():
    cfg = {"llm": {"provider": "openai", "optimization_level": "auto", "custom_tpm": 20000},
           "openai": {"base_url": "https://llm.mycorp.internal/v1"}}
    assert tiering.resolve_level(cfg).name == "Lean"


# --- background-call gating (second axis) — the per-level flag table --------------------------

@pytest.mark.parametrize("level,proactive,chatter,variants", [
    ("Full", True, True, True),
    ("Standard", True, False, False),
    ("Lean", False, False, False),
    ("Minimal", False, False, False),
    ("Bare", False, False, False),
])
def test_background_flags_match_table(level, proactive, chatter, variants):
    lvl = tiering.LEVELS[level]
    assert (lvl.proactive, lvl.chatter_flavor, lvl.comms_variants) == (proactive, chatter, variants)


# --- the settings schema stays in lock-step with the real levels -----------------------------

def test_settings_optimization_levels_match_tiering():
    # "auto" + the five named levels, in the same order — so the Settings dropdown can't drift.
    assert schema.OPTIMIZATION_LEVELS == ["auto"] + tiering.LEVEL_NAMES


# --- AudioLayer wiring: the allow_* flags gate the LLM generators (canned fallback) ----------

class _FakeLLM:
    """An LLM whose stream_reply would be a background call — if it were ever wired."""

    def __init__(self):
        self.calls = 0

    def stream_reply(self, messages, cancel, on_event, **kw):  # pragma: no cover - must not run
        self.calls += 1
        yield ("text", "flavored")


def _audio_layer(*, allow_flavor, allow_variants):
    """Build an offline AudioLayer with flavor + variants CONFIGURED ON, so only the tiering
    allow_* flags decide whether the LLM generators are wired. Returns (layer, fake_llm)."""
    from covas.mixer import AudioLayer, BusMixer

    class _FakeTTS:
        def synth_pcm(self, text, voice_id=None):
            return b"", 16000

    cfg = {"audio": {"mix_sample_rate": 16000,
                     "cues": {"enabled": True, "flavor": True},
                     "comms": {"enabled": True, "variants": True}}}
    llm = _FakeLLM()
    mix = BusMixer(cfg)
    layer = AudioLayer(cfg, mix, _FakeTTS(), ed_ctx=None, llm=llm,
                       allow_chatter_flavor=allow_flavor, allow_comms_variants=allow_variants)
    return layer, llm


def test_full_level_wires_the_background_generators():
    layer, _ = _audio_layer(allow_flavor=True, allow_variants=True)
    assert layer._chatter._generate is not None      # LLM chatter flavor wired
    assert layer._comms._generate is not None         # LLM comms variants wired


def test_lean_level_falls_back_to_canned_paths():
    """With the flags off (any level below Full), the generators are NOT wired: chatter is
    pool-only and comms are verbatim, so no background LLM call can be spawned."""
    layer, _ = _audio_layer(allow_flavor=False, allow_variants=False)
    assert layer._chatter._generate is None
    assert layer._persona_chatter._generate is None
    assert layer._comms._generate is None


def test_disabled_comms_path_is_verbatim_and_costs_no_llm_call():
    """End-to-end proof the disabled comms path costs no LLM call: a voiced NPC line is the
    verbatim source text and the fake LLM is never invoked."""
    layer, llm = _audio_layer(allow_flavor=False, allow_variants=False)
    said = []
    layer._comms._play = lambda text, rec: (said.append(text) or True)
    layer.on_event({"type": "ed_event", "event": "ReceiveText", "Channel": "npc",
                    "From_Localised": "Control", "Message": "Docking granted."})
    assert said == ["Docking granted."]
    assert llm.calls == 0


# --- app composition: the level filter reaches the real registry + the proactive gate ---------

def _app(tmp_path, *, level="auto"):
    from covas.app import App
    from tests.fakes import FakeLLM, FakeSTT, FakeTTS

    checklist = tmp_path / "checklist.md"
    checklist.write_text("- [ ] Scoop fuel\n", encoding="utf-8")
    cfg = {
        "llm": {"provider": "anthropic", "optimization_level": level, "custom_tpm": 0},
        "anthropic": {"model": "claude-haiku-4-5", "max_tokens": 1024,
                      "thinking": {"default": "Off"}, "cache_ttl": "1h"},
        "web_search": {"enabled": False},
        "personality": {"enabled": False},
        "checklist": {"file": str(checklist)},
        "conversation": {"max_turns": 20},
        "logging": {"dir": str(tmp_path / "logs")},
        "audio": {"sample_rate": 16000, "input_device": ""},
        "sound_cues": {},
        "keys": {"push_to_talk": "right ctrl"},
        "proactive": {"enabled": True},
    }
    return App(cfg, llm=FakeLLM(text="ok"), tts=FakeTTS(), stt=FakeSTT(text="hi"))


def test_app_minimal_keeps_checklist_tools_that_bare_drops(tmp_path):
    """On the REAL registry, Minimal keeps the core + checklist tools while Bare drops every tool —
    proving the level filter is wired into the live app, not just the pure functions above. (This
    minimal config registers only core + checklist capabilities, since ED/search/engineering are
    config-gated off; the deeper drops are covered by the fake-registry test.)"""
    app = _app(tmp_path, level="Minimal")
    assert app.tier_level.name == "Minimal"
    minimal = {t["name"] for t in app.registry.tools_for_level(tiering.LEVELS["Minimal"])}
    bare = {t["name"] for t in app.registry.tools_for_level(tiering.LEVELS["Bare"])}
    assert "add_objective" in minimal          # a checklist tool survives at Minimal
    assert bare == set()                        # Bare advertises nothing


def test_app_bare_advertises_no_tools(tmp_path):
    app = _app(tmp_path, level="Bare")
    assert app.registry.tools_for_level(app.tier_level) == []


def test_app_proactive_suppressed_at_lean_levels(tmp_path):
    """At Minimal the proactive LLM path is gated: _speak_proactive returns False WITHOUT starting
    a worker, so no background LLM call is spawned even though [proactive].enabled is True."""
    app = _app(tmp_path, level="Minimal")
    assert app.tier_level.proactive is False
    started = app._speak_proactive("FSDJump", {"type": "ed_event", "event": "FSDJump"})
    assert started is False
    assert app.worker is None  # no proactive worker thread was launched
