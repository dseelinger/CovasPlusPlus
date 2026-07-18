"""Unit tests for FindClosestShipCapability + the multi-turn flow (offline, DESIGN §9).

Two layers, mirroring the outfitting capability's tests:
  * direct capability tests — each resolve outcome (unknown / ambiguous family / resolved-
    searches) with fake http + fake clipboard, plus the N3 skip-copy-when-already-there rule;
  * the multi-turn dialogue driven through the real App loop with a scripted tool-calling LLM,
    proving an ambiguous family ASKS (no search) and the follow-up model choice searches once.

All hermetic — the http poster, clipboard, and current-system are injected fakes; the default
`pytest` never hits the network or the real clipboard.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from covas.app import App
from covas.capabilities.find_closest_capability import FindClosestShipCapability, NavConfig
from tests.fakes import FakeSTT, FakeTTS

_FIXTURE = Path(__file__).parent / "fixtures" / "spansh_stations_ship_anaconda.json"


def _fixture() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


class FakeHttp:
    """Returns the recorded Spansh body and counts calls (to assert 'searched exactly once')."""

    def __init__(self, status=200, body=None) -> None:
        self._status = status
        self._body = body if body is not None else _fixture()
        self.calls: list[dict] = []

    def post_json(self, url, payload, *, headers=None, timeout=20.0):
        self.calls.append({"url": url, "payload": payload})
        return self._status, self._body


class FakeClipboard:
    def __init__(self) -> None:
        self.copied: list[str] = []

    def __call__(self, text: str) -> None:
        self.copied.append(text)


def _cap(*, http=None, clip=None, system="Sol", cfg=None):
    http = http or FakeHttp()
    clip = clip or FakeClipboard()
    cap = FindClosestShipCapability(
        cfg or NavConfig(enabled=True),
        http=http,
        get_current_system=(lambda: system),
        clipboard=clip,
    )
    return cap, http, clip


# --- direct capability outcomes ----------------------------------------------------------

def test_tool_advertised_with_ship_required():
    cap, _, _ = _cap()
    tool = cap.tools()[0]
    assert tool["name"] == "find_closest_ship"
    assert tool["input_schema"]["required"] == ["ship"]
    assert "ship" in tool["input_schema"]["properties"]


def test_unknown_ship_no_search():
    cap, http, clip = _cap()
    out = cap.run_tool("find_closest_ship", {"ship": "flux capacitor"})
    assert "don't recognize" in out.lower()
    assert http.calls == [] and clip.copied == []


def test_ambiguous_family_asks_and_no_search():
    cap, http, clip = _cap()
    out = cap.run_tool("find_closest_ship", {"ship": "krait"})
    assert "which" in out.lower()
    assert "MkII" in out or "Phantom" in out
    assert http.calls == [] and clip.copied == []


def test_resolved_ship_searches_immediately_and_copies_system():
    cap, http, clip = _cap(system="Sol")
    out = cap.run_tool("find_closest_ship", {"ship": "Anaconda"})
    assert len(http.calls) == 1
    # nearest non-carrier in the fixture is Wolf 359 / Cayley Enterprise
    assert clip.copied == ["Wolf 359"]
    assert "Cayley Enterprise" in out and "Wolf 359" in out
    assert "clipboard" in out.lower()


def test_result_mentions_price():
    cap, _, _ = _cap()
    out = cap.run_tool("find_closest_ship", {"ship": "conda"})
    assert "credits" in out.lower()                          # ship price read from the result


def test_pad_default_from_config_used():
    cap, http, _ = _cap(cfg=NavConfig(enabled=True, default_pad_size="L"))
    cap.run_tool("find_closest_ship", {"ship": "Anaconda"})
    assert http.calls[0]["payload"]["filters"]["has_large_pad"] == {"value": True}


def test_pad_override_any_disables_filter():
    cap, http, _ = _cap(cfg=NavConfig(enabled=True, default_pad_size="L"))
    cap.run_tool("find_closest_ship", {"ship": "Anaconda", "pad_size": "any"})
    assert not any(k.startswith("has_") for k in http.calls[0]["payload"]["filters"])


def test_pad_match_resolves_via_current_ship_size():
    """"Match Current Ship Size" (#117): the injected get_current_ship_size getter resolves
    the config default into the actual pad filter sent to Spansh."""
    http = FakeHttp()
    cap = FindClosestShipCapability(
        NavConfig(enabled=True, default_pad_size="match"),
        http=http, get_current_system=(lambda: "Sol"),
        get_current_ship_size=(lambda: "S"), clipboard=FakeClipboard())
    cap.run_tool("find_closest_ship", {"ship": "Anaconda"})
    assert http.calls[0]["payload"]["filters"]["has_small_pad"] == {"value": True}


def test_pad_match_falls_back_to_large_when_ship_unknown():
    """No ship-size getter wired (or it returns None) -> the conservative Large fallback,
    never 'any' — a search never returns a station the ship couldn't use."""
    http = FakeHttp()
    cap = FindClosestShipCapability(
        NavConfig(enabled=True, default_pad_size="match"),
        http=http, get_current_system=(lambda: "Sol"), clipboard=FakeClipboard())
    cap.run_tool("find_closest_ship", {"ship": "Anaconda"})
    assert http.calls[0]["payload"]["filters"]["has_large_pad"] == {"value": True}


def test_pad_match_as_one_off_tool_arg_override():
    """A per-search 'match my ship' override works without changing the config default."""
    http = FakeHttp()
    cap = FindClosestShipCapability(
        NavConfig(enabled=True, default_pad_size="L"),
        http=http, get_current_system=(lambda: "Sol"),
        get_current_ship_size=(lambda: "M"), clipboard=FakeClipboard())
    cap.run_tool("find_closest_ship", {"ship": "Anaconda", "pad_size": "match"})
    assert http.calls[0]["payload"]["filters"]["has_medium_pad"] == {"value": True}


def test_clipboard_skipped_when_already_in_that_system():
    """N3 rule: if the nearest station is in the Commander's current system (distance ~0),
    nothing is copied — you're already there."""
    body = {"count": 1, "results": [
        {"system_name": "Sol", "name": "Daylight Depot", "type": "Outpost", "distance": 0.0,
         "has_large_pad": True, "large_pads": 2,
         "ships": [{"name": "Anaconda", "price": 100, "symbol": "Anaconda"}]},
    ]}
    cap, http, clip = _cap(http=FakeHttp(body=body), system="Sol")
    out = cap.run_tool("find_closest_ship", {"ship": "Anaconda"})
    assert clip.copied == []                                 # already there -> nothing copied
    assert "already there" in out.lower()


def test_search_failure_is_spoken_not_raised():
    cap, http, clip = _cap(http=FakeHttp(status=503, body={}))
    out = cap.run_tool("find_closest_ship", {"ship": "Anaconda"})
    assert "fail" in out.lower() or "try again" in out.lower()
    assert clip.copied == []


def test_clipboard_failure_still_speaks_result():
    class BadClip:
        def __call__(self, text):
            raise RuntimeError("no clipboard")
    cap, http, _ = _cap(clip=BadClip(), system="Sol")
    out = cap.run_tool("find_closest_ship", {"ship": "Anaconda"})
    assert "Cayley Enterprise" in out and "Wolf 359" in out  # answer still delivered


def test_no_current_system_is_spoken():
    cap, http, clip = _cap(system=None)
    out = cap.run_tool("find_closest_ship", {"ship": "Anaconda"})
    assert "current system" in out.lower()
    assert http.calls == []


def test_empty_ship_arg_asks():
    cap, http, _ = _cap()
    out = cap.run_tool("find_closest_ship", {"ship": "  "})
    assert "which ship" in out.lower()
    assert http.calls == []


# --- live roster: a newly-released hull becomes findable ---------------------------------

class FakeShipIndex:
    def __init__(self, extras):
        self._extras = tuple(extras)

    def extra_names(self):
        return self._extras


def test_new_hull_from_index_resolves_and_searches():
    """A hull absent from the bundle but surfaced by the live index resolves and searches — no
    code change needed when Frontier adds a ship."""
    body = {"count": 1, "results": [
        {"system_name": "Wolf 359", "name": "Cayley Enterprise", "type": "Outpost",
         "distance": 7.8, "has_large_pad": True, "large_pads": 2,
         "ships": [{"name": "Frontier Destroyer", "price": 500, "symbol": "FD"}]},
    ]}
    http, clip = FakeHttp(body=body), FakeClipboard()
    cap = FindClosestShipCapability(
        NavConfig(enabled=True), http=http, get_current_system=(lambda: "Sol"),
        clipboard=clip, ship_index=FakeShipIndex(["Frontier Destroyer"]))
    out = cap.run_tool("find_closest_ship", {"ship": "Frontier Destroyer"})
    assert len(http.calls) == 1
    assert http.calls[0]["payload"]["filters"]["ships"] == [{"name": "Frontier Destroyer"}]
    assert clip.copied == ["Wolf 359"] and "Wolf 359" in out


def test_unknown_without_index_extra_is_still_unknown():
    """The same hull, with no live index, stays Unknown (proving the index is what enabled it)
    and never hits the network."""
    http, clip = FakeHttp(), FakeClipboard()
    cap = FindClosestShipCapability(NavConfig(enabled=True), http=http,
                                    get_current_system=(lambda: "Sol"), clipboard=clip)
    out = cap.run_tool("find_closest_ship", {"ship": "Frontier Destroyer"})
    assert "don't recognize" in out.lower()
    assert http.calls == []


def test_broken_ship_index_is_fail_soft():
    """A throwing index never breaks a lookup — resolution falls back to the bundled roster."""
    class BadIndex:
        def extra_names(self):
            raise RuntimeError("index boom")
    http, clip = FakeHttp(), FakeClipboard()
    cap = FindClosestShipCapability(NavConfig(enabled=True), http=http,
                                    get_current_system=(lambda: "Sol"), clipboard=clip,
                                    ship_index=BadIndex())
    out = cap.run_tool("find_closest_ship", {"ship": "Anaconda"})   # bundled hull still works
    assert "Cayley Enterprise" in out and clip.copied == ["Wolf 359"]


# --- multi-turn flow through the real App loop -------------------------------------------

class ScriptLLM:
    """A scripted stand-in for the LLM: each turn makes zero or more tool calls (via the app's
    registry handler) then returns a reply. Mirrors the real streaming tool loop closely enough
    to exercise App._process end to end."""

    def __init__(self, turns) -> None:
        self._turns = list(turns)     # [(list[(tool_name, input)], reply_text), ...]
        self.i = 0
        self.results: list[tuple[str, dict, str]] = []

    def stream_reply(self, messages, cancel, on_event, tool_handler=None, tools=None,
                     model=None, max_tokens=None):
        calls, text = self._turns[self.i]
        self.i += 1
        for name, inp in calls:
            on_event("tool", name)
            self.results.append((name, inp, tool_handler(name, inp)))
        yield ("text", text)


def _cfg(tmp_path) -> dict:
    checklist = tmp_path / "checklist.md"
    checklist.write_text("- [ ] Fly safe\n", encoding="utf-8")
    return {
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
        # [nav] deliberately omitted so App doesn't build a real RequestsHttp capability; the
        # test registers a fake-wired one below.
    }


def _app_with_ship_nav(tmp_path, llm, cap) -> App:
    app = App(_cfg(tmp_path), stt=FakeSTT(text="_"), llm=llm, tts=FakeTTS())
    app.registry.register(cap)
    app.ship_nav = cap
    return app


def _drive(app, n):
    for _ in range(n):
        app._process(object(), threading.Event())


def test_flow_ambiguous_family_then_choice_searches_once(tmp_path):
    """Ask for the closest Asp -> the tool asks Explorer vs Scout (no search); the follow-up
    model choice resolves and searches exactly once, copying the system. (Asp Explorer is what
    the fixture's nearest real station stocks.)"""
    http, clip = FakeHttp(), FakeClipboard()
    cap = FindClosestShipCapability(NavConfig(enabled=True), http=http,
                                    get_current_system=(lambda: "Sol"), clipboard=clip)
    llm = ScriptLLM([
        ([("find_closest_ship", {"ship": "asp"})], "Explorer or Scout, Commander?"),
        ([("find_closest_ship", {"ship": "Asp Explorer"})],
         "Found it — copied to your clipboard."),
    ])
    app = _app_with_ship_nav(tmp_path, llm, cap)

    _drive(app, 1)
    assert http.calls == []                                  # the family ask does NOT search
    assert "which" in llm.results[0][2].lower()

    _drive(app, 1)
    assert len(http.calls) == 1                              # the choice searched once
    assert clip.copied == ["Wolf 359"]


def test_flow_cancel_runs_no_search_and_no_copy(tmp_path):
    http, clip = FakeHttp(), FakeClipboard()
    cap = FindClosestShipCapability(NavConfig(enabled=True), http=http,
                                    get_current_system=(lambda: "Sol"), clipboard=clip)
    llm = ScriptLLM([
        ([("find_closest_ship", {"ship": "asp"})], "Explorer or Scout?"),
        ([], "No worries, cancelled."),                      # verbal cancel: no tool call
    ])
    app = _app_with_ship_nav(tmp_path, llm, cap)
    _drive(app, 2)
    assert http.calls == [] and clip.copied == []
