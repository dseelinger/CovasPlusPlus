"""Unit tests for C7 music — context selection, crossfade math, no runtime generation. Offline."""
from __future__ import annotations

import numpy as np
import pytest

from covas.mixer import (
    MUSIC,
    CueRegistry,
    MusicDirector,
    MusicLibrary,
    crossfade,
    equal_power_gains,
    fade_in,
    fade_out,
    generate_track,
    music_context,
    music_cues,
    register_music,
)
from covas.mixer.music import (
    CTX_COMBAT,
    CTX_DEEP_SPACE,
    CTX_DEFAULT,
    CTX_NEAR_STAR,
    CTX_POPULATED,
)

# ---- context selection ---------------------------------------------------------------------

def test_music_context_priority():
    assert music_context({"in_danger", "populated"}) == CTX_COMBAT     # combat wins
    assert music_context({"scooping_fuel", "deep_space"}) == CTX_NEAR_STAR
    assert music_context({"deep_space"}) == CTX_DEEP_SPACE
    assert music_context({"unpopulated"}) == CTX_DEEP_SPACE
    assert music_context({"populated"}) == CTX_POPULATED
    assert music_context(set()) == CTX_DEFAULT


# ---- library selection ---------------------------------------------------------------------

def _lib():
    return MusicLibrary({
        CTX_DEEP_SPACE: ["music/deep1.ogg", "music/deep2.ogg"],
        CTX_POPULATED: ["music/city.ogg"],
        CTX_DEFAULT: ["music/amb.ogg"],
    })


def test_library_selection_is_deterministic_rotation():
    lib = _lib()
    assert lib.select(CTX_DEEP_SPACE, 0) == "music/deep1.ogg"
    assert lib.select(CTX_DEEP_SPACE, 1) == "music/deep2.ogg"
    assert lib.select(CTX_DEEP_SPACE, 2) == "music/deep1.ogg"      # wraps


def test_library_falls_back_to_default_then_none():
    lib = _lib()
    assert lib.select(CTX_NEAR_STAR, 0) == "music/amb.ogg"         # no near_star -> default
    assert MusicLibrary({}).select(CTX_DEEP_SPACE, 0) is None      # nothing -> silent
    assert lib.tracks_for(CTX_POPULATED) == ["music/city.ogg"]
    assert set(lib.contexts()) == {CTX_DEEP_SPACE, CTX_POPULATED, CTX_DEFAULT}


def test_library_from_cfg():
    cfg = {"music": {"tracks": {"deep_space": ["a.ogg", "b.ogg"], "populated": []}}}
    lib = MusicLibrary.from_cfg(cfg)
    assert lib.tracks_for("deep_space") == ["a.ogg", "b.ogg"]
    assert lib.tracks_for("populated") == []


# ---- crossfade envelope math ---------------------------------------------------------------

def test_equal_power_gains_conserve_power():
    gout, gin = equal_power_gains(64)
    assert np.allclose(gout**2 + gin**2, 1.0, atol=1e-6)   # constant power across the blend
    assert gout[0] == pytest.approx(1.0) and gout[-1] == pytest.approx(0.0)
    assert gin[0] == pytest.approx(0.0) and gin[-1] == pytest.approx(1.0)


def test_crossfade_length_and_endpoints():
    a = np.ones(100, dtype=np.float32)
    b = np.full(80, 0.5, dtype=np.float32)
    out = crossfade(a, b, overlap=20)
    assert out.shape[0] == 100 + 80 - 20            # len(a)+len(b)-overlap
    assert out[0] == pytest.approx(1.0)             # starts as a
    assert out[-1] == pytest.approx(0.5)            # ends as b
    assert out.dtype == np.float32


def test_crossfade_zero_overlap_is_concatenation():
    a = np.ones(10, dtype=np.float32)
    b = np.zeros(10, dtype=np.float32)
    assert np.array_equal(crossfade(a, b, 0), np.concatenate([a, b]))


def test_crossfade_overlap_clamped_to_shorter_buffer():
    a = np.ones(5, dtype=np.float32)
    b = np.ones(3, dtype=np.float32)
    out = crossfade(a, b, overlap=100)              # clamped to 3
    assert out.shape[0] == 5 + 3 - 3


def test_fade_in_out():
    x = np.ones(50, dtype=np.float32)
    fin = fade_in(x, 10)
    assert fin[0] == pytest.approx(0.0) and fin[10] == pytest.approx(1.0)
    fout = fade_out(x, 10)
    assert fout[-1] == pytest.approx(0.0) and fout[0] == pytest.approx(1.0)


# ---- director transitions ------------------------------------------------------------------

def test_director_disabled_is_silent():
    d = MusicDirector(_lib(), enabled=False)
    assert d.update({"deep_space"}) is None


def test_director_first_track_is_fade_in_then_stable():
    d = MusicDirector(_lib(), enabled=True)
    t1 = d.update({"deep_space"})
    assert t1 is not None and t1.to_track == "music/deep1.ogg" and not t1.crossfade
    assert d.update({"deep_space"}) is None          # same context -> keep playing


def test_director_crossfades_on_context_change():
    d = MusicDirector(_lib(), enabled=True)
    d.update({"deep_space"})
    t2 = d.update({"populated"})
    assert t2 is not None and t2.crossfade
    assert t2.from_track == "music/deep1.ogg" and t2.to_track == "music/city.ogg"
    assert d.current_context == CTX_POPULATED


def test_director_keeps_current_when_new_context_has_no_track():
    lib = MusicLibrary({CTX_DEEP_SPACE: ["music/deep1.ogg"]})   # no default, no combat track
    d = MusicDirector(lib, enabled=True)
    d.update({"deep_space"})
    assert d.update({"in_danger"}) is None           # combat has no track -> keep deep1
    assert d.current_track == "music/deep1.ogg"


def test_director_from_cfg_enabled_flag():
    # Music is gated behind [experimental.music] too (issue #123) — [music].enabled alone builds
    # a DISABLED director; both are required to arm it.
    assert not MusicDirector.from_cfg({"music": {"enabled": True, "tracks": {}}})._enabled  # noqa: SLF001
    on = {"music": {"enabled": True, "tracks": {}}, "experimental": {"music": {"enabled": True}}}
    assert MusicDirector.from_cfg(on)._enabled  # noqa: SLF001
    assert not MusicDirector.from_cfg({})._enabled  # noqa: SLF001


# ---- cue-registry inventory ----------------------------------------------------------------

def test_music_cues_register_cleanly_and_nebula_is_silent():
    lib = _lib()
    reg = CueRegistry()
    register_music(reg, lib)
    assert reg.contract_violations() == []
    names = {c.name for c in reg.cues()}
    assert "music_deep_space" in names and "music_nebula" in names
    # nebula has no state token -> empty eligibility -> valid but never eligible (silent)
    assert reg.eligible({"nebula", "deep_space", "populated"})  # some eligible, nebula won't be
    nebula = next(c for c in music_cues(lib) if c.name == "music_nebula")
    assert nebula.bus == MUSIC and nebula.eligible_states == frozenset()


# ---- generation is a seam, not a runtime dependency ----------------------------------------

def test_generation_is_a_seam_not_a_dependency():
    with pytest.raises(NotImplementedError):
        generate_track("deep_space")
    # a full director + library cycle works with ZERO generation involvement
    d = MusicDirector(_lib(), enabled=True)
    assert d.update({"deep_space"}).to_track == "music/deep1.ogg"
