"""Unit tests for fleet-carrier context voices (issue #19) — offline, no device/network.

Covers the whole chain: the "at/near my own carrier" context (EDContext predicates + journal
capture), the eligibility tokens + engine folding, the config parsing, the cue definitions, the
name templating, the CarrierPlayer routing, and the AudioLayer end-to-end wiring.
"""
from __future__ import annotations

from covas.ed.context import EDContext
from covas.ed.journal import apply_carrier_event, apply_journal_event
from covas.mixer import (
    AudioLayer,
    BusMixer,
    CaptainDedup,
    CarrierEventResponder,
    CarrierPlayer,
    CueRegistry,
    EligibilityEngine,
    apply_names,
    build_carrier_config,
    carrier_cues,
    carrier_event_cues,
)
from covas.mixer.carrier import CAPTAIN, CHATTER, TOWER
from covas.mixer.eligibility import AT_OWN_CARRIER, NEAR_OWN_CARRIER, STATES, unknown_states
from covas.mixer.voices import Voice


# ============================================================================================
# 1. Context detection — EDContext predicates + journal field capture
# ============================================================================================

def _ctx_docked_at(*, market_id, carrier_id, station_type="FleetCarrier",
                   system="Sol", carrier_system="Sol") -> EDContext:
    ctx = EDContext()
    ctx.update(docked=True, station="K7X-B0X", system=system,
               docked_station_type=station_type, docked_market_id=market_id)
    ctx.update_carrier(carrier_id=carrier_id, carrier_name="Nomad",
                       carrier_callsign="K7X-B0X", carrier_system=carrier_system)
    return ctx


def test_at_own_carrier_true_when_docked_market_matches_owned_id():
    ctx = _ctx_docked_at(market_id=3700005632, carrier_id=3700005632)
    assert ctx.at_own_carrier() is True
    assert ctx.near_own_carrier() is True         # docked at it -> same system too


def test_at_own_carrier_false_for_a_different_carrier():
    # Docked at a FleetCarrier, but its MarketID isn't ours (e.g. a squadron/other carrier).
    ctx = _ctx_docked_at(market_id=9999, carrier_id=3700005632)
    assert ctx.at_own_carrier() is False


def test_at_own_carrier_false_at_a_normal_station():
    ctx = _ctx_docked_at(market_id=3700005632, carrier_id=3700005632, station_type="Coriolis")
    assert ctx.at_own_carrier() is False


def test_at_own_carrier_false_when_owner_id_unknown():
    ctx = EDContext()
    ctx.update(docked=True, docked_station_type="FleetCarrier", docked_market_id=3700005632)
    assert ctx.at_own_carrier() is False          # no CarrierStats seen yet -> can't confirm


def test_at_own_carrier_false_when_not_docked():
    ctx = _ctx_docked_at(market_id=3700005632, carrier_id=3700005632)
    ctx.update(docked=False, docked_station_type=None, docked_market_id=None)
    assert ctx.at_own_carrier() is False


def test_near_own_carrier_tracks_system_match_only():
    ctx = EDContext()
    ctx.update_carrier(carrier_system="Shinrarta Dezhra")
    ctx.update(system="Shinrarta Dezhra")
    assert ctx.near_own_carrier() is True
    ctx.update(system="Sol")
    assert ctx.near_own_carrier() is False
    assert ctx.at_own_carrier() is False          # not docked -> never "at"


def test_near_own_carrier_false_when_unknown():
    assert EDContext().near_own_carrier() is False


def test_journal_docked_captures_station_type_and_market_id():
    ctx = EDContext()
    apply_carrier_event(ctx, {"event": "CarrierStats", "CarrierID": 42, "Name": "Nomad",
                              "Callsign": "K7X-B0X"})
    apply_journal_event(ctx, {"event": "Docked", "StationName": "K7X-B0X",
                              "StationType": "FleetCarrier", "MarketID": 42, "StarSystem": "Sol"})
    snap = ctx.snapshot()
    assert snap["docked_station_type"] == "FleetCarrier" and snap["docked_market_id"] == 42
    assert ctx.at_own_carrier() is True


def test_journal_undocked_clears_dock_identity():
    ctx = _ctx_docked_at(market_id=42, carrier_id=42)
    apply_journal_event(ctx, {"event": "Undocked", "StationName": "K7X-B0X"})
    snap = ctx.snapshot()
    assert snap["docked_station_type"] is None and snap["docked_market_id"] is None
    assert ctx.at_own_carrier() is False


