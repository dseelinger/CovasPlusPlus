"""Canonical Elite Dangerous "game mode" vocabulary (#29).

The single source of truth for the four modes the Commander can be in, shared by the
producer (the Status.json decoder in `ed/status.py`, which derives the current mode from the
Flags/Flags2 bitfields) and the consumer (mode-gated keybind advertisement in
`capabilities/keybind_capability.py`, which only offers actions valid for the current mode).

Kept in its own leaf module — zero imports — so both sides depend on the same string values
without either side depending on the other.
"""
from __future__ import annotations

MODE_MAINSHIP = "mainship"   # flying the main ship (Flags InMainShip)
MODE_FIGHTER = "fighter"     # in a deployed ship-launched fighter (Flags InFighter)
MODE_SRV = "srv"             # driving the SRV (Flags InSRV)
MODE_ON_FOOT = "on_foot"     # Odyssey on-foot / disembarked (Flags2 OnFoot)

# Every valid mode, for validation/iteration. None (unknown) is deliberately NOT a member.
GAME_MODES: tuple[str, ...] = (MODE_MAINSHIP, MODE_FIGHTER, MODE_SRV, MODE_ON_FOOT)
