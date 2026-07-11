"""Settings capability — change (or read back) any COVAS++ setting by voice (Prompt N2).

LLM-native, like find-closest: the tool DESCRIPTION steers Claude to pass its best
interpretation of WHICH setting the Commander named and the new value AS SPOKEN; the
capability does the deterministic work — resolve the spoken name to a schema `Setting`,
validate the value against that setting's type/range/options, write it to overrides.json
via the injected apply hook, and speak a confirmation back.

It projects from the SAME `settings_schema` the web page uses (Prompt N1), so the two
surfaces can never drift: a setting added to the schema is instantly voice-settable, with
the same validation. Nothing here guesses — an invalid value is refused WITH the valid
options, and an unrecognized setting routes the Commander to help ("what can I change").

Everything with a side effect is injected (read current value, apply a patch, resolve
dynamic options), so the default `pytest` run exercises the whole dialog offline and free
(DESIGN §9). Fail soft throughout — any error is spoken, never raised into the voice loop.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Optional

from .. import settings_schema as schema
from ..settings_schema import Setting
from .base import HelpMeta

_GET_TOOL = "get_setting"
_SET_TOOL = "set_setting"

_SET_DESCRIPTION = (
    "Change ONE COVAS++ setting by voice. Use when the Commander asks to change / adjust / "
    "turn on or off / set a setting — e.g. 'turn personality off', 'set thinking to high', "
    "'use the George voice', 'set max tokens to 2000', 'switch to Opus', 'use the small "
    "whisper model'.\n"
    "  - `setting`: your best interpretation of WHICH setting they named (e.g. 'personality', "
    "'whisper model', 'thinking', 'the voice', 'web search', 'max tokens').\n"
    "  - `value`: the new value AS SPOKEN (e.g. 'off', 'high', 'small', 'George', '2000', "
    "'opus').\n"
    "The tool validates the value against the setting's allowed type / range / options and "
    "returns a ready-to-speak confirmation. If the value is INVALID it returns the valid "
    "options — relay them and do NOT guess a different value. If the setting isn't "
    "recognized it tells you what CAN be changed. Relay the tool's reply."
)
_GET_DESCRIPTION = (
    "Report the CURRENT value of one COVAS++ setting — e.g. 'what's my whisper model', 'is "
    "personality on', 'which voice are you using', 'what's the reply length cap'. `setting` "
    "is your interpretation of which one. Relay the tool's reply."
)

_SET_SCHEMA = {
    "name": _SET_TOOL,
    "description": _SET_DESCRIPTION,
    "input_schema": {
        "type": "object",
        "properties": {
            "setting": {"type": "string",
                        "description": "Which setting to change (your interpretation of what "
                                       "the Commander named)."},
            "value": {"type": "string",
                      "description": "The new value, as the Commander said it."},
        },
        "required": ["setting", "value"],
    },
}
_GET_SCHEMA = {
    "name": _GET_TOOL,
    "description": _GET_DESCRIPTION,
    "input_schema": {
        "type": "object",
        "properties": {
            "setting": {"type": "string",
                        "description": "Which setting to report (your interpretation)."},
        },
        "required": ["setting"],
    },
}


class SettingsCapability:
    """Advertises `get_setting` / `set_setting` and runs the resolve → validate → apply flow.

    Injected seams (so the default test run is offline):
      * `get_value(setting)`   — the setting's current value (app: read from live config).
      * `apply_patch(patch)`   — persist a nested config patch to overrides.json + reload
                                 (app: `App.update_settings`).
      * `options_for(setting)` — resolve a DYNAMIC enum's `(value, label)` options (models,
                                 ElevenLabs voices/models); may return None when unavailable.
                                 Static enums are read straight from the schema, so this is
                                 only consulted for `options_source` settings.
    """

    def __init__(
        self,
        *,
        get_value: Callable[[Setting], Any],
        apply_patch: Callable[[dict], None],
        options_for: Optional[Callable[[Setting], Optional[list]]] = None,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._get_value = get_value
        self._apply = apply_patch
        self._options_for = options_for
        self._log = log

    # -- capability interface ---------------------------------------------------------
    def tools(self) -> list[dict]:
        return [_GET_SCHEMA, _SET_SCHEMA]

    def help_meta(self) -> HelpMeta:
        return HelpMeta(
            category="settings",
            group="settings",
            one_liner=("I change my settings by voice — the whisper model, thinking depth, "
                       "personality, the voice, web search, and more."),
            example="turn personality off",
            help_when_active=("Name a setting and a value — like 'set thinking to high', 'use "
                              "the George voice', or 'turn web search off'. Ask 'what can I "
                              "change' and I'll run through the options."),
        )

    def run_tool(self, name: str, inp: dict) -> str:
        try:
            if name == _GET_TOOL:
                return self._get(inp)
            if name == _SET_TOOL:
                return self._set(inp)
            return f"Unknown tool: {name}"
        except Exception as e:  # noqa: BLE001 — the voice loop must survive any tool error
            self._logline(f"error: {e}")
            return f"Settings error: {e}"

    # -- get --------------------------------------------------------------------------
    def _get(self, inp: dict) -> str:
        spoken = str(inp.get("setting") or "").strip()
        if not spoken:
            return "Which setting do you want to check?"
        matches = find_settings(spoken)
        if not matches:
            return self._unknown(spoken)
        if len(matches) > 1:
            return self._ambiguous(matches)
        s = matches[0]
        display = self._display_value(s, self._get_value(s))
        if s.type == "bool":
            return f"{s.label} is {display}."
        return f"{s.label} is {display}."

    # -- set --------------------------------------------------------------------------
    def _set(self, inp: dict) -> str:
        spoken = str(inp.get("setting") or "").strip()
        if not spoken:
            return "Which setting should I change?"
        matches = find_settings(spoken)
        if not matches:
            return self._unknown(spoken)
        if len(matches) > 1:
            return self._ambiguous(matches)
        s = matches[0]

        raw = inp.get("value")
        if raw is None or str(raw).strip() == "":
            return f"What should I set {s.label.lower()} to?"

        value, display, err = self._coerce(s, raw)
        if err:
            self._logline(f"rejected {s.key}={raw!r}: {err}")
            return err

        patch = schema.set_value({}, s, value)
        # The voice id and its display name travel together — keep them in sync so the web
        # page and logs show the friendly name, not just the id.
        if s.key == "elevenlabs.voice_id":
            schema.set_value(patch, schema.by_key["elevenlabs.voice_name"], display)

        self._apply(patch)
        self._logline(f"set {s.key} = {value!r} ({display})")
        return self._confirm(s, display)

    # -- value coercion ---------------------------------------------------------------
    def _coerce(self, s: Setting, raw: Any) -> tuple[Any, str, Optional[str]]:
        """Resolve a spoken value to a canonical, validated value.

        Returns ``(value, display, None)`` on success or ``(None, "", error)`` on failure,
        where `error` is a ready-to-speak refusal that names the valid options — never a
        guess. Enum values are matched against the (static or dynamic) option list by value
        OR spoken label ('George' -> the voice id; 'opus' -> claude-opus-4-8)."""
        if s.type == "enum":
            pairs = self._option_pairs(s)
            if pairs is None:
                return None, "", (f"I can't reach the list of {s.label.lower()} options right "
                                  "now — try again in a moment.")
            value, ambiguous = _resolve_option(pairs, str(raw))
            if ambiguous:
                return None, "", (f"Did you mean {_or_list([_pretty(l) for _v, l in ambiguous])}? "
                                  "Tell me which one.")
            if value is None:
                opts = _or_list([_pretty(l) for _v, l in pairs][:10])
                return None, "", f"'{raw}' isn't a valid {s.label.lower()}. Options: {opts}."
            return value, _pretty(_label_for(pairs, value)), None

        # bool / int / float / string / path — the schema validator handles coercion + range,
        # and its error message already names the setting and the valid inputs.
        value, err = schema.validate_value(s, raw)
        if err:
            return None, "", err
        if s.type == "bool":
            return value, ("on" if value else "off"), None
        display = "" if value == "" else str(value)
        return value, display, None

    def _confirm(self, s: Setting, display: str) -> str:
        if s.type == "bool":
            return f"{s.label} turned {display}."
        if display == "":
            return f"{s.label} cleared."
        return f"{s.label} set to {display}."

    # -- display ----------------------------------------------------------------------
    def _display_value(self, s: Setting, value: Any) -> str:
        if s.type == "bool":
            return "on" if value else "off"
        if s.type == "enum":
            pairs = self._option_pairs(s)
            if pairs is not None:
                return _pretty(_label_for(pairs, value))
        if value is None or value == "":
            return "not set"
        return str(value)

    def _option_pairs(self, s: Setting) -> Optional[list]:
        """`(value, label)` options for an enum: static ones straight from the schema, dynamic
        ones (models, voices) via the injected resolver (None if it can't fetch them)."""
        if s.options is not None:
            return [(v, v) for v in s.options]
        if self._options_for is not None:
            return self._options_for(s)
        return None

    # -- unresolved-setting handling (routes to help) ---------------------------------
    def _unknown(self, spoken: str) -> str:
        return (f"I don't have a setting called '{spoken}'. I can change things like the "
                "whisper model, thinking depth, personality, the voice, or web search — ask "
                "'what can I change' and I'll run through the full list.")

    def _ambiguous(self, matches: list[Setting]) -> str:
        labels = _dedupe([m.label for m in matches])[:5]
        return f"Did you mean {_or_list(labels)}? Tell me which one."

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)


