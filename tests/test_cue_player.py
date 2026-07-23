"""I8 UI-cue folder discovery + two-tier override resolution (offline, no device).

Covers the resolution rules (override REPLACES default; empty override -> default; neither ->
silent; arbitrary file counts), the sounds-folder skeleton, and that CuePlayer loads/rotates the
resolved set. Playback is exercised through a stub mixer so no audio device is opened.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from covas.audio import (
    CUE_TYPES,
    CuePlayer,
    cue_roots,
    ensure_cue_skeleton,
    resolve_cue_files,
    _scan_cue_dir,
)


def _wav(path, freq=440.0, ms=40):
    """Write a tiny real wav soundfile can read back."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n = int(16000 * ms / 1000)
    t = np.linspace(0, ms / 1000, n, endpoint=False)
    sf.write(str(path), (0.1 * np.sin(2 * np.pi * freq * t)).astype(np.float32), 16000)
    return str(path)


# ---- _scan_cue_dir --------------------------------------------------------------------------
def test_scan_orders_by_name_and_filters_extensions(tmp_path):
    _wav(tmp_path / "b.wav"); _wav(tmp_path / "a.wav")
    (tmp_path / "notes.txt").write_text("ignore me", encoding="utf-8")
    (tmp_path / "README.md").write_text("ignore me", encoding="utf-8")
    out = _scan_cue_dir(tmp_path)
    assert [p.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for p in out] == ["a.wav", "b.wav"]


def test_scan_missing_dir_is_empty(tmp_path):
    assert _scan_cue_dir(tmp_path / "nope") == []


# ---- resolve_cue_files: the two-tier rule ---------------------------------------------------
def test_override_replaces_default(tmp_path):
    user, asset = tmp_path / "sounds", tmp_path / "assets"
    _wav(asset / "listen" / "d1.wav"); _wav(asset / "listen" / "d2.wav")
    _wav(user / "listen" / "mine.wav")
    got = resolve_cue_files("listen", user_base=user, asset_base=asset)
    assert len(got) == 1 and got[0].endswith("mine.wav")   # user set REPLACES the default set


def test_empty_override_falls_back_to_default(tmp_path):
    user, asset = tmp_path / "sounds", tmp_path / "assets"
    (user / "listen").mkdir(parents=True)                  # present but EMPTY
    _wav(asset / "listen" / "d1.wav"); _wav(asset / "listen" / "d2.wav")
    got = resolve_cue_files("listen", user_base=user, asset_base=asset)
    assert len(got) == 2 and all("assets" in p for p in got)


def test_neither_is_silent(tmp_path):
    got = resolve_cue_files("failure", user_base=tmp_path / "sounds", asset_base=tmp_path / "assets")
    assert got == []


def test_arbitrary_file_counts(tmp_path):
    user, asset = tmp_path / "sounds", tmp_path / "assets"
    for i in range(7):
        _wav(user / "processing" / f"p{i}.wav")
    _wav(asset / "completed" / "only.wav")
    assert len(resolve_cue_files("processing", user_base=user, asset_base=asset)) == 7  # N override
    assert len(resolve_cue_files("completed", user_base=user, asset_base=asset)) == 1  # 1 default


# ---- ensure_cue_skeleton --------------------------------------------------------------------
def test_ensure_skeleton_creates_all_types_idempotently(tmp_path):
    base = tmp_path / "sounds"
    ensure_cue_skeleton(base)
    for t in CUE_TYPES:
        assert (base / t).is_dir()
        assert (base / t / "README.txt").is_file()
    # a dropped-in file survives a second call (only-missing writes)
    mine = _wav(base / "listen" / "mine.wav")
    ensure_cue_skeleton(base)
    assert (base / "listen" / "mine.wav").exists() and mine


# ---- cue_roots uses [audio].content_root as the override seam -------------------------------
def test_cue_roots_honors_content_root(tmp_path):
    user, asset = cue_roots({"audio": {"content_root": str(tmp_path)}})
    assert user == tmp_path / "sounds"
    assert asset.parts[-3:] == ("covas", "assets", "cues")


# ---- CuePlayer: loads + rotates the resolved set (stub mixer, no device) --------------------
class _StubMixer:
    def __init__(self):
        self.submitted = []

    def submit(self, bus, buf, sr):
        self.submitted.append((bus, buf, sr))

    def clear_bus(self, bus):
        pass


