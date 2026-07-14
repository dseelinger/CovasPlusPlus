"""Unit tests for the TTS provider registry + per-role resolution (issue #14). Offline, no SDKs.

Covers the seam the Edge/OpenAI/Azure/Cartesia providers (#15–#18) plug into: registering a
backend by name, per-role provider resolution, and that `CastSynth` dispatches through the registry
(so a newly-registered provider is castable) while still failing soft to silence.
"""
from __future__ import annotations

import pytest

from covas.mixer import CastSynth, Voice
from covas.mixer.voices import EL, PIPER
from covas.providers.registry import TTSProviderRegistry, resolve_provider


# ---- TTSProviderRegistry -------------------------------------------------------------------

def test_register_has_names_and_synth():
    reg = TTSProviderRegistry()
    reg.register("Edge", lambda text, ref: (f"{ref}:{text}".encode(), 24000))
    assert reg.has("edge") and reg.has("EDGE")          # name lookup is case-insensitive
    assert not reg.has("piper")
    assert reg.names() == ["edge"]
    assert reg.synth("edge", "hi", "aria") == (b"aria:hi", 24000)


def test_synth_defaults_blank_ref_and_chains():
    reg = TTSProviderRegistry().register("a", lambda t, r: (r.encode() or b"DEF", 16000))
    assert reg.synth("a", "x") == (b"DEF", 16000)       # ref omitted -> '' -> provider default


def test_unknown_provider_raises_keyerror():
    with pytest.raises(KeyError):
        TTSProviderRegistry().synth("nope", "t")


def test_register_replaces_existing():
    reg = TTSProviderRegistry()
    reg.register("x", lambda t, r: (b"OLD", 1))
    reg.register("x", lambda t, r: (b"NEW", 2))
    assert reg.synth("x", "t") == (b"NEW", 2)


# ---- resolve_provider ----------------------------------------------------------------------

def _cfg(cast_provider="elevenlabs", providers=None):
    voices = {"cast_provider": cast_provider}
    if providers is not None:
        voices["providers"] = providers
    return {"audio": {"voices": voices}}


def test_role_override_wins():
    cfg = _cfg("elevenlabs", {"chatter": "Piper", "comms": "edge"})
    assert resolve_provider(cfg, "chatter") == "piper"  # lower-cased
    assert resolve_provider(cfg, "comms") == "edge"


def test_falls_back_to_cast_provider_then_default():
    cfg = _cfg("piper", {"comms": "edge"})
    assert resolve_provider(cfg, "chatter") == "piper"  # no override -> umbrella cast_provider
    assert resolve_provider({}, "comms") == "elevenlabs"  # nothing set -> hard default


def test_explicit_default_arg_used_before_cast_provider():
    cfg = _cfg("piper")                                  # no per-role override
    assert resolve_provider(cfg, "persona", default="elevenlabs") == "elevenlabs"
    # ...but a per-role override still beats the passed default
    cfg2 = _cfg("piper", {"persona": "edge"})
    assert resolve_provider(cfg2, "persona", default="elevenlabs") == "edge"


def test_empty_override_value_is_ignored():
    cfg = _cfg("piper", {"chatter": ""})                 # blank -> treated as unset
    assert resolve_provider(cfg, "chatter") == "piper"


# ---- CastSynth dispatches through a shared registry ----------------------------------------

def test_castsynth_routes_via_shared_registry():
    reg = TTSProviderRegistry().register("edge", lambda text, ref: (b"EDGE", 24000))
    cs = CastSynth(registry=reg)
    assert cs(Voice("edge", "aria"), "hello") == (b"EDGE", 24000)
    assert cs(Voice("piper", "x.onnx"), "t") == (b"", 16000)   # unregistered -> silence


def test_castsynth_registry_property_is_extensible():
    # A provider added AFTER construction (the #15 pattern) becomes castable immediately.
    cs = CastSynth(el_synth=None, piper_loader=None)
    assert cs(Voice("edge", "v"), "t") == (b"", 16000)         # not registered yet -> silence
    cs.registry.register("edge", lambda text, ref: (b"LATE", 22050))
    assert cs(Voice("edge", "v"), "t") == (b"LATE", 22050)


def test_castsynth_backcompat_wraps_el_and_piper():
    # The legacy constructor still works: el_synth/piper_loader are wrapped into the registry.
    cs = CastSynth(el_synth=lambda text, vid: (f"EL:{vid}".encode(), 22050),
                   piper_loader=lambda path: type("P", (), {"synth_pcm": lambda s, t: (b"PIPER", 16000)})())
    assert cs(Voice(EL, "VID"), "hi") == (b"EL:VID", 22050)
    assert cs(Voice(EL, ""), "hi") == (b"EL:None", 22050)      # blank ref -> None to el_synth
    assert cs(Voice(PIPER, "m.onnx"), "a") == (b"PIPER", 16000)


def test_castsynth_registry_error_is_silent():
    reg = TTSProviderRegistry().register("boom", lambda text, ref: (_ for _ in ()).throw(RuntimeError("x")))
    assert CastSynth(registry=reg)(Voice("boom", "v"), "t") == (b"", 16000)
