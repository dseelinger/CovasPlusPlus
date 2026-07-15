"""Unit tests for CommsSendCapability's read-back-before-send gate (issue #49, DESIGN §6, §9).

Offline and hermetic: a recording fake executor + fake injector capture what WOULD be pressed
and injected, an injectable clock drives the confirm window. The non-negotiable guarantee under
test is that an OUTWARD-FACING message never leaves without a separate-turn confirmation — plus
channel routing, fail-soft on unbound keys, and message sanitisation.
"""
from __future__ import annotations

from covas.capabilities.comms_capability import (CommsSendCapability, CommsSendConfig,
                                                 clean_message, MAX_MESSAGE_CHARS)
from covas.comms.injector import PASTE_BINDING, SEND_BINDING
from covas.keybinds.binds import KeyBinding


class _FakeExecutor:
    """Records the ED action token of every binding pressed, in order."""

    def __init__(self) -> None:
        self.pressed: list[str] = []

    def press(self, binding) -> None:
        self.pressed.append(binding.action)


class _FakeInjector:
    """Records the injected text + whether send() fired, without touching a clipboard/executor."""

    def __init__(self) -> None:
        self.injected: list[str] = []
        self.sent = 0

    def inject(self, text) -> None:
        self.injected.append(text)

    def send(self) -> None:
        self.sent += 1


class _Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


# Binds: the open-comms box + a distinct key per channel so routing is observable.
_BINDS = {
    "QuickCommsPanel": KeyBinding(action="QuickCommsPanel", key="Key_5"),
    "MultiCrewCommsLocal": KeyBinding(action="MultiCrewCommsLocal", key="Key_1"),
    "MultiCrewCommsWing": KeyBinding(action="MultiCrewCommsWing", key="Key_2"),
    "MultiCrewCommsSquadron": KeyBinding(action="MultiCrewCommsSquadron", key="Key_3"),
}

_ROUTED = {"local": "MultiCrewCommsLocal", "wing": "MultiCrewCommsWing",
           "squadron": "MultiCrewCommsSquadron", "direct": ""}


def _cfg(**kw) -> CommsSendConfig:
    base = dict(enabled=True, confirm_window=60.0, open_bind="QuickCommsPanel",
                settle_seconds=0.0, channel_binds=dict(_ROUTED))
    base.update(kw)
    return CommsSendConfig(**base)


def _cap(*, binds=None, cfg=None, injector=None, clock=None):
    ex = _FakeExecutor()
    inj = injector if injector is not None else _FakeInjector()
    clk = clock or _Clock()
    cap = CommsSendCapability(
        binds=_BINDS if binds is None else binds,
        executor=ex,
        config=cfg or _cfg(),
        injector=inj,
        clock=clk,
        sleep=lambda _s: None,
    )
    return cap, ex, inj, clk


# --- tools -----------------------------------------------------------------

def test_tools_expose_send_confirm_cancel():
    cap, _, _, _ = _cap()
    names = {t["name"] for t in cap.tools()}
    assert names == {"send_comms_message", "confirm_comms_send", "cancel_comms_send"}


def test_send_tool_channel_enum_lists_four_channels():
    cap, _, _, _ = _cap()
    send = next(t for t in cap.tools() if t["name"] == "send_comms_message")
    assert send["input_schema"]["properties"]["channel"]["enum"] == \
        ["local", "wing", "squadron", "direct"]


# --- compose never sends ---------------------------------------------------

def test_compose_does_not_send():
    cap, ex, inj, _ = _cap()
    msg = cap.run_tool("send_comms_message", {"channel": "local", "message": "o7"})
    assert ex.pressed == []          # nothing pressed on compose
    assert inj.injected == []        # nothing injected
    assert inj.sent == 0
    assert "o7" in msg and "confirm" in msg.lower()


def test_compose_reads_message_back_verbatim():
    cap, _, _, _ = _cap()
    msg = cap.run_tool("send_comms_message",
                       {"channel": "wing", "message": "forming up at the nav beacon"})
    assert "forming up at the nav beacon" in msg
    assert "wing" in msg.lower()


# --- the read-back-before-send gate (turn-gated) ---------------------------

