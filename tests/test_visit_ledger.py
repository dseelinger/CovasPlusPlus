"""Unit tests for the visit ledger (issue #138) — pure, offline, free.

Covers recording arrivals from faked journal events with an INJECTED clock, the 24h/7d windows,
first-vs-repeat detection, milestone totals, rolloff of ancient recent-stamps, the location cap,
and fail-soft load/save round-trips.
"""
from __future__ import annotations

import calendar

from covas.ed.visit_ledger import ARRIVAL_EVENTS, VisitLedger, VisitStats, _parse_ts

_DAY = 86400.0


def _docked(system: str, station: str) -> dict:
    return {"event": "Docked", "StarSystem": system, "StationName": station}


def _jump(system: str) -> dict:
    return {"event": "FSDJump", "StarSystem": system}


# --- recording + first/repeat ---------------------------------------------------------

def test_first_dock_is_first_visit():
    led = VisitLedger()
    assert led.record_arrival(_docked("Deciat", "Farseer Inc"), when=1000.0) is True
    st = led.stats_for_station("Deciat", "Farseer Inc", now=1000.0)
    assert st.total == 1 and st.first_visit is True
    assert st.visits_24h == 1 and st.visits_7d == 1


def test_repeat_dock_is_not_first_visit():
    led = VisitLedger()
    led.record_arrival(_docked("Deciat", "Farseer Inc"), when=1000.0)
    led.record_arrival(_docked("Deciat", "Farseer Inc"), when=2000.0)
    st = led.stats_for_station("Deciat", "Farseer Inc", now=2000.0)
    assert st.total == 2 and st.first_visit is False


def test_station_key_is_case_and_space_insensitive():
    led = VisitLedger()
    led.record_arrival(_docked("Deciat", "Farseer Inc"), when=1000.0)
    st = led.stats_for_station("  deciat ", "FARSEER   inc", now=1000.0)
    assert st.total == 1


def test_unknown_station_before_any_visit_is_zero():
    led = VisitLedger()
    st = led.stats_for_station("Nowhere", "Nothing", now=1000.0)
    assert st == VisitStats() and st.total == 0 and st.first_visit is False


# --- 24h / 7d windows (injected clock) ------------------------------------------------

def test_visits_24h_counts_only_the_last_day():
    led = VisitLedger()
    base = 1_000_000.0
    # three visits: 30h ago, 10h ago, now
    led.record_arrival(_docked("Sol", "Abraham Lincoln"), when=base - 30 * 3600)
    led.record_arrival(_docked("Sol", "Abraham Lincoln"), when=base - 10 * 3600)
    led.record_arrival(_docked("Sol", "Abraham Lincoln"), when=base)
    st = led.stats_for_station("Sol", "Abraham Lincoln", now=base)
    assert st.total == 3
    assert st.visits_24h == 2         # the 30h-ago one is outside the window
    assert st.visits_7d == 3


def test_ten_docks_in_a_day_reads_as_ten():
    led = VisitLedger()
    base = 5_000_000.0
    for i in range(10):
        led.record_arrival(_docked("Deciat", "Farseer Inc"), when=base + i * 600)  # every 10 min
    st = led.stats_for_station("Deciat", "Farseer Inc", now=base + 9 * 600)
    assert st.total == 10 and st.visits_24h == 10


# --- system-grain arrivals ------------------------------------------------------------

def test_fsd_jump_records_system_not_station():
    led = VisitLedger()
    led.record_arrival(_jump("Deciat"), when=1000.0)
    assert led.stats_for_system("Deciat", now=1000.0).first_visit is True
    # a system jump does not create a station entry
    assert led.stats_for_station("Deciat", "Farseer Inc", now=1000.0).total == 0


def test_non_arrival_event_is_a_noop():
    led = VisitLedger()
    assert led.record_arrival({"event": "Scan", "StarSystem": "Sol"}, when=1000.0) is False
    assert "Docked" in ARRIVAL_EVENTS and "FSDJump" in ARRIVAL_EVENTS


def test_docked_without_station_name_is_a_noop():
    led = VisitLedger()
    assert led.record_arrival({"event": "Docked", "StarSystem": "Sol"}, when=1000.0) is False


# --- rolloff + bounding ---------------------------------------------------------------

def test_ancient_recent_stamps_roll_off_but_total_survives():
    led = VisitLedger(retention_days=30)
    old = 1_000_000.0
    led.record_arrival(_docked("Sol", "Abraham Lincoln"), when=old)          # 40 days before new
    new = old + 40 * _DAY
    led.record_arrival(_docked("Sol", "Abraham Lincoln"), when=new)
    st = led.stats_for_station("Sol", "Abraham Lincoln", now=new)
    # lifetime total is retained across the rolloff...
    assert st.total == 2
    # ...but only the in-window stamp counts toward the recency windows
    assert st.visits_7d == 1 and st.visits_24h == 1