def test_journal_location_while_docked_at_own_carrier_on_login():
    # Logging in already parked at your carrier: the Location event must carry the identity too.
    ctx = EDContext()
    apply_carrier_event(ctx, {"event": "CarrierStats", "CarrierID": 7, "Name": "Nomad",
                              "Callsign": "AAA-000"})
    apply_journal_event(ctx, {"event": "Location", "Docked": True, "StationName": "AAA-000",
                              "StationType": "FleetCarrier", "MarketID": 7, "StarSystem": "Sol"})
    assert ctx.at_own_carrier() is True


# ============================================================================================
# 2. Eligibility tokens + engine folding
# ============================================================================================

def test_carrier_tokens_are_in_the_vocabulary():
    assert {AT_OWN_CARRIER, NEAR_OWN_CARRIER} <= STATES


def test_note_carrier_at_own_implies_near():
    eng = EligibilityEngine()
    eng.note_carrier(at_own=True, near_own=False)
    s = eng.states()
    assert AT_OWN_CARRIER in s and NEAR_OWN_CARRIER in s


def test_note_carrier_near_only():
    eng = EligibilityEngine()
    eng.note_carrier(at_own=False, near_own=True)
    s = eng.states()
    assert NEAR_OWN_CARRIER in s and AT_OWN_CARRIER not in s


def test_note_carrier_clears_when_gone():
    eng = EligibilityEngine()
    eng.note_carrier(at_own=True, near_own=True)
    eng.note_carrier(at_own=False, near_own=False)
    s = eng.states()
    assert AT_OWN_CARRIER not in s and NEAR_OWN_CARRIER not in s


# ============================================================================================
# 3. Cue definitions
# ============================================================================================

def test_carrier_cues_register_cleanly():
    reg = CueRegistry(carrier_cues())
    assert reg.contract_violations() == []
    assert len(reg.cues()) == len(carrier_cues())


def test_carrier_cue_states_are_all_known():
    for cue in carrier_cues():
        assert unknown_states(cue.eligible_states) == set()
        assert cue.eligible_states <= STATES


def test_every_carrier_cue_has_a_role():
    roles = {c.voice_role for c in carrier_cues()}
    assert roles == {CAPTAIN, TOWER, CHATTER}
    assert all(c.voice_role for c in carrier_cues())


def test_tower_only_fires_while_docked_at_carrier():
    reg = CueRegistry(carrier_cues())
    at = {c.name for c in reg.eligible({AT_OWN_CARRIER, NEAR_OWN_CARRIER})}
    near_only = {c.name for c in reg.eligible({NEAR_OWN_CARRIER})}
    # Tower (docking control) needs to be AT the carrier; the captain has an in-system-only line.
    assert any(c.voice_role == TOWER and c.name in at for c in carrier_cues())
    assert not any(c.voice_role == TOWER and c.name in near_only for c in carrier_cues())
    assert "carrier_captain_nearby" in near_only   # captain still greets from across the system


# ============================================================================================
# 4. Config parsing
# ============================================================================================

def test_build_carrier_config_defaults():
    cc = build_carrier_config({})
    assert cc.enabled is True
    assert cc.name_map() == {"captain": "Captain", "tower": "Tower Control", "chatter": ""}
    assert all(cc.roles[r].voice is None for r in ("captain", "tower", "chatter"))


def test_build_carrier_config_configured_voice_and_name():
    cfg = {"audio": {"carrier": {
        "enabled": True,
        "captain": {"name": "Reynolds", "voice_ref": "CAPVOICE", "gender": "male"},
    }}}
    cc = build_carrier_config(cfg)
    cap = cc.roles["captain"]
    assert cap.name == "Reynolds"
    assert cap.voice == Voice("elevenlabs", "CAPVOICE", "male")   # cast_provider default


def test_build_carrier_config_provider_resolution_order():
    # carrier-local voice_provider wins over the [audio.voices.providers] role override.
    cfg = {"audio": {
        "voices": {"cast_provider": "elevenlabs", "providers": {"tower": "piper"}},
        "carrier": {
            "tower": {"voice_ref": "T", "voice_provider": "openai"},
            "chatter": {"voice_ref": "C"},   # no local provider -> role override -> piper? none -> cast
        },
    }}
    cc = build_carrier_config(cfg)
    assert cc.roles["tower"].voice.provider == "openai"    # local override wins
    assert cc.roles["chatter"].voice.provider == "elevenlabs"  # falls through to cast_provider