def test_confirm_in_same_turn_is_refused():
    cap, ex, inj, _ = _cap()
    cap.new_turn()                                   # turn 1 (the compose utterance)
    cap.run_tool("send_comms_message", {"channel": "local", "message": "o7"})
    msg = cap.run_tool("confirm_comms_send", {})     # same turn -> not a real confirmation
    assert ex.pressed == [] and inj.sent == 0
    assert "separate" in msg.lower() or "new command" in msg.lower()


def test_confirm_on_new_turn_sends_full_sequence():
    cap, ex, inj, _ = _cap()
    cap.new_turn()                                   # turn 1: "tell local o7"
    cap.run_tool("send_comms_message", {"channel": "local", "message": "o7"})
    cap.new_turn()                                   # turn 2: "confirm"
    msg = cap.run_tool("confirm_comms_send", {})
    # open box -> select the local channel key -> (inject text) -> send
    assert ex.pressed == ["QuickCommsPanel", "MultiCrewCommsLocal"]
    assert inj.injected == ["o7"]
    assert inj.sent == 1
    assert "sent" in msg.lower()


def test_confirm_without_compose_is_noop():
    cap, ex, inj, _ = _cap()
    msg = cap.run_tool("confirm_comms_send", {})
    assert ex.pressed == [] and inj.sent == 0
    assert "nothing to send" in msg.lower()


def test_confirm_window_expiry():
    clk = _Clock()
    cap, ex, inj, _ = _cap(cfg=_cfg(confirm_window=30.0), clock=clk)
    cap.new_turn()
    cap.run_tool("send_comms_message", {"channel": "local", "message": "o7"})
    cap.new_turn()
    clk.t += 60.0                                    # past the 30s window
    msg = cap.run_tool("confirm_comms_send", {})
    assert ex.pressed == [] and inj.sent == 0
    assert "expired" in msg.lower()


def test_cancel_discards_pending():
    cap, ex, inj, _ = _cap()
    cap.new_turn()
    cap.run_tool("send_comms_message", {"channel": "local", "message": "o7"})
    assert "discarded" in cap.run_tool("cancel_comms_send", {}).lower()
    cap.new_turn()
    # a confirm after cancel finds nothing armed
    msg = cap.run_tool("confirm_comms_send", {})
    assert inj.sent == 0 and "nothing to send" in msg.lower()


def test_cancel_without_pending_is_noop():
    cap, _, _, _ = _cap()
    assert "nothing to cancel" in cap.run_tool("cancel_comms_send", {}).lower()


def test_recompose_replaces_pending_message():
    cap, ex, inj, _ = _cap()
    cap.new_turn()
    cap.run_tool("send_comms_message", {"channel": "local", "message": "first"})
    cap.run_tool("send_comms_message", {"channel": "wing", "message": "second"})
    cap.new_turn()
    cap.run_tool("confirm_comms_send", {})
    # only the latest compose is sent, on its channel
    assert ex.pressed == ["QuickCommsPanel", "MultiCrewCommsWing"]
    assert inj.injected == ["second"]


# --- channel routing -------------------------------------------------------

def test_channel_routing_presses_the_right_key():
    for channel, token in (("local", "MultiCrewCommsLocal"),
                           ("wing", "MultiCrewCommsWing"),
                           ("squadron", "MultiCrewCommsSquadron")):
        cap, ex, inj, _ = _cap()
        cap.new_turn()
        cap.run_tool("send_comms_message", {"channel": channel, "message": "hi"})
        cap.new_turn()
        cap.run_tool("confirm_comms_send", {})
        assert ex.pressed == ["QuickCommsPanel", token], channel


def test_unconfigured_channel_skips_channel_select():
    """A blank channel token (here: direct) sends on the CURRENT channel — no channel key
    pressed, just open + inject + send. The read-back notes it."""
    cap, ex, inj, _ = _cap()
    cap.new_turn()
    readback = cap.run_tool("send_comms_message", {"channel": "direct", "message": "hey"})
    assert "current" in readback.lower()
    cap.new_turn()
    cap.run_tool("confirm_comms_send", {})
    assert ex.pressed == ["QuickCommsPanel"]     # no channel-select press
    assert inj.injected == ["hey"] and inj.sent == 1


