"""Find-closest-module capability — "find the closest station that sells module X".

The dialogue is multi-turn but the tool is STATELESS: the conversation history *is* the
state, so each re-call just passes more-complete args (module → +size/mount → +confirmed).
The tool is a pure function of its arguments; there's no pending-request object to manage.

Flow the tool DESCRIPTION steers the LLM through (DESIGN / build prompt):
  1. Commander asks for the closest <module>.
  2. The LLM normalizes the (maybe misheard) name and calls the tool. The tool validates
     against the offline taxonomy and returns structured guidance:
       - NEED_ATTRS  → ask for the missing size/mount (never guess), don't search.
       - AMBIGUOUS   → ask which module they meant, don't search.
       - UNKNOWN     → say so, offer suggestions, don't search.
       - RESOLVED    → state the interpretation and ask the Commander to CONFIRM (still no
                       search — `confirmed` is not yet true).
  3. Commander narrows / confirms / cancels. On "cancel / never mind" the LLM simply drops
     the request (it doesn't call the tool) — verbal cancel is an LLM-recognized intent,
     separate from the hard PTT-cancel.
  4. Only when the module is RESOLVED *and* `confirmed=true` does the real, rate-limited
     Spansh query fire — exactly once. It reads the current system (ED context; journal
     fallback), finds the nearest station, copies the SYSTEM name to the clipboard, and
     returns a short spoken line.

Everything I/O-bound is injected (`http`, `get_current_system`, `clipboard`) so the whole
capability is unit-testable offline and the default `pytest` never hits the network or the
real clipboard (DESIGN §9). Fail soft throughout — an unknown module or a failed lookup is
spoken, never raised into the loop.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable

from ..nav import (Ambiguous, NavError, NeedAttrs, Resolved, Unknown,
                   copy as _default_copy, find_closest_module, resolve as _default_resolve)
from ..nav.closest import Http, RequestsHttp, _DEFAULT_BASE_URL, _DEFAULT_UA, _SEARCH_SIZE
from ..nav.modules import TAXONOMY
from .base import HelpMeta, Slot


# ---- config -------------------------------------------------------------------------------


@dataclass(frozen=True)
class NavConfig:
    """Immutable snapshot of `[nav]`. Off by default; the capability isn't registered unless
    `enabled`."""
    enabled: bool = False
    base_url: str = _DEFAULT_BASE_URL
    user_agent: str = _DEFAULT_UA
    default_pad_size: str = "L"          # my main ships need Large — configurable
    search_size: int = _SEARCH_SIZE
    # Require an explicit separate-turn confirmation before the (rate-limited) search fires.
    # DEFAULT OFF: a resolved module searches immediately — this is a read-only lookup, and
    # in practice the extra "confirm" turn is friction. When ON, a hard turn-gate (mirroring
    # the keybind safety layer) makes the confirmation real — the model can't self-confirm
    # inside the arming turn the way it otherwise will.
    require_confirmation: bool = False

    @classmethod
    def from_cfg(cls, cfg: dict) -> "NavConfig":
        n = cfg.get("nav", {}) or {}
        d = cls()
        pad = str(n.get("default_pad_size", d.default_pad_size) or "").strip()
        return cls(
            enabled=bool(n.get("enabled", False)),
            base_url=str(n.get("base_url", d.base_url) or d.base_url),
            user_agent=str(n.get("user_agent", d.user_agent) or d.user_agent),
            default_pad_size=pad,
            search_size=int(n.get("search_size", d.search_size) or d.search_size),
            require_confirmation=bool(n.get("require_confirmation", False)),
        )


# ---- tool ---------------------------------------------------------------------------------

_TOOL_NAME = "find_closest_module"

_SHARED_STEPS = (
    "1. Normalize the spoken module name to a real module (e.g. 'multiple cannon' or "
    "'multicannon' -> Multi-Cannon) and call this tool with `module` set to your best "
    "interpretation.\n"
    "2. The tool replies with structured guidance. If it says attributes are MISSING "
    "(size and/or mount), ask the Commander for exactly those — offering the valid options "
    "it lists — and NEVER guess them. If it says the name is AMBIGUOUS, ask which one. If "
    "UNKNOWN, say so and offer the suggestions.\n"
)
_CANCEL_STEP = (
    "If the Commander says 'cancel' / 'never mind' / 'forget it', DROP the request and "
    "acknowledge — do NOT call this tool.\n"
)
# When the tool returns a result it ends with the copied-to-clipboard note. Tell the model to
# keep it — it paraphrases freely and will otherwise drop it (observed live).
_REPORT_STEP = (
    "When the tool returns a result, relay the station, system, and distance, and ALWAYS "
    "tell the Commander that the system name has been copied to their clipboard."
)

_DESC_NO_CONFIRM = (
    "Find the closest Elite Dangerous station that SELLS a given outfitting module, by "
    "distance from the Commander's current system, and copy that system's name to the "
    "clipboard. Resolve the module CONVERSATIONALLY, then search:\n"
    + _SHARED_STEPS +
    "3. As soon as the module is fully specified (name plus any required size/mount), the "
    "tool searches immediately and returns the nearest station — no separate confirmation "
    "step. " + _CANCEL_STEP +
    "It is stateless — re-call it each turn with everything known so far (module, then "
    "+size/+mount). " + _REPORT_STEP
)
_DESC_CONFIRM = (
    "Find the closest Elite Dangerous station that SELLS a given outfitting module, by "
    "distance from the Commander's current system, and copy that system's name to the "
    "clipboard. Resolve the module CONVERSATIONALLY before searching:\n"
    + _SHARED_STEPS +
    "3. When the tool reports the module is RESOLVED, tell the Commander your interpretation "
    "and ask them to CONFIRM before searching. Do NOT set `confirmed` until they actually "
    "confirm on a LATER turn — a confirmation in the same turn you resolved is refused.\n"
    "4. " + _CANCEL_STEP +
    "5. Only once the Commander confirms on a separate command, call the tool again with the "
    "same module args plus `confirmed=true` to run the one-shot search. " + _REPORT_STEP
)

_SCHEMA_PROPS = {
    "module": {
        "type": "string",
        "description": "Your best interpretation of the module name (e.g. 'Multi-Cannon', "
                       "'Frame Shift Drive', 'Fuel Scoop').",
    },
    "size": {
        "type": "string",
        "description": "Module size when relevant: a word for weapons "
                       "(small/medium/large/huge) or a class number for internals (1-8). "
                       "Omit if unknown — the tool will ask.",
    },
    "mount": {
        "type": "string",
        "description": "Weapon mount when relevant: fixed / gimballed / turreted. Omit if "
                       "unknown — the tool will ask.",
    },
    "pad_size": {
        "type": "string",
        "description": "Required landing-pad size (S/M/L) for the Commander's ship. Omit to "
                       "use the configured default.",
    },
}
_CONFIRMED_PROP = {
    "confirmed": {
        "type": "boolean",
        "description": "Set true ONLY after the Commander has explicitly confirmed the "
                       "resolved module on a separate turn. Triggers the one-shot search.",
    },
}


def _build_tool(require_confirmation: bool) -> dict:
    """The tool schema, tailored to the confirmation mode so the model's instructions match
    the actual behavior (search-on-resolve vs confirm-first)."""
    props = dict(_SCHEMA_PROPS)
    if require_confirmation:
        props.update(_CONFIRMED_PROP)
    return {
        "name": _TOOL_NAME,
        "description": _DESC_CONFIRM if require_confirmation else _DESC_NO_CONFIRM,
        "input_schema": {"type": "object", "properties": props, "required": ["module"]},
    }


class FindClosestCapability:
    """Advertises `find_closest_module` and runs the resolve → confirm → search dialog.

    Injected seams (all so the default test run is offline):
      * `http` — the Http poster for Spansh (RequestsHttp in the app; a fake in tests).
      * `get_current_system` — Callable[[], str|None] returning the Commander's current
        system (ED context, with a journal fallback the app wires up), or None.
      * `resolve` / `search` / `clipboard` — pure/offline deps, defaulted to the real ones
        but overridable in tests.
    """

    def __init__(
        self,
        config: NavConfig,
        *,
        http: Http | None = None,
        get_current_system: Callable[[], str | None] | None = None,
        resolve: Callable[..., object] = _default_resolve,
        search: Callable[..., object] = find_closest_module,
        clipboard: Callable[[str], None] = _default_copy,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._cfg = config
        self._http = http if http is not None else RequestsHttp()
        self._current_system = get_current_system
        self._resolve = resolve
        self._search = search
        self._clipboard = clipboard
        self._log = log
        self._tool = _build_tool(config.require_confirmation)
        # Confirmation turn-gate (only used when require_confirmation is on): _turn counts
        # Commander utterances (advanced by new_turn()); _armed_turn is the turn a resolve
        # was armed on, so a confirmation is only honored on a genuinely later turn.
        self._lock = threading.Lock()
        self._turn = 0
        self._armed_turn: int | None = None

    # -- capability interface ---------------------------------------------------------
    def tools(self) -> list[dict]:
        return [self._tool]

    def help_meta(self) -> HelpMeta:
        """Describe this capability for the help subsystem (Search Prompt 1). Templated help
        is projected from this — nothing here is generated by an LLM."""
        return HelpMeta(
            category="outfitting",
            one_liner=("I find the closest station selling an outfitting module and copy that "
                       "system to your clipboard."),
            example="find the closest multi-cannon",
            slots=(
                Slot(param="module",
                     phrasings=("the module name", "the module"),
                     example="find the nearest fuel scoop",
                     help_text="Name the module you want — a multi-cannon, a fuel scoop, a "
                               "frame shift drive, and so on."),
                Slot(param="size",
                     phrasings=("a size", "a class"),
                     example="a large multi-cannon",
                     help_text="For modules that come in several sizes, say the size — small, "
                               "medium, large, huge, or a class number."),
                Slot(param="mount",
                     phrasings=("a mount", "fixed, gimballed, or turreted"),
                     example="a gimballed multi-cannon",
                     help_text="For weapons, say the mount: fixed, gimballed, or turreted."),
                Slot(param="pad_size",
                     phrasings=("a landing pad size", "a pad size"),
                     example="somewhere with a large pad",
                     help_text="Restrict to stations with a given landing-pad size — small, "
                               "medium, or large."),
            ),
            help_when_active=("Tell me the module — and its size or mount if I ask — and I'll "
                              "find the nearest station that sells it."),
        )

    def help_vocabulary(self) -> dict[str, list[str]]:
        """The canonical module names help's failure-recovery mode matches an unresolved term
        against, so a suggested correction is always a real module (never invented)."""
        return {"module": [spec.name for spec in TAXONOMY]}

    def new_turn(self) -> None:
        """Called by the app once per Commander utterance. Advances the confirmation gate so
        a resolved module can only be confirmed on a genuinely new command (the model can't
        arm-and-confirm within one turn). No-op unless require_confirmation is on."""
        with self._lock:
            self._turn += 1

    def run_tool(self, name: str, inp: dict) -> str:
        if name != _TOOL_NAME:
            return f"Unknown tool: {name}"
        try:
            return self._handle(inp)
        except Exception as e:  # noqa: BLE001 — the voice loop must survive any tool error
            self._logline(f"error: {e}")
            return f"Module lookup error: {e}"

    # -- dialog -----------------------------------------------------------------------
    def _handle(self, inp: dict) -> str:
        module = str(inp.get("module") or "").strip()
        if not module:
            return "Which module should I find the closest station for?"

        outcome = self._resolve(module, inp.get("size"), inp.get("mount"))

        if isinstance(outcome, Unknown):
            return self._say_unknown(outcome)
        if isinstance(outcome, Ambiguous):
            return self._say_ambiguous(outcome)
        if isinstance(outcome, NeedAttrs):
            return self._say_need_attrs(outcome)
        if isinstance(outcome, Resolved):
            # Default: search as soon as the module is resolved (read-only lookup; the extra
            # confirm turn is friction). require_confirmation flips on a real turn-gate.
            if not self._cfg.require_confirmation:
                return self._do_search(outcome, inp)
            return self._confirm_gate(outcome, inp)
        return "I couldn't interpret that module — try naming it another way."

    def _confirm_gate(self, resolved: Resolved, inp: dict) -> str:
        """Turn-gated confirmation (require_confirmation on). A `confirmed=true` call only
        searches if a resolve was armed on an EARLIER Commander turn — so the model can't
        arm-and-confirm inside one turn (exactly what Haiku does otherwise)."""
        with self._lock:
            armed_turn, cur = self._armed_turn, self._turn
        if bool(inp.get("confirmed")):
            if armed_turn is not None and cur > armed_turn:
                return self._do_search(resolved, inp)
            # Self-confirm in the arming turn (or never armed): refuse, and (re)arm at this
            # turn so the NEXT command genuinely confirms.
            with self._lock:
                self._armed_turn = self._turn
            self._logline(f"refused same-turn confirm for {resolved.label}")
            return (f"I've got {resolved.label} ready — but I need you to confirm on a "
                    f"separate command. Say 'confirm' or 'yes' and I'll search.")
        with self._lock:
            self._armed_turn = self._turn
        self._logline(f"resolved '{resolved.label}'; awaiting confirmation")
        return (f"Resolved to {resolved.label}. Say 'confirm' on a separate command and I'll "
                f"find the closest station that sells it. (Say 'cancel' to drop it.)")

    def _say_unknown(self, o: Unknown) -> str:
        if o.suggestions:
            return (f"I don't recognize '{o.query}' as a module. Did you mean "
                    f"{_or_list(o.suggestions)}?")
        return (f"I don't recognize '{o.query}' as a module. Tell me the module name another "
                "way.")

    def _say_ambiguous(self, o: Ambiguous) -> str:
        return (f"That could be a few modules — {_or_list(o.candidates)}. Which one?")

    def _say_need_attrs(self, o: NeedAttrs) -> str:
        asks: list[str] = []
        for attr in o.missing:
            opts = o.options.get(attr, [])
            if attr == "size":
                asks.append(f"what size ({_or_list(opts)})")
            elif attr == "mount":
                asks.append(f"which mount ({_or_list(opts)})")
        joined = " and ".join(asks) if asks else "a bit more detail"
        return (f"I've got the {o.module}. Before I search, {joined}? I won't guess.")

    def _do_search(self, resolved: Resolved, inp: dict) -> str:
        """The one networked step — fires exactly once, only on RESOLVED + confirmed."""
        system = self._current_system() if self._current_system is not None else None
        pad = self._pad_size(inp)
        try:
            result = self._search(
                resolved, system, self._http,
                pad_size=pad,
                base_url=self._cfg.base_url,
                user_agent=self._cfg.user_agent,
                search_size=self._cfg.search_size,
            )
        except NavError as e:
            self._logline(f"search failed for {resolved.label}: {e}")
            return str(e)

        # Search done — clear any confirmation arm so a later request starts fresh.
        with self._lock:
            self._armed_turn = None
        # Copy the SYSTEM name (what you paste into the galaxy map). Non-fatal on failure —
        # the answer is still spoken.
        copied = self._copy(result.system)
        self._logline(f"nearest {resolved.label}: {result.station} in {result.system} "
                      f"({result.distance_ly:.1f} ly), pad {result.pad}, "
                      f"clipboard={'ok' if copied else 'failed'}")
        return self._say_result(resolved, result, copied)

    def _say_result(self, resolved: Resolved, result, copied: bool) -> str:
        dist = ("in your current system" if result.distance_ly < 0.05
                else f"{result.distance_ly:.1f} light-years away")
        line = (f"Closest {resolved.label}: {result.station} in {result.system}, {dist}. "
                f"Largest pad {result.pad}.")
        arrival = result.extra.get("distance_to_arrival")
        if isinstance(arrival, (int, float)) and arrival >= 1:
            line += f" About {arrival:,.0f} light-seconds from the star."
        line += (f" I've copied {result.system} to your clipboard." if copied
                 else f" (Couldn't copy to the clipboard — the system is {result.system}.)")
        return line

    # -- helpers ----------------------------------------------------------------------
    def _pad_size(self, inp: dict) -> str | None:
        """The pad constraint for this search: the tool arg if given, else the config
        default. 'any' / 'none' / '' disables it."""
        raw = inp.get("pad_size")
        pad = str(raw).strip() if raw is not None else self._cfg.default_pad_size
        if not pad or pad.lower() in ("any", "none", "n/a"):
            return None
        return pad

    def _copy(self, text: str) -> bool:
        try:
            self._clipboard(text)
            return True
        except Exception as e:  # noqa: BLE001 — clipboard is a convenience, never fatal
            self._logline(f"clipboard copy failed: {e}")
            return False

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)


def _or_list(items: list[str]) -> str:
    """Join options for speech: 'A', 'A or B', 'A, B, or C'."""
    items = [str(i) for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} or {items[1]}"
    return ", ".join(items[:-1]) + f", or {items[-1]}"
