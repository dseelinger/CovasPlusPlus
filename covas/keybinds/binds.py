"""Resolve + parse the ACTIVE Elite Dangerous key bindings (DESIGN §6).

ED stores bindings as XML under
    %LOCALAPPDATA%\\Frontier Developments\\Elite Dangerous\\Options\\Bindings\\
The Commander can have several presets; the *active* one is named in `StartPreset.4.start`
(older ED: `StartPreset.start`), and its bindings live in `<preset>.4.0.binds`. We resolve
the active preset by name rather than globbing `*.binds` and guessing — a machine often has
stale/default preset files sitting alongside the real one, so a glob would pick the wrong
bindings. A config override (`[keybinds].binds_file`) is the escape hatch when auto-detection
fails.

For each *action* (e.g. `LandingGearToggle`) we extract the **keyboard** binding
specifically: the `Primary`/`Secondary` entry with `Device="Keyboard"`. The executor injects
keyboard scancodes and physically cannot press a joystick/HOTAS button, so a joystick-only
action is marked *unusable* with a clear "bind it to a key in-game" message rather than
silently doing nothing.

Everything here is pure/offline and unit-tested against a sample `.binds` fixture; no
ctypes, no injection — that's `executor.py`.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

# ED's bindings live under the Windows user profile's LOCALAPPDATA. Resolved at runtime
# (never a hardcoded C:\Users\... path — see the repo guardrails) so it's portable.
_BINDINGS_SUBPATH = ("Frontier Developments", "Elite Dangerous", "Options", "Bindings")

# The active-preset marker files, newest naming first. Odyssey writes StartPreset.4.start
# (often several lines, one per binding context — usually all the same preset name); older
# builds wrote a single-line StartPreset.start.
_START_PRESET_FILES = ("StartPreset.4.start", "StartPreset.start")

# The bindings-file extension. ED versions the base name — "<preset>.<major>.<minor>.binds"
# (e.g. .4.0 through .4.2 and up), and occasionally an unversioned "<preset>.binds". We pick
# the HIGHEST version present rather than hardcoding one, so a game update can't hide the binds.
_BINDS_EXT = ".binds"

# The device string ED uses for keyboard bindings. A joystick/HOTAS uses a hex device id
# (e.g. "221B0A57"); an unbound slot uses "{NoDevice}".
_KEYBOARD = "Keyboard"


class BindsError(Exception):
    """Raised when the active bindings file can't be located (no preset marker, missing
    file, unreadable override). Carries a Commander-facing message."""


@dataclass(frozen=True)
class KeyBinding:
    """The keyboard binding for one ED action, as resolved from the active `.binds` file.

    `key` is ED's key token (e.g. "Key_L"); None means the action has no keyboard binding
    (joystick-only or unbound) and therefore can't be driven by the scancode executor.
    `modifiers` are any keyboard modifier tokens (e.g. "Key_LeftShift") that must be held.
    `source` records which slot it came from ("Primary"/"Secondary") for diagnostics.
    """
    action: str
    key: str | None = None
    modifiers: tuple[str, ...] = ()
    source: str | None = None

    @property
    def usable(self) -> bool:
        """True when a keyboard key is bound — i.e. the executor can actually press it."""
        return bool(self.key)

    @property
    def unusable_reason(self) -> str | None:
        """A short Commander-facing reason when unusable, else None."""
        if self.usable:
            return None
        return (f"'{self.action}' has no keyboard binding — bind it to a key in Elite "
                f"Dangerous (Controls > Keyboard) so COVAS can press it.")


def bindings_dir() -> Path:
    """The standard ED bindings directory under %LOCALAPPDATA%. LOCALAPPDATA is resolved
    live (env var, or the conventional ~/AppData/Local fallback) so no username is baked in."""
    import os
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else (Path.home() / "AppData" / "Local")
    return base.joinpath(*_BINDINGS_SUBPATH)


def active_preset(dir_: Path) -> str | None:
    """Read the active preset name from the StartPreset marker file, or None if absent.

    The file may hold several lines (Odyssey writes one per binding context); we take the
    first non-empty line, which is the preset name in the common single-preset setup. The
    name is stripped and used verbatim as the `.binds` filename base."""
    for fname in _START_PRESET_FILES:
        f = dir_ / fname
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            name = line.strip()
            # ED sometimes quotes the name or wraps it; strip surrounding quotes.
            name = name.strip('"').strip()
            if name:
                return name
    return None


def _binds_version(filename: str, preset: str) -> tuple[int, ...] | None:
    """The numeric version of a "<preset>.<v>.binds" file ("Custom.4.2.binds" -> (4, 2)), or
    None if `filename` isn't a numeric-versioned binds file for `preset`."""
    prefix = f"{preset}."
    if not (filename.startswith(prefix) and filename.endswith(_BINDS_EXT)):
        return None
    middle = filename[len(prefix):-len(_BINDS_EXT)]         # "4.2"
    parts = middle.split(".")
    if not middle or not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)


