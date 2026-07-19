"""Unit tests for ED journal parsing + tailing (DESIGN §5, §9).

Pure logic and the tail state machine, all offline: NDJSON parsing incl. half-written
lines, journal-event -> context folding, and JournalWatcher's drain/rollover behavior
driven synchronously against real temp files (no thread, no ED, no network).
"""
from __future__ import annotations

from pathlib import Path

from covas.ed import EDContext, JournalWatcher
from covas.ed.journal import (apply_journal_event, apply_scan_organic, default_journal_dir,
                              describe_journal_event, parse_journal_line,
                              resolve_journal_dir, srv_hull_transitions)
from covas.ed.modes import MODE_MAINSHIP, MODE_SRV
from covas.events import EventBus

FIXTURES = Path(__file__).parent / "fixtures" / "ed"


def _lines() -> list[str]:
    return (FIXTURES / "journal_sample.log").read_text(encoding="utf-8").splitlines()


# --- parse_journal_line ----------------------------------------------------

def test_parse_valid_line():
    ev = parse_journal_line('{"event":"FSDJump","StarSystem":"Sol"}')
    assert ev == {"event": "FSDJump", "StarSystem": "Sol"}


def test_parse_blank_line_is_none():
    assert parse_journal_line("   ") is None
    assert parse_journal_line("") is None


def test_parse_half_written_line_is_none():
    # A truncated final line (caught mid-flush) must not raise — returns None to retry.
    assert parse_journal_line('{"event":"FSDJump","StarSy') is None


def test_parse_non_object_json_is_none():
    assert parse_journal_line("[1, 2, 3]") is None


# --- apply_journal_event ---------------------------------------------------

def test_load_game_sets_ship_and_fuel():
    ctx = EDContext()
    apply_journal_event(ctx, {"event": "LoadGame", "Ship": "anaconda",
                              "ShipName": "Void Runner", "FuelLevel": 24.0,
                              "FuelCapacity": 32.0})
    s = ctx.snapshot()
    assert s["ship"] == "Anaconda"          # internal id title-cased
    assert s["ship_name"] == "Void Runner"
    assert s["fuel_main"] == 24.0 and s["fuel_capacity"] == 32.0
    assert s["fuel_pct"] == 75.0


def test_localised_ship_name_preferred():
    ctx = EDContext()
    apply_journal_event(ctx, {"event": "LoadGame", "Ship": "anaconda",
                              "Ship_Localised": "Anaconda"})
    assert ctx.snapshot()["ship"] == "Anaconda"


def test_load_game_captures_raw_ship_symbol():
    # ship_symbol (#117) is the RAW internal symbol, not the title-cased display name —
    # ed.ships.ship_pad_size looks it up case-insensitively.
    ctx = EDContext()
    apply_journal_event(ctx, {"event": "LoadGame", "Ship": "python_nx",
                              "Ship_Localised": "Python MkII"})
    s = ctx.snapshot()
    assert s["ship"] == "Python MkII"          # display name, unchanged (already mixed-case)
    assert s["ship_symbol"] == "python_nx"     # raw internal symbol, the lookup key


def test_loadout_captures_raw_ship_symbol():
    ctx = EDContext()
    apply_journal_event(ctx, {"event": "Loadout", "Ship": "federation_corvette",
                              "Ship_Localised": "Federal Corvette", "ShipName": "Vigil"})
    s = ctx.snapshot()
    assert s["ship_symbol"] == "federation_corvette"
    assert s["ship_name"] == "Vigil"


def test_ship_symbol_none_until_loadout():
    ctx = EDContext()
    assert ctx.snapshot()["ship_symbol"] is None


def test_docked_then_undocked():
    ctx = EDContext()
    apply_journal_event(ctx, {"event": "Docked", "StationName": "Jameson Memorial",
                              "StarSystem": "Shinrarta Dezhra"})
    s = ctx.snapshot()
    assert s["docked"] is True and s["station"] == "Jameson Memorial"
    assert s["system"] == "Shinrarta Dezhra"

    apply_journal_event(ctx, {"event": "Undocked", "StationName": "Jameson Memorial"})
    s = ctx.snapshot()
    assert s["docked"] is False and s["station"] is None