# ---- pure matching helpers (module-level so they're unit-testable) ------------------------

_ARTICLE = re.compile(r"^(?:the|my|your|a|an)\s+")


def _norm(text: str) -> str:
    """Fold a spoken name for comparison: lowercase, collapse whitespace, drop a leading
    article. Keeps it forgiving of 'the'/'my' without over-normalizing real words."""
    t = " ".join(str(text or "").lower().split())
    while True:
        m = _ARTICLE.match(t)
        if not m:
            break
        t = t[m.end():]
    return t.strip()


def _terms(s: Setting) -> set[str]:
    """The normalized phrasings a setting can be named by: its spoken phrasings, its label,
    and its dotted key (in case the model passes it verbatim)."""
    terms = {_norm(p) for p in s.phrasings}
    terms.add(_norm(s.label))
    terms.add(s.key.lower())
    return {t for t in terms if t}


def find_settings(spoken: str) -> list[Setting]:
    """Resolve a spoken setting name to schema settings. Exact (normalized) matches win; if
    there are none, fall back to substring overlap. Returns [] (unknown), [one] (resolved),
    or [several] (ambiguous). Hidden settings (e.g. the paired voice name) are never matched."""
    q = _norm(spoken)
    if not q:
        return []
    visible = [s for s in schema.SCHEMA if not s.hidden]
    exact = [s for s in visible if q in _terms(s)]
    if exact:
        return _dedupe_settings(exact)
    weak = [s for s in visible
            if any(len(t) >= 3 and (q in t or t in q) for t in _terms(s))]
    return _dedupe_settings(weak)