def _preset_binds_file(d: Path, preset: str) -> Path | None:
    """The active preset's bindings file, tolerant of ED's version suffix. Prefer the highest
    "<preset>.<v>.binds" present, else an unversioned "<preset>.binds"; None if neither exists.
    The glob is scoped to THIS preset name, so stale/default presets can't be picked."""
    try:
        versioned = [(v, p) for p in d.glob(f"{preset}.*{_BINDS_EXT}")
                     if (v := _binds_version(p.name, preset)) is not None]
    except OSError:
        versioned = []
    if versioned:
        versioned.sort(key=lambda vp: vp[0])
        return versioned[-1][1]
    plain = d / f"{preset}{_BINDS_EXT}"
    return plain if plain.exists() else None


def resolve_binds_file(cfg: dict | None = None, *, dir_: Path | None = None) -> Path:
    """Locate the active bindings file. Resolution order:

      1. `[keybinds].binds_file` override (absolute, or relative to the bindings dir) —
         the escape hatch for when auto-detection picks wrong.
      2. Active preset from StartPreset.4.start (fallback StartPreset.start) -> the highest
         `<preset>.<version>.binds` present (or unversioned `<preset>.binds`).

    Raises `BindsError` (with a Commander-facing message) if nothing resolvable is found or
    the preset has no bindings file. The glob is scoped to the active preset name — never a
    blind `*.binds` that could grab a stale/default preset."""
    d = dir_ or bindings_dir()

    override = str((cfg or {}).get("keybinds", {}).get("binds_file", "") or "").strip()
    if override:
        p = Path(override)
        if not p.is_absolute():
            p = d / override
        if not p.exists():
            raise BindsError(f"Configured [keybinds].binds_file not found: {p}")
        return p

    preset = active_preset(d)
    if not preset:
        raise BindsError(
            f"Couldn't find the active ED bindings preset in {d} "
            f"(no {' or '.join(_START_PRESET_FILES)}). Is Elite Dangerous installed and "
            f"run at least once? Set [keybinds].binds_file to point at your .binds file.")
    p = _preset_binds_file(d, preset)
    if p is None:
        raise BindsError(
            f"Active preset is '{preset}' but no bindings file for it was found in {d} "
            f"(looked for {preset}.*{_BINDS_EXT}). Set [keybinds].binds_file to override.")
    return p


def _keyboard_slot(slot: ET.Element | None) -> tuple[str, tuple[str, ...]] | None:
    """If `slot` (a <Primary>/<Secondary> element) is a keyboard binding, return
    (key_token, keyboard_modifier_tokens); else None. A slot counts as keyboard only when
    Device="Keyboard" AND it names a non-empty key."""
    if slot is None:
        return None
    if slot.get("Device") != _KEYBOARD:
        return None
    key = (slot.get("Key") or "").strip()
    if not key:
        return None
    # Only keyboard modifiers are reproducible by the scancode executor; a modifier bound
    # to a joystick device is dropped (rare, and unpressable via SendInput anyway).
    mods = tuple(
        (m.get("Key") or "").strip()
        for m in slot.findall("Modifier")
        if m.get("Device") == _KEYBOARD and (m.get("Key") or "").strip()
    )
    return key, mods


def parse_binds(xml_text: str) -> dict[str, KeyBinding]:
    """Parse a `.binds` XML document into {action_name: KeyBinding}.

    For each action element we prefer the Primary keyboard binding, falling back to
    Secondary — so a Commander who put the joystick on Primary and a key on Secondary still
    gets a usable keyboard binding. An action whose slots are all joystick/unbound yields a
    KeyBinding with key=None (unusable). Non-action elements (root-level scalars ED writes,
    like <KeyboardLayout>) that have no Primary/Secondary are skipped.

    Tolerant of a malformed document: a parse error yields an empty mapping rather than
    raising, so a corrupt file degrades to 'no bindings' instead of crashing the loop."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {}

    out: dict[str, KeyBinding] = {}
    for el in root:
        primary = el.find("Primary")
        secondary = el.find("Secondary")
        # Heuristic for "this is an action": it has a Primary or Secondary slot. ED's
        # non-action settings (layout, sensitivity scalars) don't.
        if primary is None and secondary is None:
            continue
        action = el.tag
        kb = _keyboard_slot(primary)
        source = "Primary"
        if kb is None:
            kb = _keyboard_slot(secondary)
            source = "Secondary"
        if kb is None:
            out[action] = KeyBinding(action=action)          # bound elsewhere / unbound
        else:
            key, mods = kb
            out[action] = KeyBinding(action=action, key=key, modifiers=mods, source=source)
    return out


def load_binds(cfg: dict | None = None, *, dir_: Path | None = None) -> dict[str, KeyBinding]:
    """Resolve the active bindings file and parse it. Raises `BindsError` if the file can't
    be located; a file that exists but is unreadable/corrupt yields an empty mapping (the
    capability then reports every action as unusable, fail-soft)."""
    path = resolve_binds_file(cfg, dir_=dir_)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise BindsError(f"Couldn't read bindings file {path}: {e}") from e
    return parse_binds(text)