def test_disabled_config():
    cc = build_carrier_config({"audio": {"carrier": {"enabled": False}}})
    assert cc.enabled is False


# ============================================================================================
# 5. Name templating
# ============================================================================================

def test_apply_names_weaves_display_names():
    assert apply_names("{captain} here.", {"captain": "Reynolds"}) == "Reynolds here."


def test_apply_names_leaves_unknown_placeholders():
    assert apply_names("{captain} and {mystery}", {"captain": "R"}) == "R and {mystery}"


def test_apply_names_tolerates_stray_braces():
    # A malformed brace must never raise — the line is returned as-is.
    assert apply_names("100% { open", {"captain": "R"}) == "100% { open"


# ============================================================================================
# 6. CarrierPlayer routing
# ============================================================================================

class _Speak:
    def __init__(self, ok=True):
        self.ok = ok
        self.said: list[tuple[Voice, str, str]] = []

    def __call__(self, voice, text, bus):
        self.said.append((voice, text, bus))
        return self.ok


def _voice_for(mapping):
    return lambda role: mapping[role]


def test_carrier_player_routes_to_the_role_voice_with_name():
    speak = _Speak()
    voices = {CAPTAIN: Voice("elevenlabs", "CAPVOICE")}
    player = CarrierPlayer(speak, _voice_for(voices), names={CAPTAIN: "Reynolds"})
    from covas.mixer import Cue
    from covas.mixer.buses import COMMS
    cue = Cue("c", COMMS, {AT_OWN_CARRIER}, voice_role=CAPTAIN, phrasings=("{captain} here.",))
    assert player(cue) is True
    voice, text, bus = speak.said[0]
    assert voice.ref == "CAPVOICE" and text == "Reynolds here." and bus == COMMS


def test_carrier_player_rotates_deterministically():
    speak = _Speak()
    player = CarrierPlayer(speak, _voice_for({CHATTER: Voice("piper", "x")}))
    from covas.mixer import Cue
    from covas.mixer.buses import COMMS
    cue = Cue("c", COMMS, {AT_OWN_CARRIER}, voice_role=CHATTER, phrasings=("a", "b", "c"))
    for _ in range(4):
        player(cue)
    assert [t for _v, t, _b in speak.said] == ["a", "b", "c", "a"]


def test_carrier_player_failed_speak_does_not_advance():
    speak = _Speak(ok=False)
    player = CarrierPlayer(speak, _voice_for({CHATTER: Voice("piper", "x")}))
    from covas.mixer import Cue
    from covas.mixer.buses import COMMS
    cue = Cue("c", COMMS, {AT_OWN_CARRIER}, voice_role=CHATTER, phrasings=("a", "b"))
    assert player(cue) is False and player(cue) is False
    assert [t for _v, t, _b in speak.said] == ["a", "a"]


def test_carrier_player_ignores_cue_without_a_role():
    speak = _Speak()
    player = CarrierPlayer(speak, _voice_for({}))
    from covas.mixer import Cue
    from covas.mixer.buses import COMMS
    cue = Cue("plain", COMMS, {AT_OWN_CARRIER}, phrasings=("a",))   # no voice_role
    assert player(cue) is False and speak.said == []


# ============================================================================================
# 7. AudioLayer end-to-end
# ============================================================================================

class _FakeTTS:
    def __init__(self):
        self.said: list[tuple[str, str | None]] = []

    def synth_pcm(self, text, voice_id=None):
        self.said.append((text, voice_id))
        return b"", 16000


class _FakeCtx:
    def __init__(self, *, at=False, near=False):
        self._at, self._near = at, near

    def at_own_carrier(self):
        return self._at

    def near_own_carrier(self):
        return self._near

    def fuel_pct(self):
        return None


def _carrier_layer(ctx, **carrier):
    cfg = {"elevenlabs": {"voice_id": "PERSONA"},
           "audio": {"mix_sample_rate": 16000,
                     "cues": {"enabled": False},          # chatter/SFX OFF -> isolate carrier
                     "comms": {"enabled": True},
                     "carrier": {"enabled": True, **carrier}}}
    tts = _FakeTTS()
    layer = AudioLayer(cfg, BusMixer(cfg), tts, ed_ctx=ctx, llm=None, clock=lambda: 0.0)
    return layer, tts


