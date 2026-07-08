"""ED key token -> Windows hardware scancode map (DESIGN §6).

The executor injects keys via scancode-level `SendInput` (games like Elite read DirectInput
scancodes, not virtual keys — see executor.py). ED names keys with tokens like "Key_L",
"Key_LeftShift", "Key_UpArrow". This module turns a token into the hardware scancode (Set-1
"make" code) plus an `extended` flag for the keys that live on the 0xE0-prefixed extended
range (arrows, right-hand modifiers, the nav cluster, numpad Enter/Slash).

Kept a pure lookup with no ctypes so it imports and unit-tests on any platform. Coverage is
the practical ED-bindable set (letters, digits, F-keys, punctuation, modifiers, arrows,
numpad); an unmapped token returns None and the executor reports it clearly rather than
pressing the wrong key.
"""
from __future__ import annotations

# token -> (scancode, extended?). Scancodes are US-layout Set-1 make codes. `extended` marks
# keys sent with the 0xE0 prefix / KEYEVENTF_EXTENDEDKEY.
SCANCODES: dict[str, tuple[int, bool]] = {
    # --- letters ---------------------------------------------------------------
    "Key_A": (0x1E, False), "Key_B": (0x30, False), "Key_C": (0x2E, False),
    "Key_D": (0x20, False), "Key_E": (0x12, False), "Key_F": (0x21, False),
    "Key_G": (0x22, False), "Key_H": (0x23, False), "Key_I": (0x17, False),
    "Key_J": (0x24, False), "Key_K": (0x25, False), "Key_L": (0x26, False),
    "Key_M": (0x32, False), "Key_N": (0x31, False), "Key_O": (0x18, False),
    "Key_P": (0x19, False), "Key_Q": (0x10, False), "Key_R": (0x13, False),
    "Key_S": (0x1F, False), "Key_T": (0x14, False), "Key_U": (0x16, False),
    "Key_V": (0x2F, False), "Key_W": (0x11, False), "Key_X": (0x2D, False),
    "Key_Y": (0x15, False), "Key_Z": (0x2C, False),
    # --- number row ------------------------------------------------------------
    "Key_1": (0x02, False), "Key_2": (0x03, False), "Key_3": (0x04, False),
    "Key_4": (0x05, False), "Key_5": (0x06, False), "Key_6": (0x07, False),
    "Key_7": (0x08, False), "Key_8": (0x09, False), "Key_9": (0x0A, False),
    "Key_0": (0x0B, False),
    # --- function keys ---------------------------------------------------------
    "Key_F1": (0x3B, False), "Key_F2": (0x3C, False), "Key_F3": (0x3D, False),
    "Key_F4": (0x3E, False), "Key_F5": (0x3F, False), "Key_F6": (0x40, False),
    "Key_F7": (0x41, False), "Key_F8": (0x42, False), "Key_F9": (0x43, False),
    "Key_F10": (0x44, False), "Key_F11": (0x57, False), "Key_F12": (0x58, False),
    # --- whitespace / edit -----------------------------------------------------
    "Key_Space": (0x39, False), "Key_Enter": (0x1C, False), "Key_Tab": (0x0F, False),
    "Key_Backspace": (0x0E, False), "Key_Escape": (0x01, False),
    # --- modifiers -------------------------------------------------------------
    "Key_LeftShift": (0x2A, False), "Key_RightShift": (0x36, False),
    "Key_LeftControl": (0x1D, False), "Key_RightControl": (0x1D, True),
    "Key_LeftAlt": (0x38, False), "Key_RightAlt": (0x38, True),
    # --- punctuation (US layout) ----------------------------------------------
    "Key_Grave": (0x29, False), "Key_Minus": (0x0C, False), "Key_Equals": (0x0D, False),
    "Key_LeftBracket": (0x1A, False), "Key_RightBracket": (0x1B, False),
    "Key_SemiColon": (0x27, False), "Key_Apostrophe": (0x28, False),
    "Key_Comma": (0x33, False), "Key_Period": (0x34, False), "Key_Slash": (0x35, False),
    "Key_BackSlash": (0x2B, False),
    # --- arrows + nav cluster (all extended) -----------------------------------
    "Key_UpArrow": (0x48, True), "Key_DownArrow": (0x50, True),
    "Key_LeftArrow": (0x4B, True), "Key_RightArrow": (0x4D, True),
    "Key_Home": (0x47, True), "Key_End": (0x4F, True),
    "Key_PageUp": (0x49, True), "Key_PageDown": (0x51, True),
    "Key_Insert": (0x52, True), "Key_Delete": (0x53, True),
    # --- numpad ----------------------------------------------------------------
    "Key_Numpad_0": (0x52, False), "Key_Numpad_1": (0x4F, False),
    "Key_Numpad_2": (0x50, False), "Key_Numpad_3": (0x51, False),
    "Key_Numpad_4": (0x4B, False), "Key_Numpad_5": (0x4C, False),
    "Key_Numpad_6": (0x4D, False), "Key_Numpad_7": (0x47, False),
    "Key_Numpad_8": (0x48, False), "Key_Numpad_9": (0x49, False),
    "Key_Numpad_Decimal": (0x53, False), "Key_Numpad_Multiply": (0x37, False),
    "Key_Numpad_Add": (0x4E, False), "Key_Numpad_Subtract": (0x4A, False),
    "Key_Numpad_Divide": (0x35, True), "Key_Numpad_Enter": (0x1C, True),
}


def scancode_for(token: str) -> tuple[int, bool] | None:
    """(scancode, extended) for an ED key token, or None if unmapped. Case-insensitive on
    the token (ED is consistent, but be forgiving)."""
    if not token:
        return None
    hit = SCANCODES.get(token)
    if hit is not None:
        return hit
    # Fall back to a case-insensitive match so "key_l" still resolves.
    low = token.lower()
    for k, v in SCANCODES.items():
        if k.lower() == low:
            return v
    return None
