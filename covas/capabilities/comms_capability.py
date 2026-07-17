"""Send in-game comms text by voice — local / wing / squadron / direct (issue #49, DESIGN §6).

Compose and send an Elite Dangerous Comms-panel message by voice, without opening the panel
and typing: voice -> open comms (keybind) -> select channel (keybind) -> inject the text
(clipboard-paste) -> send (Enter). The LLM composes the phrasing ("tell local o7", "message
my wing: forming up at the nav beacon") and picks the channel; a deterministic executor +
`ClipboardTextInjector` do the actual input.

THIS ACTION IS OUTWARD-FACING — other Commanders see the message — so the safety model is a
**mandatory read-back-before-send gate**, not a combat guard:

  1. **Compose + arm never sends.** `send_comms_message` records the channel + cleaned text and
     returns a read-back string; nothing is injected yet.
  2. **Confirm on a SEPARATE turn.** `confirm_comms_send` fires the send ONLY when a new
     Commander utterance arrived after the arm (turn-gated via `new_turn()`, exactly like the
     keybind capability) and within `confirm_window`. The model physically cannot compose-and-
     send in one turn, and the Commander hears the exact words before they go out — which also
     guards against a garbled STT reaching strangers.
  3. **Cancel / expiry.** `cancel_comms_send` discards a pending message; an un-confirmed
     message expires after the window.

Un-confirmed sends are FORBIDDEN: unlike a benign keybind, there is no `confirm_required=False`
path here — confirmation is unconditional. Off by default (`[comms_send].enabled`).

Everything is injected (binds, executor, clipboard/injector, clock) so the whole flow is
unit-tested offline: a recording fake executor/injector, a fake clipboard, an injectable clock.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..comms.injector import ClipboardTextInjector, InjectorError
from ..keybinds.binds import KeyBinding
from ..keybinds.executor import ExecutorError
from .base import HelpMeta

# Canonical channels + spoken aliases the model (or a mishear) might use. The tool advertises
# the four canonical names; `_normalize` maps common synonyms so "system"/"squad"/"dm" resolve.
CHANNELS = ("local", "wing", "squadron", "direct")
_CHANNEL_ALIASES = {
    "local": "local", "system": "local", "sys": "local",
    "wing": "wing",
    "squadron": "squadron", "squad": "squadron",
    "direct": "direct", "commander": "direct", "cmdr": "direct", "dm": "direct",
    "private": "direct",
}
# Spoken label per channel for the read-back / result.
_CHANNEL_LABEL = {
    "local": "local/system chat", "wing": "wing chat",
    "squadron": "squadron chat", "direct": "a direct message",
}

# ED chat has a per-message length limit; cap defensively so a runaway composition can't paste a
# wall of text. Well above any normal spoken line.
MAX_MESSAGE_CHARS = 200


def clean_message(text: object) -> str:
    """Normalize a composed message for a single-line chat send: collapse ALL runs of
    whitespace (including newlines/tabs) to single spaces, strip, and cap the length. Stripping
    newlines matters — a stray newline in the pasted text would commit the message early or
    split it — so this is a safety step, not just tidiness."""
    s = " ".join(str(text or "").split())
    return s[:MAX_MESSAGE_CHARS].strip()


@dataclass(frozen=True)
class CommsSendConfig:
    """Immutable snapshot of `[comms_send]`. Off by default; the capability isn't registered
    unless `enabled`.

    `open_bind` is the ED action token that opens the comms text box (default `QuickCommsPanel`,
    which drops the cursor straight into chat). `channel_binds` maps each channel to the ED
    action token that selects it — Elite has no universal per-channel send key, so these are
    Commander-configurable and default EMPTY, meaning "send on ED's currently-selected channel"
    (still fine for the common local-chat case). A configured-but-unbound channel key fails soft
    with a 'bind it in-game' message, exactly like every other keybind action."""
    enabled: bool = False
    confirm_window: float = 60.0            # seconds an armed message stays confirmable
    open_bind: str = "QuickCommsPanel"      # ED token that focuses the chat text box
    settle_seconds: float = 0.15            # pause after focus/channel/paste so the field keeps up
    channel_binds: dict[str, str] = field(default_factory=dict)  # channel -> ED action token

    @classmethod
    def from_cfg(cls, cfg: dict) -> "CommsSendConfig":
        c = cfg.get("comms_send", {}) or {}
        d = cls()
        try:
            window = float(c.get("confirm_window", d.confirm_window))
        except (TypeError, ValueError):
            window = d.confirm_window
        try:
            settle = max(0.0, float(c.get("settle_seconds", d.settle_seconds)))
        except (TypeError, ValueError):
            settle = d.settle_seconds
        open_bind = str(c.get("open_bind", d.open_bind) or "").strip() or d.open_bind
        # Per-channel tokens live under discrete keys (channel_local, channel_wing, …) so they
        # sit as flat TOML scalars rather than a nested table.
        binds = {ch: str(c.get(f"channel_{ch}", "") or "").strip() for ch in CHANNELS}
        return cls(
            enabled=bool(c.get("enabled", False)),
            confirm_window=window,
            open_bind=open_bind,
            settle_seconds=settle,
            channel_binds=binds,
        )


# ---- tools --------------------------------------------------------------------------------

_SEND_TOOL = {
    "name": "send_comms_message",
    "description": (
        "Compose an in-game text message to send in Elite Dangerous and ARM it for sending. "
        "This does NOT send immediately — for safety the Commander must confirm on a SEPARATE "
        "command first. Call this when the Commander asks to message another player / channel "
        "(e.g. 'tell local o7', 'message my wing: forming up at the nav beacon'). Put the exact "
        "words to send in `message` and pick the `channel`. After calling it, READ THE MESSAGE "
        "BACK to the Commander word-for-word and wait for them to confirm ('confirm', 'send it', "
        "'yes') on a new command before calling confirm_comms_send. Never send unconfirmed."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "channel": {
                "type": "string",
                "enum": list(CHANNELS),
                "description": ("Which comms channel to send on: local/system chat, your wing, "
                                "your squadron, or a direct message to the selected Commander."),
            },
            "message": {
                "type": "string",
                "description": "The exact text to send (a short single line — it's typed into chat).",
            },
        },
        "required": ["channel", "message"],
    },
}

_CONFIRM_TOOL = {
    "name": "confirm_comms_send",
    "description": (
        "Confirm and SEND the in-game message you previously composed with send_comms_message. "
        "Only call this after the Commander has confirmed on a NEW, separate command (they said "
        "'confirm', 'send it', 'yes', 'go ahead') AFTER you read the message back. NEVER call it "
        "in the same turn you composed the message — that isn't a real confirmation and will be "
        "refused. Other players will see this message, so an unconfirmed send is never allowed."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

_CANCEL_TOOL = {
    "name": "cancel_comms_send",
    "description": (
        "Discard the in-game message you armed with send_comms_message without sending it. Call "
        "this the moment the Commander says cancel / no / belay / don't send, or wants to reword."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}


class CommsSendCapability:
    """Advertises compose/confirm/cancel and runs the send behind the read-back gate.

    Injected seams (so the default test run is offline + hermetic):
      * `binds`    — {action_token: KeyBinding} parsed from the active .binds file (may be {}).
      * `executor` — a KeyExecutor (or a recording fake) for the open + channel-select presses,
        SHARED with the keybind capability so a hard abort lifts any key it pressed.
      * `injector` — a text injector (clipboard + paste + Enter). Defaults to a
        `ClipboardTextInjector` built on `executor` + `copy`; tests pass a recording fake.
      * `copy`     — the clipboard writer, used only to build the default injector.
      * `clock`    — monotonic clock for the confirm-window (injected in tests).
    """
    # Tiering group (issue #84): the token-budget cluster this capability's tools belong
    # to; the level filter (covas/tiering.py) keeps or drops the whole group as a unit.
    TIERING_GROUP = "keybinds"

    def __init__(
        self,
        *,
        binds: dict[str, KeyBinding],
        executor: object,
        config: CommsSendConfig,
        injector: object | None = None,
        copy: Optional[Callable[[str], None]] = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._binds = binds or {}
        self._executor = executor
        self._cfg = config
        self._injector = injector or ClipboardTextInjector(
            executor=executor, copy=copy, sleep=sleep, settle=config.settle_seconds)
        self._clock = clock
        self._sleep = sleep
        self._log = log
        self._lock = threading.Lock()
        self._pending: dict | None = None   # {channel, message, turn, at}
        self._turn = 0                       # Commander-utterance counter (confirm gate)

    # -- capability interface ---------------------------------------------------------
    def tools(self) -> list[dict]:
        return [_SEND_TOOL, _CONFIRM_TOOL, _CANCEL_TOOL]

    def help_meta(self) -> HelpMeta:
        return HelpMeta(
            category="in-game messages",
            group="your ship",
            one_liner=("I can send Elite Dangerous chat for you — to local, your wing, your "
                       "squadron, or a direct message — composing the words from what you say. "
                       "Because other Commanders see it, I always read the message back and only "
                       "send after you confirm on a separate command."),
            example="tell local o7",
        )

    def run_tool(self, name: str, inp: dict) -> str:
        try:
            if name == "send_comms_message":
                return self._arm(inp.get("channel"), inp.get("message"))
            if name == "confirm_comms_send":
                return self._confirm()
            if name == "cancel_comms_send":
                return self._cancel()
            return f"Unknown comms tool: {name}"
        except Exception as e:  # noqa: BLE001 — the loop must survive any tool error
            self._logline(f"error in {name}: {e}")
            return f"Comms error: {e}"

    def new_turn(self) -> None:
        """Called by the app once per Commander utterance. Advances the turn counter so a send
        is only accepted when confirmation arrives on a genuinely new command (see `_confirm`)
        — the model can't compose-and-send within a single turn."""
        with self._lock:
            self._turn += 1

    # -- compose / confirm / cancel ---------------------------------------------------
    def _arm(self, channel: object, message: object) -> str:
        ch = _normalize(channel)
        if ch is None:
            return ("I can send to local, wing, squadron, or a direct message — which channel "
                    "did you want?")
        msg = clean_message(message)
        if not msg:
            return "There's nothing to send yet — tell me what you want the message to say."

        problem = self._binding_problem(ch)
        if problem is not None:
            self._logline(f"comms to {ch} unusable: {problem}")
            return problem

        with self._lock:
            self._pending = {"channel": ch, "message": msg,
                             "turn": self._turn, "at": self._clock()}
        self._logline(f"armed comms to {ch}: {msg!r} (awaiting confirmation)")
        note = self._channel_note(ch)
        return (f'Ready to send to {_CHANNEL_LABEL[ch]}: "{msg}".{note} Read that back to the '
                f"Commander word-for-word and wait for them to confirm ('confirm' / 'send it') "
                f"on a separate command, then call confirm_comms_send. Say 'cancel' to discard.")

    def _confirm(self) -> str:
        with self._lock:
            p = self._pending
            if not p:
                return "Nothing to send — no message is composed."
            # Turn gate: a real confirmation is a NEW utterance after the compose. This is what
            # makes an un-confirmed send structurally impossible in a single turn.
            if self._turn <= p["turn"]:
                return ("That isn't a separate confirmation yet — the Commander must confirm on a "
                        "new command after you read the message back. Wait for them to say it.")
            if self._clock() - p["at"] > self._cfg.confirm_window:
                self._pending = None
                return ("That message expired for safety — compose it again if you still want "
                        "to send it.")
            channel, message = p["channel"], p["message"]
            self._pending = None

        # Re-check the binds at send time (config could have changed, but mainly to fail soft).
        problem = self._binding_problem(channel)
        if problem is not None:
            self._logline(f"comms to {channel} blocked at confirm: {problem}")
            return problem
        return self._execute(channel, message)

    def _cancel(self) -> str:
        with self._lock:
            had = self._pending is not None
            self._pending = None
        self._logline("comms send cancelled" if had else "comms cancel — nothing armed")
        return ("Discarded that message — nothing was sent." if had
                else "Nothing to cancel — no message is composed.")

    # -- execution --------------------------------------------------------------------
    def _execute(self, channel: str, message: str) -> str:
        """Run the scripted send: open the comms box -> (optional) select the channel -> inject
        the text (clipboard-paste) -> press Enter. The read-back gate has already passed. Fail
        soft: any injection fault returns a spoken error and sends nothing further."""
        try:
            open_binding = self._binds[self._cfg.open_bind]
            self._executor.press(open_binding)
            self._settle()
            token = self._channel_token(channel)
            if token:
                self._executor.press(self._binds[token])
                self._settle()
            self._injector.inject(message)   # clipboard + Ctrl+V — the distinct text step
            self._injector.send()            # Enter commits it
        except (ExecutorError, InjectorError) as e:
            self._logline(f"comms to {channel} injection failed: {e}")
            return f"Couldn't send that message — {e}."
        except Exception as e:  # noqa: BLE001 — never crash the loop on an injection fault
            self._logline(f"comms to {channel} injection error: {e}")
            return f"Comms injection failed: {e}"
        self._logline(f"sent comms to {channel}: {message!r}")
        return f'Sent to {_CHANNEL_LABEL[channel]}: "{message}".'

    def _settle(self) -> None:
        if self._cfg.settle_seconds:
            self._sleep(self._cfg.settle_seconds)

    # -- binds / channel resolution ---------------------------------------------------
    def _channel_token(self, channel: str) -> str:
        """The ED action token that selects `channel`, or '' when none is configured (send on
        the currently-selected channel)."""
        return self._cfg.channel_binds.get(channel, "")

    def _binding_problem(self, channel: str) -> str | None:
        """A Commander-facing reason the message can't be sent because a key it needs isn't
        bound to a keyboard key, or None when it's runnable. Checks the open-comms bind and,
        when the channel has a configured token, that token — mirroring the keybind capability's
        fail-soft 'bind it in-game' message. Paste/Enter are fixed keystrokes, always usable."""
        ob = self._binds.get(self._cfg.open_bind)
        if ob is None or not ob.usable:
            return (f"I can't open the comms text box — bind '{self._cfg.open_bind}' to a key in "
                    f"Elite Dangerous (Controls) so I can start a message.")
        token = self._channel_token(channel)
        if token:
            cb = self._binds.get(token)
            if cb is None or not cb.usable:
                return (f"Your {channel} comms key ('{token}') isn't bound to a keyboard key — "
                        f"bind it in-game, or clear [comms_send].channel_{channel} to send on "
                        f"your current channel.")
        return None

    def _channel_note(self, channel: str) -> str:
        """A short read-back note when no channel-select key is configured, so the Commander
        knows the message goes to whatever channel is currently selected rather than a switch."""
        if self._channel_token(channel):
            return ""
        return (f" (No {channel} channel key is set, so I'll send on your currently-selected "
                f"comms channel — set [comms_send].channel_{channel} to switch automatically.)")

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)


def _normalize(channel: object) -> str | None:
    """Map a spoken/echoed channel name to a canonical channel, or None if unrecognized."""
    key = str(channel or "").strip().lower()
    return _CHANNEL_ALIASES.get(key)