def test_captain_speaks_in_configured_voice_when_near_own_carrier():
    # Near (in-system, not docked) -> only the captain's in-system line is eligible, so the pick is
    # deterministic and must come out on the configured captain voice with the configured name.
    ctx = _FakeCtx(near=True, at=False)
    layer, tts = _carrier_layer(
        ctx, captain={"name": "Reynolds", "voice_ref": "CAPVOICE"})
    layer.on_event({"type": "ed_event", "event": "Location", "StarSystem": "Sol"})
    assert len(tts.said) == 1
    text, voice_id = tts.said[0]
    assert voice_id == "CAPVOICE" and "Reynolds" in text


def test_no_carrier_lines_when_away():
    layer, tts = _carrier_layer(_FakeCtx(near=False, at=False))
    layer.on_event({"type": "ed_event", "event": "Location", "StarSystem": "Deciat"})
    assert tts.said == []


def test_carrier_toggle_suppresses_lines():
    ctx = _FakeCtx(near=True, at=True)
    layer, tts = _carrier_layer(ctx, captain={"voice_ref": "CAPVOICE"})
    layer.set_carrier(False)
    layer.on_event({"type": "ed_event", "event": "Docked", "StationName": "K7X-B0X"})
    assert tts.said == []
    layer.set_carrier(True)
    layer.on_event({"type": "ed_event", "event": "Docked", "StationName": "K7X-B0X"})
    assert len(tts.said) >= 1               # something aboard speaks once re-enabled


def test_master_mute_suppresses_carrier():
    ctx = _FakeCtx(near=True, at=True)
    layer, tts = _carrier_layer(ctx)
    layer.set_muted(True)
    layer.on_event({"type": "ed_event", "event": "Docked", "StationName": "K7X-B0X"})
    assert tts.said == []


def test_carrier_voice_falls_back_to_a_distinct_pool_voice():
    # With a configured pool but no captain voice_ref, the role still gets a stable pool voice
    # (not the persona), so it sounds like a separate person out of the box.
    cfg = {"elevenlabs": {"voice_id": "PERSONA"},
           "audio": {"mix_sample_rate": 16000, "cues": {"enabled": False},
                     "voices": {"cast_provider": "elevenlabs",
                                "pool": [{"provider": "elevenlabs", "ref": r} for r in
                                         ("VA", "VB", "VC", "VD")]},
                     "carrier": {"enabled": True}}}
    layer = AudioLayer(cfg, BusMixer(cfg), _FakeTTS(), ed_ctx=_FakeCtx(at=True, near=True),
                       llm=None, clock=lambda: 0.0)
    cap = layer._carrier_voice("captain")      # noqa: SLF001
    tower = layer._carrier_voice("tower")      # noqa: SLF001
    assert cap.ref in {"VA", "VB", "VC", "VD"} and cap.ref != "PERSONA"
    assert isinstance(tower.ref, str)


# ============================================================================================
# 8. Event-anchored captain responses (issue #137) — arrival / departure + dedup
# ============================================================================================

def test_carrier_event_cues_are_captain_role_and_known_states():
    cues = carrier_event_cues()
    assert set(cues) == {"arrival", "departure"}
    for cue in cues.values():
        assert cue.voice_role == CAPTAIN
        assert cue.eligible_states <= STATES
        assert cue.phrasings                       # a non-empty deferential pool


class _PlayRec:
    def __init__(self, ok=True):
        self.ok = ok
        self.cues = []

    def __call__(self, cue):
        self.cues.append(cue)
        return self.ok


def _responder(*, at=False, near=False, ok=True, owned=None, dedup=None):
    rec = _PlayRec(ok)
    r = CarrierEventResponder(rec, at_near=lambda: (at, near),
                              owned_id=(lambda: owned), dedup=dedup)
    return r, rec


def test_arrival_fires_captain_welcome_near_own_carrier():
    r, rec = _responder(near=True, at=False)
    assert r.on_event({"event": "SupercruiseExit", "StarSystem": "Sol"}) is True
    assert [c.name for c in rec.cues] == ["carrier_captain_arrival"]


def test_arrival_silent_when_away():
    r, rec = _responder(near=False, at=False)
    assert r.on_event({"event": "SupercruiseExit", "StarSystem": "Deciat"}) is False
    assert rec.cues == []


def test_departure_fires_send_off_on_own_carrier_undock():
    r, rec = _responder(near=True, at=False)
    assert r.on_event({"event": "Undocked", "StationName": "K7X-B0X",
                       "StationType": "FleetCarrier", "MarketID": 42}) is True
    assert [c.name for c in rec.cues] == ["carrier_captain_departure"]