def test_fsd_jump_updates_system_and_clears_dock():
    ctx = EDContext()
    ctx.update(docked=True, station="Somewhere")
    apply_journal_event(ctx, {"event": "FSDJump", "StarSystem": "Sol", "FuelLevel": 20.5})
    s = ctx.snapshot()
    assert s["system"] == "Sol" and s["docked"] is False and s["station"] is None
    assert s["fuel_main"] == 20.5


def test_fuel_scoop_updates_fuel():
    ctx = EDContext()
    apply_journal_event(ctx, {"event": "FuelScoop", "Scooped": 2.0, "Total": 22.5})
    assert ctx.snapshot()["fuel_main"] == 22.5


def test_unhandled_event_returns_empty_patch():
    ctx = EDContext()
    assert apply_journal_event(ctx, {"event": "ReceiveText", "Message": "hi"}) == {}


# --- SRV hull + exobiology (#54) -------------------------------------------

def test_launch_and_dock_srv_track_hull():
    ctx = EDContext()
    apply_journal_event(ctx, {"event": "LaunchSRV", "Loadout": "starter"})
    assert ctx.snapshot()["srv_hull"] == 1.0          # fresh SRV at full hull
    apply_journal_event(ctx, {"event": "DockSRV"})
    assert ctx.snapshot()["srv_hull"] is None         # back in the ship, cleared


def test_hull_damage_is_srv_only_in_srv_mode():
    ctx = EDContext()
    # In the SRV, HullDamage.Health is the SRV's hull.
    ctx.update(game_mode=MODE_SRV)
    patch = apply_journal_event(ctx, {"event": "HullDamage", "Health": 0.4})
    assert patch["srv_hull"] == 0.4 and ctx.snapshot()["srv_hull"] == 0.4
    # In the main ship, the same event must NOT be read as SRV hull.
    ctx2 = EDContext()
    ctx2.update(game_mode=MODE_MAINSHIP)
    patch2 = apply_journal_event(ctx2, {"event": "HullDamage", "Health": 0.4})
    assert "srv_hull" not in patch2 and ctx2.snapshot()["srv_hull"] is None


def test_srv_hull_transitions_downward_crossing_only():
    assert srv_hull_transitions(1.0, 0.2) == ["SrvHullLow"]
    assert srv_hull_transitions(0.2, 0.1) == []        # already low, no re-alert
    assert srv_hull_transitions(None, 0.1) == []       # unknown prior = silent baseline
    assert srv_hull_transitions(0.5, None) == []       # cleared (DockSRV) = no alert


def test_scan_organic_tracks_sample_count():
    ctx = EDContext()
    apply_scan_organic(ctx, {"event": "ScanOrganic", "ScanType": "Log",
                             "Genus_Localised": "Bacterium"})
    assert ctx.bio_scan() == {"genus": "Bacterium", "species": None,
                              "samples": 1, "required": 3}
    apply_scan_organic(ctx, {"event": "ScanOrganic", "ScanType": "Sample",
                             "Genus_Localised": "Bacterium"})
    assert ctx.bio_scan()["samples"] == 2
    apply_scan_organic(ctx, {"event": "ScanOrganic", "ScanType": "Analyse",
                             "Genus_Localised": "Bacterium"})
    assert ctx.bio_scan()["samples"] == 3              # complete (samples == required)


def test_scan_organic_ignores_unknown_scan_type():
    ctx = EDContext()
    apply_scan_organic(ctx, {"event": "ScanOrganic", "ScanType": "Nonsense"})
    assert ctx.bio_scan() is None


def test_describe_scan_organic_phrases():
    assert describe_journal_event(
        {"event": "ScanOrganic", "ScanType": "Sample", "Genus_Localised": "Bacterium"}
    ) == "Sample 2 of 3 of Bacterium logged — one more to analyse"
    assert describe_journal_event(
        {"event": "ScanOrganic", "ScanType": "Analyse", "Genus_Localised": "Bacterium"}
    ) == "Analysed Bacterium — exobiology sample complete"
    # A ScanType we don't recognise isn't logged as a bare event.
    assert describe_journal_event({"event": "ScanOrganic", "ScanType": "Nope"}) is None