def test_recent_list_is_capped():
    led = VisitLedger(max_recent=5, retention_days=3650)
    base = 2_000_000.0
    for i in range(20):
        led.record_arrival(_docked("Sol", "Abraham Lincoln"), when=base + i * 60)
    entry = led.entries()["stn::sol::abraham lincoln"]
    assert len(entry["recent"]) == 5           # bounded
    assert entry["total"] == 20                # but total keeps counting


def test_location_cap_evicts_least_recently_visited():
    led = VisitLedger(max_locations=2)
    led.record_arrival(_docked("A", "s1"), when=1000.0)
    led.record_arrival(_docked("B", "s2"), when=2000.0)
    led.record_arrival(_docked("C", "s3"), when=3000.0)   # evicts A (oldest last-seen)
    keys = set(led.entries().keys())
    assert "stn::a::s1" not in keys
    assert "stn::b::s2" in keys and "stn::c::s3" in keys


# --- persistence round-trip -----------------------------------------------------------

def test_save_and_load_round_trip(tmp_path):
    p = tmp_path / "visit_ledger.json"
    led = VisitLedger(path=p)
    led.record_arrival(_docked("Deciat", "Farseer Inc"), when=1000.0)
    led.record_arrival(_docked("Deciat", "Farseer Inc"), when=2000.0)
    reloaded = VisitLedger.load(p)
    st = reloaded.stats_for_station("Deciat", "Farseer Inc", now=2000.0)
    assert st.total == 2 and st.first_visit is False


def test_load_missing_file_is_empty():
    led = VisitLedger.load(None)
    assert led.entries() == {}
    led2 = VisitLedger.load("does-not-exist-anywhere.json")
    assert led2.entries() == {}


def test_load_corrupt_file_degrades_to_empty(tmp_path):
    p = tmp_path / "visit_ledger.json"
    p.write_text("{ not json", encoding="utf-8")
    led = VisitLedger.load(p)
    assert led.entries() == {}


# --- timestamp fallback ---------------------------------------------------------------

def test_records_from_event_timestamp_when_no_when_given():
    led = VisitLedger()
    ev = {"event": "Docked", "StarSystem": "Deciat", "StationName": "Farseer Inc",
          "timestamp": "2026-07-08T12:00:00Z"}
    led.record_arrival(ev)
    ts = _parse_ts("2026-07-08T12:00:00Z")
    assert ts is not None
    st = led.stats_for_station("Deciat", "Farseer Inc", now=ts)
    assert st.total == 1 and st.visits_24h == 1


# --- UTC timestamp parsing (issue #155) -----------------------------------------------

def test_parse_ts_is_true_utc_epoch_regardless_of_local_tz():
    """ED journal stamps are UTC wall-clock. _parse_ts must yield the TRUE UTC epoch (via
    calendar.timegm), NOT a local-time epoch (time.mktime) that skews by the machine's UTC
    offset. This equality is independent of the test runner's timezone."""
    stamp = "2026-07-08T12:00:00Z"
    expected_utc = float(calendar.timegm((2026, 7, 8, 12, 0, 0, 0, 0, 0)))
    assert _parse_ts(stamp) == expected_utc


def test_visit_window_uses_utc_not_local_when_no_when_given():
    """End-to-end guard for #155: record via the event's own `timestamp` (going through _parse_ts,
    NOT an explicit when= float) and query with a `time.time()`-style UTC `now`. The arrival must
    land in the correct 24h/7d windows regardless of the runner's local timezone. Before the fix,
    a non-UTC machine offset the stored epoch by its UTC offset and shifted these windows."""
    led = VisitLedger()
    # A fixed UTC arrival, and a UTC "now" one hour later — both are true UTC epochs.
    arrival_utc = float(calendar.timegm((2026, 7, 8, 12, 0, 0, 0, 0, 0)))
    ev = {"event": "Docked", "StarSystem": "Deciat", "StationName": "Farseer Inc",
          "timestamp": "2026-07-08T12:00:00Z"}
    led.record_arrival(ev)  # no when= -> exercises _parse_ts

    now = arrival_utc + 3600.0  # one hour after the arrival, in true UTC epoch seconds
    st = led.stats_for_station("Deciat", "Farseer Inc", now=now)
    assert st.total == 1
    assert st.visits_24h == 1   # within the last day
    assert st.visits_7d == 1

    # And it must fall OUT of the window once we are >24h / >7d past it (no spurious offset).
    st2 = led.stats_for_station("Deciat", "Farseer Inc", now=arrival_utc + 2 * _DAY)
    assert st2.visits_24h == 0 and st2.visits_7d == 1
    st3 = led.stats_for_station("Deciat", "Farseer Inc", now=arrival_utc + 8 * _DAY)
    assert st3.visits_24h == 0 and st3.visits_7d == 0