def test_departure_ignores_a_normal_station_undock():
    r, rec = _responder(near=True, at=False)
    # Undocking from a Coriolis in the carrier's system is NOT leaving the carrier.
    assert r.on_event({"event": "Undocked", "StationName": "Jameson Memorial",
                       "StationType": "Coriolis", "MarketID": 99}) is False
    assert rec.cues == []


def test_departure_ignores_a_different_carrier_when_id_known():
    r, rec = _responder(near=True, owned=42)
    # A fleet-carrier undock whose MarketID isn't our owned CarrierID = a squadron/other carrier.
    assert r.on_event({"event": "Undocked", "StationType": "FleetCarrier", "MarketID": 999}) is False
    assert rec.cues == []


def test_departure_fires_when_id_matches_owned_carrier():
    r, rec = _responder(near=True, owned=42)
    assert r.on_event({"event": "Undocked", "StationType": "FleetCarrier", "MarketID": 42}) is True
    assert [c.name for c in rec.cues] == ["carrier_captain_departure"]


def test_unrelated_event_never_fires():
    r, rec = _responder(near=True, at=True)
    assert r.on_event({"event": "FSDJump", "StarSystem": "Sol"}) is False
    assert rec.cues == []


def test_dedup_blocks_a_second_captain_line_in_the_window():
    dedup = CaptainDedup(clock=lambda: 0.0, window_s=60.0)
    r, rec = _responder(near=True, dedup=dedup)
    assert r.on_event({"event": "SupercruiseExit"}) is True          # fires + marks the window
    # A second transition at the same instant is inside the window -> suppressed.
    assert r.on_event({"event": "Undocked", "StationType": "FleetCarrier"}) is False
    assert len(rec.cues) == 1


def test_captain_dedup_window_reopens_after_it_elapses():
    now = {"t": 0.0}
    dedup = CaptainDedup(clock=lambda: now["t"], window_s=60.0)
    assert dedup.allow() is True
    dedup.mark()
    assert dedup.allow() is False
    now["t"] = 61.0
    assert dedup.allow() is True


# ============================================================================================
# 9. Event-anchored responses through the AudioLayer (dedup with the ambient cue)
# ============================================================================================

def test_layer_arrival_speaks_captain_once_and_dedups_the_ambient_cue():
    # Drop out of supercruise NEAR the owned carrier: the guaranteed arrival line speaks in the
    # configured captain voice/name, and the ambient captain_nearby cue is deduped in the SAME tick
    # (same clock), so exactly ONE captain line comes out.
    ctx = _FakeCtx(near=True, at=False)
    layer, tts = _carrier_layer(ctx, captain={"name": "Reynolds", "voice_ref": "CAPVOICE"})
    layer.on_event({"type": "ed_event", "event": "SupercruiseExit", "StarSystem": "Sol"})
    assert len(tts.said) == 1
    text, voice_id = tts.said[0]
    assert voice_id == "CAPVOICE" and "Reynolds" in text


def test_layer_departure_speaks_send_off_once():
    ctx = _FakeCtx(near=True, at=False)   # just undocked: in-system, no longer docked
    layer, tts = _carrier_layer(ctx, captain={"name": "Reynolds", "voice_ref": "CAPVOICE"})
    layer.on_event({"type": "ed_event", "event": "Undocked", "StationName": "K7X-B0X",
                    "StationType": "FleetCarrier", "MarketID": 42})
    assert len(tts.said) == 1
    text, voice_id = tts.said[0]
    assert voice_id == "CAPVOICE" and "Reynolds" in text


def test_layer_no_event_line_when_carrier_voices_off():
    ctx = _FakeCtx(near=True, at=False)
    layer, tts = _carrier_layer(ctx, captain={"voice_ref": "CAPVOICE"})
    layer.set_carrier(False)
    layer.on_event({"type": "ed_event", "event": "SupercruiseExit", "StarSystem": "Sol"})
    assert tts.said == []


def test_layer_no_event_line_when_muted():
    ctx = _FakeCtx(near=True, at=False)
    layer, tts = _carrier_layer(ctx, captain={"voice_ref": "CAPVOICE"})
    layer.set_muted(True)
    layer.on_event({"type": "ed_event", "event": "Undocked", "StationType": "FleetCarrier"})
    assert tts.said == []