def test_describe_srv_events():
    assert describe_journal_event({"event": "LaunchSRV"}) == "Deployed the SRV"
    assert describe_journal_event({"event": "DockSRV"}) == "Docked the SRV"


def test_watcher_publishes_srv_hull_low(tmp_path):
    jf = tmp_path / "Journal.2026-07-08T120000.01.log"
    jf.write_text("", encoding="utf-8")
    w, ctx, q = _watcher(tmp_path)
    w._open(jf, prime=True)
    ctx.update(game_mode=MODE_SRV)
    with open(jf, "a", encoding="utf-8") as f:
        f.write('{"event":"LaunchSRV"}\n')                       # baseline hull 1.0
        f.write('{"timestamp":"2026-07-08T12:05:00Z","event":"HullDamage","Health":0.2}\n')
    w._drain()
    events = [e["event"] for e in _drain_queue(q) if e.get("type") == "ed_event"]
    assert "SrvHullLow" in events
    assert "SRV hull getting low" in [e["desc"] for e in ctx.recent()]


# --- describe_journal_event (recent-events feed) ---------------------------

def test_describe_notable_events():
    assert describe_journal_event({"event": "FSDJump", "StarSystem": "Sol"}) == "Jumped to Sol"
    assert describe_journal_event(
        {"event": "Docked", "StationName": "Jameson Memorial"}) == "Docked at Jameson Memorial"
    assert describe_journal_event(
        {"event": "MissionCompleted", "LocalisedName": "Deliver widgets"}
    ) == "Completed mission: Deliver widgets"


def test_describe_skips_spammy_events():
    assert describe_journal_event({"event": "FuelScoop", "Total": 22.5}) is None
    assert describe_journal_event({"event": "Bounty"}) is None
    # Auto-scan pings are skipped; only a deliberate detailed scan is logged.
    assert describe_journal_event({"event": "Scan", "ScanType": "AutoScan",
                                   "BodyName": "Sol 4"}) is None
    assert describe_journal_event({"event": "Scan", "ScanType": "Detailed",
                                   "BodyName": "Sol 4"}) == "Scanned Sol 4"


def test_replaying_fixture_builds_expected_context():
    ctx = EDContext()
    for line in _lines():
        ev = parse_journal_line(line)
        if ev:
            apply_journal_event(ctx, ev)
    s = ctx.snapshot()
    # Ends docked at Abraham Lincoln in Sol, having scooped to 22.5t.
    assert s["system"] == "Sol"
    assert s["docked"] is True and s["station"] == "Abraham Lincoln"
    assert s["ship"] == "Anaconda" and s["ship_name"] == "Void Runner"
    assert s["fuel_main"] == 22.5 and s["fuel_capacity"] == 32.0


# --- journal dir resolution ------------------------------------------------

def test_resolve_journal_dir_default_when_blank():
    assert resolve_journal_dir({}) == default_journal_dir()
    assert resolve_journal_dir({"elite": {"journal_dir": ""}}) == default_journal_dir()


def test_resolve_journal_dir_uses_configured():
    got = resolve_journal_dir({"elite": {"journal_dir": r"D:\ed\journals"}})
    assert got == Path(r"D:\ed\journals")


def test_default_journal_dir_shape():
    # Standard Frontier layout, resolved under the user's home (no hardcoded username).
    d = default_journal_dir()
    assert d.parts[-3:] == ("Saved Games", "Frontier Developments", "Elite Dangerous")


# --- JournalWatcher tail state machine (synchronous, offline) ---------------

def _watcher(tmp_path):
    bus = EventBus()
    q = bus.subscribe()
    ctx = EDContext()
    return JournalWatcher(tmp_path, bus, ctx, poll_interval=0.01), ctx, q