def _resolve_option(pairs: list, spoken: str) -> tuple[Optional[str], Optional[list]]:
    """Match a spoken value against `(value, label)` option pairs. Returns
    ``(value, None)`` on a unique match, ``(None, candidates)`` when several partials match
    (ambiguous), or ``(None, None)`` when nothing matches. Exact value/label wins over a
    substring hit, so 'high' picks 'High' and doesn't also snag a longer option."""
    q = _norm(spoken)
    if not q:
        return None, None
    for value, label in pairs:
        if q == str(value).lower() or q == _norm(label):
            return value, None
    hits: list = []
    for value, label in pairs:
        if len(q) >= 2 and (q in str(value).lower() or q in _norm(label)):
            if value not in [h[0] for h in hits]:
                hits.append((value, label))
    if len(hits) == 1:
        return hits[0][0], None
    if len(hits) > 1:
        return None, hits
    return None, None


def _label_for(pairs: list, value: Any) -> str:
    for v, label in pairs:
        if v == value:
            return label
    return str(value)


_BRACKET = re.compile(r"\s*\[[^\]]*\]\s*$")


def _pretty(label: str) -> str:
    """Trim a trailing '[category]' tag from a voice label for speech ('George [premade]' ->
    'George'). Leaves plain option values untouched."""
    return _BRACKET.sub("", str(label)).strip() or str(label)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out


def _dedupe_settings(settings: list[Setting]) -> list[Setting]:
    seen: set[str] = set()
    out: list[Setting] = []
    for s in settings:
        if s.key not in seen:
            seen.add(s.key)
            out.append(s)
    return out


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
