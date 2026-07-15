"""THROWAWAY spike PoC (issue #46) — 2D transparent always-on-top HUD.

NOT shipped code. Lives under docs/spikes/, outside the covas/ package, on purpose.
Run on Doug's Windows machine to de-risk the 2D sub-issue of the VR/HUD epic (#40):

    .venv\\Scripts\\python.exe docs\\spikes\\hud_2d_overlay_poc.py

What it proves (the whole point of the 2D spike):
  * a borderless, always-on-top window (tkinter, ZERO new dependency — stdlib),
  * a color-keyed transparent background so the desktop/game shows through, and
  * click-through on that transparent area on Windows (`-transparentcolor` passes
    both the pixels AND the mouse hits through).

It renders a static demo HudModel (system / fuel / status / active checklist item /
last callout) and ticks a simulated update loop so you can eyeball legibility and
"stays on top." Press Esc or click the panel's [x] to quit.

Windows-only affordances (`-transparentcolor`, the optional WS_EX_TRANSPARENT tweak)
are exactly the platform COVAS++ ships on, so that is fine for a spike. On a non-Windows
box the color key is ignored and you just get an opaque window — still runnable.

Design note carried into the real feature: the panel is drawn from a tiny `HudModel`
snapshot, the same shape a real `HudCapability` would keep from the EventBus / EDContext.
The real thing renders this to an offscreen RGBA buffer so the SAME layout also feeds the
SteamVR overlay sink (see hud_vr_overlay_poc.py). This PoC keeps it as plain widgets to
stay dependency-free and obvious.
"""

from __future__ import annotations

import sys
import tkinter as tk
from dataclasses import dataclass, field

# Chroma key: a color unlikely to appear in the HUD, made fully transparent + click-through
# on Windows via `-transparentcolor`. Pure magenta is the classic pick.
CHROMA_KEY = "#ff00ff"

# HUD panel palette (kept off the chroma key so it stays opaque/visible).
PANEL_BG = "#0b0f14"      # near-black cockpit panel
ACCENT = "#ff7100"        # ED orange
TEXT = "#d7e3ea"
DIM = "#7a8a95"
OK = "#5fd38a"
WARN = "#ffcc44"


@dataclass
class HudModel:
    """The glanceable snapshot a real HudCapability would keep from the EventBus/EDContext.

    Deliberately tiny and provider-agnostic — the renderer knows only this, never where it
    came from. Mirrors the spike's recommended architecture.
    """

    system: str = "Shinrarta Dezhra"
    station: str = "Jameson Memorial"
    fuel_pct: int = 42
    status: str = "Docked"          # e.g. Docked / Supercruise / In danger
    in_danger: bool = False
    active_task: str = "Buy 5A Frame Shift Drive"
    last_callout: str = "Fuel low — scoopable star 2 jumps out."
    tick: int = field(default=0)


def _simulate(model: HudModel) -> None:
    """Fake some movement so the overlay visibly updates (stands in for real ED events)."""
    model.tick += 1
    # Drain fuel slowly, flip a danger state periodically — just to see redraws land.
    if model.tick % 5 == 0 and model.fuel_pct > 0:
        model.fuel_pct -= 1
    if model.tick % 20 == 0:
        model.in_danger = not model.in_danger
        model.status = "In danger" if model.in_danger else "Docked"


