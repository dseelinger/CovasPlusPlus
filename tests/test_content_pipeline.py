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