def _player_with(tmp_path, monkeypatch, *, user_files, asset_files, sleep=None):
    """Build a CuePlayer whose asset root is a tmp COVAS_APP_DIR and whose user root is
    content_root/sounds, populated per the given {type: [freqs]} maps."""
    app_root = tmp_path / "app"
    for ctype, freqs in asset_files.items():
        for i, f in enumerate(freqs):  # distinct lengths so rotation is observable by sample count
            _wav(app_root / "covas" / "assets" / "cues" / ctype / f"a{i}.wav", freq=f, ms=30 + 10 * i)
    data_root = tmp_path / "data"
    for ctype, freqs in user_files.items():
        for i, f in enumerate(freqs):
            _wav(data_root / "sounds" / ctype / f"u{i}.wav", freq=f, ms=30 + 10 * i)
    monkeypatch.setenv("COVAS_APP_DIR", str(app_root))
    cfg = {"audio": {"content_root": str(data_root), "mix_sample_rate": 16000}}
    return CuePlayer(cfg, mixer=_StubMixer(), sleep=sleep)


def test_cueplayer_loads_defaults_when_no_override(tmp_path, monkeypatch):
    p = _player_with(tmp_path, monkeypatch,
                     user_files={}, asset_files={"completed": [400, 500, 600]})
    assert len(p.cues["completed"]) == 3
    assert p.cues["failure"] == []          # neither -> silent


def test_cueplayer_override_wins(tmp_path, monkeypatch):
    p = _player_with(tmp_path, monkeypatch,
                     user_files={"listen": [880]}, asset_files={"listen": [200, 300]})
    assert len(p.cues["listen"]) == 1       # the single user file replaced the two defaults


def test_cueplayer_play_alias_and_rotation(tmp_path, monkeypatch):
    """play() maps legacy names (listening/done/failed) to types and draws across the whole set."""
    p = _player_with(tmp_path, monkeypatch,
                     user_files={}, asset_files={"completed": [400, 500, 600, 700]})
    import random
    random.seed(1234)
    seen = set()
    for _ in range(200):
        before = len(p._mixer.submitted)
        p.play("done")                      # alias -> "completed"
        assert len(p._mixer.submitted) == before + 1     # a cue was routed to the mixer
        seen.add(p._mixer.submitted[-1][1].shape[0])
    assert len(seen) >= 2                    # rotation drew more than one distinct file


def test_cueplayer_silent_type_is_noop(tmp_path, monkeypatch):
    p = _player_with(tmp_path, monkeypatch, user_files={}, asset_files={})
    p.play("failed")                         # no files anywhere -> no submit, no error
    assert p._mixer.submitted == []


# ---- looping "thinking" bed (issue #5): start/stop lifecycle --------------------------------

def test_thinking_is_a_cue_type_with_a_shipped_default():
    from covas.audio import CUE_TYPES, _CUE_ALIASES
    assert "thinking" in CUE_TYPES
    assert _CUE_ALIASES.get("working") == "thinking"     # intuitive synonym


def test_start_loop_repeats_until_stopped(tmp_path, monkeypatch):
    """The bed plays on repeat: with an injected (no-wait) sleep, it re-triggers each pass and
    keeps going until stop_loop() — here the 3rd pass stops it, so exactly 3 clips were routed."""
    import threading
    holder = {"n": 0, "player": None}
    done = threading.Event()

    def fake_sleep(_secs):
        holder["n"] += 1
        if holder["n"] >= 3:
            holder["player"].stop_loop()     # stop from within the loop thread (no self-join)
            done.set()

    p = _player_with(tmp_path, monkeypatch, user_files={},
                     asset_files={"thinking": [120, 140]}, sleep=fake_sleep)
    holder["player"] = p
    p.start_loop("thinking")
    assert done.wait(2.0)                     # loop reached its 3rd pass and stopped (no real time)
    assert len(p._mixer.submitted) == 3       # three clips routed to the bus, then it halted
    assert all(bus == "alert" for bus, _buf, _sr in p._mixer.submitted)
    assert p._loop_thread is None             # cleaned up


def test_start_loop_silent_type_spawns_no_thread(tmp_path, monkeypatch):
    p = _player_with(tmp_path, monkeypatch, user_files={}, asset_files={})  # no thinking files
    p.start_loop("thinking")
    assert p._loop_thread is None             # nothing to loop -> no thread, fail-soft
    assert p._mixer.submitted == []


