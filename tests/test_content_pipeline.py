"""Unit tests for the C11 drop-in content pipeline. Offline, no device/network."""
from __future__ import annotations

from covas.mixer import (
    Cue,
    ContentBundle,
    content_status,
    ensure_skeleton,
    load_content,
    parse_line_file,
    status_summary,
)
from covas.mixer.chatter import chatter_cues
from covas.mixer.content import merged_music_library, overlay_cues, threat_lines
from covas.mixer.example_cues import DEFAULT_STING, InterdictionCue, sfx_cues


def _write(path, text=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _tree(base):
    """A representative drop-in tree under `base`."""
    _write(base / "audio" / "sfx" / "thargoid_voices" / "a.wav")
    _write(base / "audio" / "sfx" / "thargoid_voices" / "b.ogg")
    _write(base / "audio" / "sfx" / "interdiction_sting" / "sting.wav")
    _write(base / "audio" / "music" / "deep_space" / "track1.ogg")
    _write(base / "content" / "chatter" / "station_traffic.txt",
           "# a comment\nTraffic is heavy today.\n\n  Docking lanes are full.  \n# end\n")
    _write(base / "content" / "interdiction_threat.txt", "Contact, hostile.\n# note\nBrace!\n")


# ---- line parsing --------------------------------------------------------------------------

def test_parse_line_file(tmp_path):
    f = tmp_path / "c.txt"
    f.write_text("# comment\nfirst line\n\n   second line   \n#another\n", encoding="utf-8")
    assert parse_line_file(f) == ["first line", "second line"]
    assert parse_line_file(tmp_path / "missing.txt") == []   # missing -> silent, no error


# ---- scanning ------------------------------------------------------------------------------

def test_load_content_maps_the_tree(tmp_path):
    _tree(tmp_path)
    b = load_content(tmp_path)
    assert [p.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for p in b.sfx["thargoid_voices"]] == ["a.wav", "b.ogg"]
    assert len(b.sfx["interdiction_sting"]) == 1
    assert b.sfx["space_radiation"] == []                    # folder absent -> silent
    assert len(b.music["deep_space"]) == 1 and b.music["nebula"] == []
    assert b.chatter["station_traffic"] == ["Traffic is heavy today.", "Docking lanes are full."]
    assert b.chatter["populated_musing"] == []              # no file -> falls back to default later
    assert b.threat == ["Contact, hostile.", "Brace!"]


def test_offered_music_contexts_are_all_reachable(tmp_path):
    """Every music folder we scan/offer must be a context music_context() can actually return
    (or the documented `nebula` library seam) — otherwise tracks dropped there are silently
    unreachable. Regression guard for issue #160: `unpopulated`/`scooping_fuel` are state tokens
    that music_context() folds into `deep_space`/`near_star`, never returns."""
    from covas.mixer import MUSIC_CONTEXTS as MUSIC_MODULE_CONTEXTS
    from covas.mixer.content import MUSIC_CONTEXTS as CONTENT_CONTEXTS
    from covas.mixer.eligibility import STATES
    from covas.mixer.music import music_context

    # content.py no longer keeps a private, drifted copy — it reuses the authoritative tuple.
    assert CONTENT_CONTEXTS is MUSIC_MODULE_CONTEXTS
    assert "unpopulated" not in CONTENT_CONTEXTS
    assert "scooping_fuel" not in CONTENT_CONTEXTS

    # Drive music_context() with every single live state token and collect the contexts it yields;
    # each offered context must be either produced by the mapping or the reserved `nebula` tag.
    reachable = {music_context(frozenset())}  # the default fallback
    reachable |= {music_context({tok}) for tok in STATES}
    for ctx in CONTENT_CONTEXTS:
        assert ctx in reachable or ctx == "nebula", f"offered context {ctx!r} is unreachable"


def test_scan_does_not_offer_folded_away_folders(tmp_path):
    """A track dropped in an `unpopulated`/`scooping_fuel` folder is not scanned at all — those
    contexts aren't offered, so the bundle carries no such keys (they'd never play)."""
    _write(tmp_path / "audio" / "music" / "unpopulated" / "u.ogg")
    _write(tmp_path / "audio" / "music" / "scooping_fuel" / "s.ogg")
    _write(tmp_path / "audio" / "music" / "near_star" / "n.ogg")
    b = load_content(tmp_path)
    assert "unpopulated" not in b.music and "scooping_fuel" not in b.music
    assert len(b.music["near_star"]) == 1                    # a real, reachable context still works


def test_skeleton_only_creates_reachable_music_folders(tmp_path):
    """ensure_skeleton() must not invite content into dead folders."""
    ensure_skeleton(tmp_path)
    music_root = tmp_path / "audio" / "music"
    assert not (music_root / "unpopulated").exists()
    assert not (music_root / "scooping_fuel").exists()
    assert (music_root / "deep_space" / "README.md").is_file()
    assert (music_root / "near_star" / "README.md").is_file()


def test_load_content_empty_base_is_all_silent(tmp_path):
    b = load_content(tmp_path)                                # nothing dropped in
    assert all(v == [] for v in b.sfx.values())
    assert all(v == [] for v in b.music.values())
    assert all(v == [] for v in b.chatter.values())
    assert b.threat == []


# ---- overlay onto cues ---------------------------------------------------------------------

def test_overlay_replaces_samples_and_phrasings_when_present(tmp_path):
    _tree(tmp_path)
    b = load_content(tmp_path)
    cues = overlay_cues(list(chatter_cues()) + list(sfx_cues({})), b)
    by_name = {c.name: c for c in cues}
    assert len(by_name["thargoid_voices"].samples) == 2      # folder samples applied
    assert by_name["station_traffic"].phrasings == ("Traffic is heavy today.", "Docking lanes are full.")
    # a category with no drop-in file keeps its shipped default pool
    assert by_name["populated_musing"].phrasings == next(
        c.phrasings for c in chatter_cues() if c.name == "populated_musing")


def test_merged_music_library_combines_config_and_folder(tmp_path):
    _tree(tmp_path)
    b = load_content(tmp_path)
    cfg = {"music": {"tracks": {"deep_space": ["config_track.ogg"]}}}
    lib = merged_music_library(cfg, b)
    tracks = lib.tracks_for("deep_space")
    assert "config_track.ogg" in tracks and any(t.endswith("track1.ogg") for t in tracks)


def test_threat_lines_override_default(tmp_path):
    _tree(tmp_path)
    b = load_content(tmp_path)
    assert threat_lines(b, ("BUILTIN",)) == ("Contact, hostile.", "Brace!")
    assert threat_lines(load_content(tmp_path / "empty"), ("BUILTIN",)) == ("BUILTIN",)


# ---- content status ------------------------------------------------------------------------

def test_content_status_states(tmp_path):
    _tree(tmp_path)
    rows = {(r["kind"], r["name"]): r for r in content_status(load_content(tmp_path))}
    assert rows[("sfx", "thargoid_voices")]["state"] == "custom" and rows[("sfx", "thargoid_voices")]["count"] == 2
    assert rows[("sfx", "space_radiation")]["state"] == "silent"          # no default, no files
    assert rows[("chatter", "station_traffic")]["state"] == "custom"
    assert rows[("chatter", "populated_musing")]["state"] == "default"   # built-in pool
    assert rows[("threat", "interdiction_threat")]["state"] == "custom"


def test_status_summary_mentions_silent_and_counts(tmp_path):
    _tree(tmp_path)
    s = status_summary(load_content(tmp_path))
    assert "populated" in s and "silent" in s


# ---- skeleton ------------------------------------------------------------------------------

def test_ensure_skeleton_creates_dirs_and_readmes(tmp_path):
    ensure_skeleton(tmp_path)
    assert (tmp_path / "audio" / "sfx" / "thargoid_voices" / "README.md").is_file()
    assert (tmp_path / "audio" / "music" / "deep_space" / "README.md").is_file()
    assert (tmp_path / "content" / "chatter" / "README.md").is_file()
    assert (tmp_path / "content" / "interdiction_threat.txt").is_file()
    # idempotent + doesn't clobber user content
    user = tmp_path / "content" / "chatter" / "station_traffic.txt"
    user.write_text("my line\n", encoding="utf-8")
    ensure_skeleton(tmp_path)
    assert user.read_text(encoding="utf-8") == "my line\n"
    # a generated skeleton yields no drop-in content (comment-only files parse to nothing)
    assert load_content(tmp_path).chatter["station_traffic"] == ["my line"]
    assert load_content(tmp_path).threat == []


# ---- interdiction sting rotation -----------------------------------------------------------

def test_interdiction_sting_samples_rotate_then_fall_back():
    emitted = []
    cue = InterdictionCue(lambda layer: emitted.append(layer.payload) or True,
                          sting_samples=("s1.wav", "s2.wav"))
    cue.on_event({"event": "Interdiction"})
    cue.on_event({"event": "Interdiction"})
    stings = [emitted[0], emitted[3]]          # layer 0 of each sequence is the sting
    assert stings == ["s1.wav", "s2.wav"]
    # no sample set -> the single default sting
    e2 = []
    InterdictionCue(lambda layer: e2.append(layer.payload) or True).on_event({"event": "Interdiction"})
    assert e2[0] == DEFAULT_STING


def test_content_bundle_type_is_frozen():
    b = ContentBundle({}, {}, {}, [])
    assert isinstance(b, ContentBundle)
    assert overlay_cues([Cue("x", "comms", {"docked"})], b)[0].name == "x"   # no-op on empty


# ---- composed-object swap seams for the live reload (issue #110) ----------------------------

def test_registry_replace_all_is_atomic_and_swaps_the_set():
    from covas.mixer import CueRegistry
    reg = CueRegistry([Cue("a", "comms", {"docked"}), Cue("b", "comms", {"docked"})])
    reg.replace_all([Cue("a", "comms", {"docked"}, phrasings=("new",)), Cue("c", "comms", {"docked"})])
    assert [c.name for c in reg.cues()] == ["a", "c"]          # whole set swapped, in order
    assert reg.get("a").phrasings == ("new",) and reg.get("b") is None and reg.get("c") is not None


def test_registry_replace_all_all_or_nothing_on_bad_set():
    from covas.mixer import CueRegistry
    reg = CueRegistry([Cue("a", "comms", {"docked"})])
    # a duplicate name in the new set is refused BEFORE any rebind -> old set intact
    try:
        reg.replace_all([Cue("x", "comms", {"docked"}), Cue("x", "comms", {"docked"})])
        raised = False
    except ValueError:
        raised = True
    assert raised and [c.name for c in reg.cues()] == ["a"]


def test_music_director_set_library_keeps_current_track():
    from covas.mixer import MusicDirector, MusicLibrary
    d = MusicDirector(MusicLibrary({"populated": ["p1.ogg"]}), enabled=True)
    first = d.update({"populated"})
    assert first is not None and d.current_track == "p1.ogg"
    d.set_library(MusicLibrary({"populated": ["p2.ogg"], "deep_space": ["d.ogg"]}))
    # same context -> NO transition, the current track keeps playing (crossfade not interrupted)
    assert d.update({"populated"}) is None and d.current_track == "p1.ogg"
    # a genuine context change now picks up the new library
    assert d.update({"deep_space"}).to_track == "d.ogg"


def test_interdiction_set_content_keeps_rotation_and_governor():
    emitted = []
    cue = InterdictionCue(lambda layer: emitted.append(layer.payload) or True,
                          sting_samples=("s1.wav",), threat_lines=("old threat",))
    cue.on_event({"event": "Interdiction"})
    assert emitted[1] == "old threat"                          # layer 1 is the threat line
    cue.set_content(sting_samples=("s2.wav",), threat_lines=("new threat",))
    emitted.clear()
    cue.on_event({"event": "Interdiction"})
    assert emitted[0] == "s2.wav" and emitted[1] == "new threat"   # new content in play
    # threat_lines=None leaves the current pool unchanged
    cue.set_content(sting_samples=())
    assert cue._threat == ("new threat",)                      # noqa: SLF001
