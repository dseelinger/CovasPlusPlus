"""Unit tests for FindClosestCapability + the multi-turn flow (offline, DESIGN §9).

Two layers:
  * direct capability tests — each resolve outcome (unknown/ambiguous/need-attrs/confirm/
    search) with fake http + fake clipboard;
  * the multi-turn dialogue driven through the real App loop with a scripted tool-calling
    LLM, proving the invariants: the Spansh search fires EXACTLY ONCE and only after
    confirmation, a verbal cancel runs NO search and writes NOTHING to the clipboard, and a
    second ambiguous answer loops before resolving.

All hermetic — the http poster, clipboard, and current-system are injected fakes; the
default `pytest` never hits the network or the real clipboard.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from covas.app import App
from covas.capabilities.find_closest_capability import FindClosestCapability, NavConfig
from tests.fakes import FakeSTT, FakeTTS

_FIXTURE = Path(__file__).parent / "fixtures" / "spansh_stations_multicannon.json"


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
    cap = FindClosestCapability(
        cfg or NavConfig(enabled=True),
        http=http,
        get_current_system=(lambda: system),
        clipboard=clip,
    )
    return cap, http, clip


# --- direct capability outcomes ------------------------------------------------------------

def test_tool_advertised():
    cap, _, _ = _cap()
    assert {t["name"] for t in cap.tools()} == {"find_closest_module"}


def test_unknown_module_no_search():
    cap, http, clip = _cap()
    out = cap.run_tool("find_closest_module", {"module": "flux capacitor"})
    assert "don't recognize" in out.lower()
    assert http.calls == [] and clip.copied == []


def test_ambiguous_module_no_search():
    cap, http, clip = _cap()
    out = cap.run_tool("find_closest_module", {"module": "laser"})
    assert "which" in out.lower()
    assert http.calls == []


def test_need_attrs_asks_and_no_search():
    cap, http, clip = _cap()
    out = cap.run_tool("find_closest_module", {"module": "multicannon"})
    assert "size" in out.lower() and "mount" in out.lower()
    assert "won't guess" in out.lower()
    assert http.calls == []


def test_resolved_searches_immediately_by_default():
    """Default (require_confirmation off): a fully-resolved module searches at once — no
    separate confirm turn."""
    cap, http, clip = _cap(system="Sol")
    out = cap.run_tool("find_closest_module",
                       {"module": "multicannon", "size": "medium", "mount": "gimballed"})
    assert len(http.calls) == 1
    assert clip.copied == ["Barnard's Star"]
    assert "clipboard" in out.lower()


def test_confirmed_runs_search_and_copies_system():
    cap, http, clip = _cap(system="Sol")
    out = cap.run_tool("find_closest_module",
                       {"module": "multicannon", "size": "medium", "mount": "gimballed",
                        "confirmed": True})
    # Gimballed medium multi-cannon: nearest in the fixture is Barnard's Star (Sol/Walz Depot
    # only stocks Fixed), so the mount post-filter picks it AND that system is copied.
    assert len(http.calls) == 1
    assert clip.copied == ["Barnard's Star"]
    assert "Barnard's Star" in out and "clipboard" in out.lower()


def test_confirmed_but_incomplete_ignores_confirm_and_asks():
    """confirmed=true with a still-ambiguous/incomplete module must NOT search — the tool
    validates first."""
    cap, http, clip = _cap()
    out = cap.run_tool("find_closest_module", {"module": "multicannon", "confirmed": True})
    assert "size" in out.lower() and "mount" in out.lower()
    assert http.calls == []


def test_search_failure_is_spoken_not_raised():
    cap, http, clip = _cap(http=FakeHttp(status=503, body={}))
    out = cap.run_tool("find_closest_module",
                       {"module": "multicannon", "size": "medium", "mount": "fixed",
                        "confirmed": True})
    assert "fail" in out.lower() or "try again" in out.lower()
    assert clip.copied == []                            # nothing to copy on failure


def test_clipboard_failure_still_speaks_result():
    class BadClip:
        def __call__(self, text):
            raise RuntimeError("no clipboard")
    cap, http, _ = _cap(clip=BadClip())
    out = cap.run_tool("find_closest_module",
                       {"module": "multicannon", "size": "medium", "mount": "fixed",
                        "confirmed": True})
    assert "Walz Depot" in out and "Sol" in out         # answer still delivered


def test_no_current_system_is_spoken():
    cap, http, clip = _cap(system=None)
    out = cap.run_tool("find_closest_module",
                       {"module": "multicannon", "size": "medium", "mount": "fixed",
                        "confirmed": True})
    assert "current system" in out.lower()
    assert http.calls == []


def test_pad_default_from_config_used():
    cap, http, clip = _cap(cfg=NavConfig(enabled=True, default_pad_size="L"))
    cap.run_tool("find_closest_module",
                 {"module": "multicannon", "size": "medium", "mount": "fixed"})
    assert len(http.calls) == 1                          # fixture stations are all Large-pad


# --- confirmation mode (require_confirmation on): the turn-gate ------------------------------

def _confirm_cap():
    return _cap(cfg=NavConfig(enabled=True, require_confirmation=True))


def test_confirm_mode_tool_exposes_confirmed_arg():
    cap, _, _ = _confirm_cap()
    props = cap.tools()[0]["input_schema"]["properties"]
    assert "confirmed" in props
    assert "confirm" in cap.tools()[0]["description"].lower()


def test_confirm_mode_default_tool_hides_confirmed_arg():
    cap, _, _ = _cap()      # default: confirmation off
    assert "confirmed" not in cap.tools()[0]["input_schema"]["properties"]


def test_confirm_mode_resolve_arms_without_searching():
    cap, http, clip = _confirm_cap()
    cap.new_turn()
    out = cap.run_tool("find_closest_module",
                       {"module": "multicannon", "size": "medium", "mount": "gimballed"})
    assert "confirm" in out.lower()
    assert http.calls == [] and clip.copied == []


def test_confirm_mode_same_turn_self_confirm_refused():
    """The model calling confirmed=true in the SAME turn it resolved must NOT search — the
    exact failure observed live with Haiku."""
    cap, http, clip = _confirm_cap()
    cap.new_turn()                                        # turn 1
    cap.run_tool("find_closest_module",
                 {"module": "multicannon", "size": "medium", "mount": "gimballed"})
    out = cap.run_tool("find_closest_module",             # same turn confirm
                       {"module": "multicannon", "size": "medium", "mount": "gimballed",
                        "confirmed": True})
    assert http.calls == []                               # gate held
    assert "separate command" in out.lower() or "confirm" in out.lower()


def test_confirm_mode_confirm_on_new_turn_searches():
    cap, http, clip = _confirm_cap()
    cap.new_turn()                                        # turn 1: resolve/arm
    cap.run_tool("find_closest_module",
                 {"module": "multicannon", "size": "medium", "mount": "gimballed"})
    cap.new_turn()                                        # turn 2: the confirmation command
    out = cap.run_tool("find_closest_module",
                       {"module": "multicannon", "size": "medium", "mount": "gimballed",
                        "confirmed": True})
    assert len(http.calls) == 1
    assert clip.copied == ["Barnard's Star"]
    assert "Barnard's Star" in out


# --- multi-turn flow through the real App loop ---------------------------------------------

class ScriptLLM:
    """A scripted stand-in for the LLM: each turn makes zero or more tool calls (via the
    app's registry handler) then returns a reply. Mirrors the real streaming tool loop
    closely enough to exercise App._process end to end."""

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
        # [nav] deliberately omitted so App doesn't build the real RequestsHttp capability;
        # the test registers a fake-wired one below.
    }


def _app_with_nav(tmp_path, llm, cap) -> App:
    app = App(_cfg(tmp_path), stt=FakeSTT(text="_"), llm=llm, tts=FakeTTS())
    app.registry.register(cap)
    app.nav = cap
    return app


def _drive(app, n):
    for _ in range(n):
        app._process(object(), threading.Event())


def test_flow_ask_then_narrow_searches_once_default(tmp_path):
    """Default (confirmation off): ask -> narrow -> the search fires exactly once the moment
    the module resolves, and the system is copied."""
    http, clip = FakeHttp(), FakeClipboard()
    cap = FindClosestCapability(NavConfig(enabled=True), http=http,
                                get_current_system=(lambda: "Sol"), clipboard=clip)
    llm = ScriptLLM([
        # turn 1: ask for the closest multicannon -> tool says NEED_ATTRS (no search)
        ([("find_closest_module", {"module": "multicannon"})], "What size and mount?"),
        # turn 2: narrow -> RESOLVED -> searches immediately
        ([("find_closest_module",
           {"module": "Multi-Cannon", "size": "medium", "mount": "gimballed"})],
         "Found it — copied to your clipboard, Commander."),
    ])
    app = _app_with_nav(tmp_path, llm, cap)
    _drive(app, 2)

    assert len(http.calls) == 1                    # searched exactly once
    assert clip.copied == ["Barnard's Star"]       # the SYSTEM name was copied
    assert llm.results[0][2].lower().count("size") >= 1   # turn 1 asked, didn't search


def test_flow_confirm_mode_requires_a_separate_turn(tmp_path):
    """With require_confirmation on, a resolve+self-confirm in ONE turn must not search; only
    the confirmation on the NEXT Commander turn does. Proves the turn-gate through App."""
    http, clip = FakeHttp(), FakeClipboard()
    cap = FindClosestCapability(NavConfig(enabled=True, require_confirmation=True), http=http,
                                get_current_system=(lambda: "Sol"), clipboard=clip)
    args = {"module": "Multi-Cannon", "size": "medium", "mount": "gimballed"}
    llm = ScriptLLM([
        ([("find_closest_module", dict(args))], "What size and mount?"),   # (already narrowed)
        # turn 2: model tries to arm AND self-confirm in the same turn -> gate refuses both
        ([("find_closest_module", dict(args)),
          ("find_closest_module", {**args, "confirmed": True})], "Please confirm."),
        # turn 3: the Commander's separate 'yes' -> searches once
        ([("find_closest_module", {**args, "confirmed": True})], "On it, copied to clipboard."),
    ])
    app = _app_with_nav(tmp_path, llm, cap)
    _drive(app, 2)
    assert http.calls == []                        # nothing searched despite the self-confirm
    _drive(app, 1)
    assert len(http.calls) == 1                    # only the separate-turn confirm searched
    assert clip.copied == ["Barnard's Star"]


def test_flow_cancel_midway_runs_no_search_and_no_copy(tmp_path):
    http, clip = FakeHttp(), FakeClipboard()
    cap = FindClosestCapability(NavConfig(enabled=True), http=http,
                                get_current_system=(lambda: "Sol"), clipboard=clip)
    llm = ScriptLLM([
        # turn 1: ask -> NEED_ATTRS
        ([("find_closest_module", {"module": "multicannon"})], "What size and mount?"),
        # turn 2: Commander says 'never mind' -> the LLM makes NO tool call at all
        ([], "No worries, cancelled."),
    ])
    app = _app_with_nav(tmp_path, llm, cap)
    _drive(app, 2)

    assert http.calls == []                        # a verbal cancel never searches
    assert clip.copied == []                        # and never writes the clipboard


def test_flow_second_ambiguous_answer_loops_before_resolving(tmp_path):
    http, clip = FakeHttp(), FakeClipboard()
    cap = FindClosestCapability(NavConfig(enabled=True), http=http,
                                get_current_system=(lambda: "Sol"), clipboard=clip)
    llm = ScriptLLM([
        ([("find_closest_module", {"module": "limpet"})], "Which limpet controller?"),
        ([("find_closest_module", {"module": "limpet controller"})], "Still several — which?"),
        ([("find_closest_module", {"module": "collector limpet", "size": "3"})],
         "Found it, copied to your clipboard."),
    ])
    app = _app_with_nav(tmp_path, llm, cap)

    # Two ambiguous rounds must loop WITHOUT searching (the point of the test).
    _drive(app, 2)
    r1, r2 = (res[2] for res in llm.results)
    assert "which" in r1.lower() and "which" in r2.lower()
    assert http.calls == []                         # no search during disambiguation

    # The third turn finally resolves -> under the default it searches once.
    _drive(app, 1)
    assert len(http.calls) == 1