def test_channel_alias_normalisation():
    cap, ex, inj, _ = _cap()
    cap.new_turn()
    cap.run_tool("send_comms_message", {"channel": "squad", "message": "hi"})
    cap.new_turn()
    cap.run_tool("confirm_comms_send", {})
    assert ex.pressed == ["QuickCommsPanel", "MultiCrewCommsSquadron"]


def test_unknown_channel_asks_which():
    cap, ex, _, _ = _cap()
    msg = cap.run_tool("send_comms_message", {"channel": "carrier", "message": "hi"})
    assert "which channel" in msg.lower()


# --- fail-soft on unbound keys ---------------------------------------------

def test_open_bind_unbound_refuses_and_arms_nothing():
    cap, ex, inj, _ = _cap(binds={})     # no open-comms bind at all
    cap.new_turn()
    msg = cap.run_tool("send_comms_message", {"channel": "local", "message": "o7"})
    assert "bind" in msg.lower()
    cap.new_turn()
    # nothing was armed, so a confirm sends nothing
    assert inj.sent == 0
    assert "nothing to send" in cap.run_tool("confirm_comms_send", {}).lower()


def test_configured_channel_key_unbound_refuses():
    # open box is bound, but the wing channel token isn't in the binds
    binds = {"QuickCommsPanel": KeyBinding(action="QuickCommsPanel", key="Key_5")}
    cap, ex, inj, _ = _cap(binds=binds)
    cap.new_turn()
    msg = cap.run_tool("send_comms_message", {"channel": "wing", "message": "hi"})
    assert "wing" in msg.lower() and "bind" in msg.lower()
    assert ex.pressed == [] and inj.sent == 0


def test_joystick_only_open_bind_is_unusable():
    binds = {"QuickCommsPanel": KeyBinding(action="QuickCommsPanel", key=None)}   # unbound/joystick
    cap, _, _, _ = _cap(binds=binds)
    msg = cap.run_tool("send_comms_message", {"channel": "local", "message": "o7"})
    assert "bind" in msg.lower()


# --- message sanitisation --------------------------------------------------

def test_empty_message_rejected():
    cap, _, _, _ = _cap()
    msg = cap.run_tool("send_comms_message", {"channel": "local", "message": "   "})
    assert "nothing to send" in msg.lower()


def test_clean_message_collapses_whitespace_and_newlines():
    # newlines would commit the message early — they MUST be collapsed
    assert clean_message("line one\nline two\t\tend") == "line one line two end"
    assert clean_message("  spaced   out  ") == "spaced out"


def test_clean_message_caps_length():
    long = "x" * (MAX_MESSAGE_CHARS + 50)
    assert len(clean_message(long)) == MAX_MESSAGE_CHARS


def test_multiline_message_is_sent_as_one_line():
    cap, ex, inj, _ = _cap()
    cap.new_turn()
    cap.run_tool("send_comms_message", {"channel": "local", "message": "a\nb"})
    cap.new_turn()
    cap.run_tool("confirm_comms_send", {})
    assert inj.injected == ["a b"]


# --- confirmation is unconditional (no bypass) -----------------------------

def test_config_has_no_confirmation_bypass():
    """Un-confirmed sends are FORBIDDEN — the config exposes no require_confirmation switch."""
    assert not hasattr(CommsSendConfig(), "require_confirmation")


def test_default_config_is_off():
    assert CommsSendConfig().enabled is False


# --- default injector uses the paste + enter bindings ----------------------

def test_default_injector_pastes_then_sends_via_executor():
    """With NO injector passed, the capability builds a clipboard-paste injector on the shared
    executor + copy, so the full sequence (open, channel, Ctrl+V, Enter) records in order and the
    clipboard receives the text."""
    ex = _FakeExecutor()
    copied: list[str] = []
    cap = CommsSendCapability(
        binds=_BINDS, executor=ex, config=_cfg(),
        copy=copied.append, clock=_Clock(), sleep=lambda _s: None)
    cap.new_turn()
    cap.run_tool("send_comms_message", {"channel": "local", "message": "o7"})
    cap.new_turn()
    cap.run_tool("confirm_comms_send", {})
    assert ex.pressed == ["QuickCommsPanel", "MultiCrewCommsLocal",
                          PASTE_BINDING.action, SEND_BINDING.action]
    assert copied == ["o7"]