def test_start_loop_is_idempotent(tmp_path, monkeypatch):
    """A second start_loop while one runs is a no-op (one bed at a time)."""
    import threading
    inside = threading.Event()
    gate = threading.Event()
    idents = set()

    def fake_sleep(_secs):
        idents.add(threading.current_thread().ident)
        inside.set()
        gate.wait(1.0)                        # hold the worker so we can try to start again

    p = _player_with(tmp_path, monkeypatch, user_files={},
                     asset_files={"thinking": [120]}, sleep=fake_sleep)
    p.start_loop("thinking")
    first = p._loop_thread
    assert inside.wait(2.0)                    # worker is running, parked in fake_sleep
    p.start_loop("thinking")                   # should NOT spawn a second worker
    assert p._loop_thread is first
    gate.set()
    p.stop_loop()
    assert idents == {first.ident}             # only ever one loop thread ran


def test_stop_loop_without_running_is_safe(tmp_path, monkeypatch):
    p = _player_with(tmp_path, monkeypatch, user_files={}, asset_files={"thinking": [100]})
    p.stop_loop()                             # nothing running -> no error, no submit
    assert p._mixer.submitted == []


# ---- reload(): re-scan + hot-swap without rebuilding the player (issue #109) -----------------
def test_reload_picks_up_an_added_and_a_removed_file(tmp_path, monkeypatch):
    """Dropping a new file into the user override folder and reloading joins the rotation; a file
    removed afterward drops out — all via the SAME CuePlayer instance, no restart."""
    p = _player_with(tmp_path, monkeypatch,
                     user_files={"listen": [200]}, asset_files={"listen": [900]})
    assert len(p.cues["listen"]) == 1           # the one user file (override wins)

    data_root = tmp_path / "data"
    added = _wav(data_root / "sounds" / "listen" / "u1.wav", freq=300)
    counts = p.reload()
    assert counts["listen"] == 2
    assert len(p.cues["listen"]) == 2

    Path(added).unlink()
    counts = p.reload()
    assert counts["listen"] == 1
    assert len(p.cues["listen"]) == 1


def test_reload_falls_back_to_default_when_user_folder_emptied(tmp_path, monkeypatch):
    p = _player_with(tmp_path, monkeypatch,
                     user_files={"completed": [700]}, asset_files={"completed": [400, 500]})
    assert len(p.cues["completed"]) == 1        # user override active

    data_root = tmp_path / "data"
    for f in (data_root / "sounds" / "completed").iterdir():
        f.unlink()
    counts = p.reload()
    assert counts["completed"] == 2             # empty override -> falls back to the 2 defaults
    assert len(p.cues["completed"]) == 2


def test_reload_skips_a_bad_file_without_crashing(tmp_path, monkeypatch):
    p = _player_with(tmp_path, monkeypatch,
                     user_files={"failure": [250]}, asset_files={})
    assert len(p.cues["failure"]) == 1

    data_root = tmp_path / "data"
    bad = data_root / "sounds" / "failure" / "corrupt.wav"
    bad.write_bytes(b"not a real wav file")
    counts = p.reload()
    assert counts["failure"] == 1               # the corrupt file was skipped, the good one kept
    assert len(p.cues["failure"]) == 1


def test_reload_recreates_a_deleted_user_folder(tmp_path, monkeypatch):
    """A folder deleted out from under the player is recreated (ensure_cue_skeleton runs in
    reload too), so a subsequent drop-in still has somewhere to land."""
    import shutil

    p = _player_with(tmp_path, monkeypatch, user_files={"thinking": [120]}, asset_files={})
    data_root = tmp_path / "data"
    shutil.rmtree(data_root / "sounds" / "thinking")
    counts = p.reload()
    assert counts["thinking"] == 0
    assert (data_root / "sounds" / "thinking").is_dir()   # skeleton recreated


def test_stop_also_stops_the_loop(tmp_path, monkeypatch):
    """The general stop() also tears the bed down (belt-and-braces on the cancel path)."""
    import threading
    import time as _time
    entered = threading.Event()

    def fake_sleep(_secs):
        entered.set()
        _time.sleep(0.005)                     # brief yield; worker re-checks stop each pass

    p = _player_with(tmp_path, monkeypatch, user_files={},
                     asset_files={"thinking": [120]}, sleep=fake_sleep)
    p.start_loop("thinking")
    assert entered.wait(2.0)
    p.stop()                                    # not stop_loop — the general stop tears it down too
    assert p._loop_thread is None