def _drain_queue(q) -> list[dict]:
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def test_watcher_primes_without_publishing_then_tails(tmp_path):
    jf = tmp_path / "Journal.2026-07-08T120000.01.log"
    jf.write_text(
        '{"event":"LoadGame","Ship":"anaconda","FuelLevel":24.0,"FuelCapacity":32.0}\n'
        '{"event":"Docked","StationName":"Jameson Memorial","StarSystem":"Shinrarta Dezhra"}\n',
        encoding="utf-8")
    w, ctx, q = _watcher(tmp_path)

    w._open(jf, prime=True)                 # replay existing lines silently
    assert ctx.snapshot()["docked"] is True         # context warmed
    assert [e for e in _drain_queue(q) if e.get("type") == "ed_event"] == []  # nothing published

    # Now a new line is appended -> tail publishes it.
    with open(jf, "a", encoding="utf-8") as f:
        f.write('{"event":"Undocked","StationName":"Jameson Memorial"}\n')
    w._drain()
    events = [e for e in _drain_queue(q) if e.get("type") == "ed_event"]
    assert len(events) == 1 and events[0]["event"] == "Undocked"
    assert ctx.snapshot()["docked"] is False


def test_watcher_buffers_half_written_line(tmp_path):
    jf = tmp_path / "Journal.2026-07-08T120000.01.log"
    jf.write_text("", encoding="utf-8")
    w, ctx, q = _watcher(tmp_path)
    w._open(jf, prime=True)

    # Write a partial line (no trailing newline) -> must NOT be processed yet.
    with open(jf, "a", encoding="utf-8") as f:
        f.write('{"event":"FSDJump","StarSy')
    w._drain()
    assert [e for e in _drain_queue(q) if e.get("type") == "ed_event"] == []

    # Complete the line -> now it parses and publishes.
    with open(jf, "a", encoding="utf-8") as f:
        f.write('stem":"Sol"}\n')
    w._drain()
    events = [e for e in _drain_queue(q) if e.get("type") == "ed_event"]
    assert len(events) == 1 and events[0]["event"] == "FSDJump"
    assert ctx.snapshot()["system"] == "Sol"


def test_watcher_buffers_half_written_line_across_prime_boundary(tmp_path):
    # #161: the PRIME (startup) open must buffer a half-written FINAL line exactly like _drain,
    # so an event straddling the startup boundary is folded ONCE it's complete — not parsed as a
    # truncated "complete" line, dropped, and the file position advanced past it (which would lose
    # the event until the next full one self-corrected).
    jf = tmp_path / "Journal.2026-07-08T120000.01.log"
    # A complete line, then a PARTIAL final line with no trailing newline — present at startup.
    jf.write_text(
        '{"event":"LoadGame","Ship":"anaconda","FuelLevel":24.0,"FuelCapacity":32.0}\n'
        '{"event":"FSDJump","StarSy',
        encoding="utf-8")
    w, ctx, q = _watcher(tmp_path)

    w._open(jf, prime=True)                          # replay complete lines, BUFFER the partial
    assert ctx.snapshot()["ship"] == "Anaconda"      # the complete primed line warmed context
    assert ctx.snapshot()["system"] is None          # the partial FSDJump was NOT (mis)folded
    assert [e for e in _drain_queue(q) if e.get("type") == "ed_event"] == []  # prime publishes nothing

    # ED flushes the rest of the straddling line -> the first tail drain completes + folds it once.
    with open(jf, "a", encoding="utf-8") as f:
        f.write('stem":"Sol"}\n')
    w._drain()
    events = [e["event"] for e in _drain_queue(q) if e.get("type") == "ed_event"]
    assert events == ["FSDJump"]                     # folded exactly once, not dropped
    assert ctx.snapshot()["system"] == "Sol"


def test_watcher_rolls_over_to_newer_file(tmp_path):
    old = tmp_path / "Journal.2026-07-08T120000.01.log"
    old.write_text('{"event":"FSDJump","StarSystem":"Sol"}\n', encoding="utf-8")
    w, ctx, q = _watcher(tmp_path)
    w._open(old, prime=False)               # read from the top, publishing
    w._drain()
    _drain_queue(q)

    # A newer part appears; opening it fresh (prime=False) publishes all its lines.
    new = tmp_path / "Journal.2026-07-08T120500.02.log"
    new.write_text('{"event":"Docked","StationName":"Abraham Lincoln","StarSystem":"Sol"}\n',
                   encoding="utf-8")
    assert w._newest() == new               # newest picked by mtime
    w._open(new, prime=False)
    w._drain()
    events = [e for e in _drain_queue(q) if e.get("type") == "ed_event"]
    assert [e["event"] for e in events] == ["Docked"]
    assert ctx.snapshot()["station"] == "Abraham Lincoln"