class HudOverlay:
    def __init__(self) -> None:
        self.model = HudModel()
        self.root = tk.Tk()
        self.root.title("COVAS++ HUD spike (#46)")

        # --- the three window tricks the 2D surface depends on ---
        self.root.overrideredirect(True)              # borderless (no title bar / chrome)
        self.root.attributes("-topmost", True)        # always-on-top
        # Color-key transparency: every CHROMA_KEY pixel becomes see-through AND click-through
        # on Windows. This is what makes it an "overlay" and not just a floating window.
        try:
            self.root.attributes("-transparentcolor", CHROMA_KEY)
        except tk.TclError:
            # Non-Windows / unsupported: fall back to a whole-window alpha so it is still usable.
            self.root.attributes("-alpha", 0.9)
        self.root.configure(bg=CHROMA_KEY)

        # Park it top-right of the primary screen.
        w, h = 320, 210
        sw = self.root.winfo_screenwidth()
        self.root.geometry(f"{w}x{h}+{sw - w - 24}+24")

        # The visible panel sits on the chroma-key canvas; only the panel is opaque.
        self.panel = tk.Frame(self.root, bg=PANEL_BG, highlightthickness=1,
                              highlightbackground=ACCENT)
        self.panel.place(x=0, y=0, relwidth=1.0, relheight=1.0)

        self._build_widgets()
        self._bind_quit()
        self._optional_full_click_through()  # commented rationale inside

        self._refresh()

    # -- widgets -------------------------------------------------------------
    def _build_widgets(self) -> None:
        p = self.panel
        tk.Label(p, text="COVAS++", bg=PANEL_BG, fg=ACCENT,
                 font=("Consolas", 13, "bold")).pack(anchor="w", padx=12, pady=(10, 0))

        self.loc = tk.Label(p, bg=PANEL_BG, fg=TEXT, justify="left",
                            font=("Consolas", 10))
        self.loc.pack(anchor="w", padx=12, pady=(6, 0))

        self.status = tk.Label(p, bg=PANEL_BG, fg=OK, font=("Consolas", 10, "bold"))
        self.status.pack(anchor="w", padx=12, pady=(4, 0))

        self.fuel = tk.Label(p, bg=PANEL_BG, fg=TEXT, font=("Consolas", 10))
        self.fuel.pack(anchor="w", padx=12, pady=(4, 0))

        tk.Label(p, text="ACTIVE TASK", bg=PANEL_BG, fg=DIM,
                 font=("Consolas", 8, "bold")).pack(anchor="w", padx=12, pady=(8, 0))
        self.task = tk.Label(p, bg=PANEL_BG, fg=TEXT, wraplength=290, justify="left",
                             font=("Consolas", 10))
        self.task.pack(anchor="w", padx=12)

        self.callout = tk.Label(p, bg=PANEL_BG, fg=WARN, wraplength=290, justify="left",
                                font=("Consolas", 9, "italic"))
        self.callout.pack(anchor="w", padx=12, pady=(8, 0))

        # Tiny quit affordance (borderless windows have no close button).
        tk.Button(p, text="x", command=self.root.destroy, bg=PANEL_BG, fg=DIM,
                  bd=0, activebackground=PANEL_BG, activeforeground=ACCENT,
                  font=("Consolas", 10, "bold")).place(relx=1.0, x=-6, y=6, anchor="ne")

    def _bind_quit(self) -> None:
        self.root.bind("<Escape>", lambda _e: self.root.destroy())
        # Let the user drag the borderless panel around to test placement.
        self.panel.bind("<Button-1>", self._start_drag)
        self.panel.bind("<B1-Motion>", self._on_drag)
        self._drag = (0, 0)

    def _start_drag(self, e: tk.Event) -> None:
        self._drag = (e.x, e.y)

    def _on_drag(self, e: tk.Event) -> None:
        dx, dy = e.x - self._drag[0], e.y - self._drag[1]
        self.root.geometry(f"+{self.root.winfo_x() + dx}+{self.root.winfo_y() + dy}")

    def _optional_full_click_through(self) -> None:
        """OPTIONAL: make the ENTIRE window click-through (even the opaque panel).

        The color-key already makes the TRANSPARENT area click-through. If the real HUD
        should be purely glanceable (never eat a click even on the panel), set the
        WS_EX_TRANSPARENT extended style via ctypes. Left OFF here so the PoC stays draggable
        for placement testing; documented so the real feature can flip it behind a setting.
        """
        return
        # Reference implementation (Windows), intentionally unreachable in the PoC:
        # import ctypes
        # GWL_EXSTYLE, WS_EX_LAYERED, WS_EX_TRANSPARENT = -20, 0x80000, 0x20
        # hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
        # ex = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        # ctypes.windll.user32.SetWindowLongW(
        #     hwnd, GWL_EXSTYLE, ex | WS_EX_LAYERED | WS_EX_TRANSPARENT)

    # -- loop ----------------------------------------------------------------
    def _refresh(self) -> None:
        _simulate(self.model)
        m = self.model
        self.loc.config(text=f"{m.system}\n{m.station}")
        self.status.config(text=m.status, fg=(WARN if m.in_danger else OK))
        bar = "#" * (m.fuel_pct // 10) + "-" * (10 - m.fuel_pct // 10)
        self.fuel.config(text=f"FUEL [{bar}] {m.fuel_pct}%",
                         fg=(WARN if m.fuel_pct <= 25 else TEXT))
        self.task.config(text=m.active_task)
        self.callout.config(text=f'"{m.last_callout}"')
        self.root.after(1000, self._refresh)  # 1 Hz is plenty for a glanceable panel

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    if not sys.platform.startswith("win"):
        print("NOTE: color-key transparency/click-through is Windows-only; "
              "on this OS you get an opaque window (still runnable).")
    HudOverlay().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