def test_watcher_feeds_recent_log_on_prime_and_tail(tmp_path):
    jf = tmp_path / "Journal.2026-07-08T120000.01.log"
    jf.write_text(
        '{"timestamp":"2026-07-08T12:00:00Z","event":"FSDJump","StarSystem":"Sol"}\n',
        encoding="utf-8")
    w, ctx, q = _watcher(tmp_path)
    w._open(jf, prime=True)                  # primed events warm the feed (not published)
    assert [e["desc"] for e in ctx.recent()] == ["Jumped to Sol"]

    with open(jf, "a", encoding="utf-8") as f:
        f.write('{"timestamp":"2026-07-08T12:07:00Z","event":"Docked",'
                '"StationName":"Abraham Lincoln"}\n')
    w._drain()
    assert [e["desc"] for e in ctx.recent()] == ["Jumped to Sol", "Docked at Abraham Lincoln"]
    # The tailed one was also published; the primed one was not.
    published = [e["event"] for e in _drain_queue(q) if e.get("type") == "ed_event"]
    assert published == ["Docked"]


def test_published_event_is_flat_ed_event(tmp_path):
    jf = tmp_path / "Journal.2026-07-08T120000.01.log"
    jf.write_text("", encoding="utf-8")
    w, ctx, q = _watcher(tmp_path)
    w._open(jf, prime=True)
    with open(jf, "a", encoding="utf-8") as f:
        f.write('{"event":"FSDJump","StarSystem":"Sol"}\n')
    w._drain()
    ev = [e for e in _drain_queue(q) if e.get("type") == "ed_event"][0]
    # Flat shape: type stamped, raw fields preserved.
    assert ev["type"] == "ed_event" and ev["event"] == "FSDJump" and ev["StarSystem"] == "Sol"


# --- fail-soft: one bad event/stat() race can't kill monitoring (#152) ------

def test_drain_skips_raising_event_and_keeps_tailing(tmp_path, monkeypatch):
    # A single event whose apply raises (e.g. a fail-loud ctx.update KeyError) must be
    # reported via on_error and skipped — NOT propagate out of _drain and stop the tail.
    import covas.ed.journal as journal_mod
    real_apply = journal_mod.apply_journal_event

    def boom(ctx, ev):
        if ev.get("event") == "Poison":
            raise KeyError("simulated fail-loud apply")
        return real_apply(ctx, ev)
    monkeypatch.setattr(journal_mod, "apply_journal_event", boom)

    jf = tmp_path / "Journal.2026-07-08T120000.01.log"
    jf.write_text("", encoding="utf-8")
    bus = EventBus()
    q = bus.subscribe()
    ctx = EDContext()
    errors: list[Exception] = []
    w = JournalWatcher(tmp_path, bus, ctx, poll_interval=0.01, on_error=errors.append)
    w._open(jf, prime=True)

    with open(jf, "a", encoding="utf-8") as f:
        f.write('{"event":"Poison"}\n')                       # this apply raises
        f.write('{"event":"FSDJump","StarSystem":"Sol"}\n')   # the NEXT good event
    w._drain()                                                # must NOT raise

    # The bad event was surfaced once via on_error; the good one still processed + published.
    assert len(errors) == 1 and isinstance(errors[0], KeyError)
    events = [e["event"] for e in _drain_queue(q) if e.get("type") == "ed_event"]
    assert events == ["FSDJump"]
    assert ctx.snapshot()["system"] == "Sol"


def test_newest_survives_file_vanishing_between_glob_and_stat(tmp_path):
    # A journal file can disappear (rollover/cleanup) between the glob listing it and the
    # stat() that reads its mtime; the resulting OSError must be swallowed to None, not
    # propagate to run() and kill the watcher thread.
    w, ctx, q = _watcher(tmp_path)
    missing = tmp_path / "Journal.2026-07-08T120000.99.log"   # never created

    class _VanishingDir:
        def glob(self, pattern):
            return [missing]                                  # listed, but already gone
    w.dir = _VanishingDir()

    # max()'s key calls missing.stat() -> FileNotFoundError (an OSError) -> caught -> None.
    assert w._newest() is None
