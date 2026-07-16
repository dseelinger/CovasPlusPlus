"""Grounded ship-specification lookup (issue #83) — real numbers, not training-cutoff guesses.

The `ship_spec` tool's data layer. `ships.py`/`ship_index.py` already answer *which* hull the
Commander means (offline canonical resolution + learned new names); this module answers *what
that hull is* from a bundled, refreshable spec table (`ship_spec_data.py`, baked from the
maintained EDCD/coriolis-data — see that file's header and `scripts/gen_ship_specs.py`).

Keyed by the SAME canonical id `resolve_ship()` returns, so the capability just resolves a
name then looks the spec straight up. Everything here is pure and offline: a `Spec` frozen
dataclass, `get_spec(id)`, and spoken-summary helpers. A hull the table doesn't cover (the
Lynx Highliner, or a brand-new name learned live from Spansh) returns None — the capability
then says so plainly and offers web search, rather than letting the model invent numbers.

Deliberately NO jump range: unlike hull mass or slot layout it is not a hull constant but a
function of the fitted FSD, mass and fuel — so for the Commander's OWN ship the loadout tool
has the real figure, and for any ship the guardrail steers to web search rather than a guess.
"""
from __future__ import annotations

from dataclasses import dataclass

from .ship_spec_data import SHIP_SPECS

_PAD_WORD = {1: "small", 2: "medium", 3: "large"}
_SIZE_WORD = {1: "small", 2: "medium", 3: "large", 4: "huge"}
# The fixed order of the 7 core-internal sizes in `Spec.core` (see ship_spec_data header).
_CORE_LABELS = ("power plant", "thrusters", "frame shift drive", "life support",
                "power distributor", "sensors", "fuel tank")


@dataclass(frozen=True)
class Spec:
    """One hull's bundled specification. Sizes are ED class numbers; tonnages are tonnes;
    speeds m/s; shields MJ. `core` is 7 sizes in `_CORE_LABELS` order; `hardpoints` the weapon
    mount sizes (1=S..4=H); `optional` is (size, kind) per optional internal, kind '' normal /
    'military' (no cargo) / 'cargo' (cargo/fuel only)."""
    id: str
    name: str
    manufacturer: str
    pad_size: int
    hull_mass: float
    fuel_capacity: int
    max_cargo: int
    crew: int
    top_speed: int
    boost_speed: int
    base_shield: int
    base_armour: int
    masslock: int
    core: tuple[int, ...]
    hardpoints: tuple[int, ...]
    utilities: int
    optional: tuple[tuple[int, str], ...]

    @property
    def pad(self) -> str:
        """Landing-pad word (small/medium/large)."""
        return _PAD_WORD.get(self.pad_size, "unknown")

    @property
    def fsd_size(self) -> int:
        """The stock frame-shift-drive slot size (index 2 of `core`) — the honest thing to say
        about 'jump range' without simulating a build."""
        return self.core[2] if len(self.core) > 2 else 0


def get_spec(ship_id: str) -> Spec | None:
    """The bundled `Spec` for a canonical ship id (as `resolve_ship()` returns), or None when
    the table doesn't cover that hull (e.g. a live-learned name with no bundled data)."""
    row = SHIP_SPECS.get(ship_id)
    if not row:
        return None
    return Spec(id=ship_id, **row)


def has_spec(ship_id: str) -> bool:
    return ship_id in SHIP_SPECS


# ---- spoken summaries (pure; the capability speaks these) ----------------------------------

def _count_sizes(sizes) -> str:
    """'2 large, 4 medium' — group weapon/mount sizes into a short spoken tally, largest first."""
    order = [4, 3, 2, 1]
    counts = {s: sum(1 for x in sizes if x == s) for s in order}
    parts = [f"{n} {_SIZE_WORD[s]}" for s in order if (n := counts[s])]
    return ", ".join(parts) if parts else "none"


def hardpoint_summary(spec: Spec) -> str:
    """'6 hardpoints (2 large, 4 medium) and 6 utility mounts', or the no-weapons case."""
    n = len(spec.hardpoints)
    hp = (f"{n} weapon hardpoint{'s' if n != 1 else ''} ({_count_sizes(spec.hardpoints)})"
          if n else "no weapon hardpoints")
    ut = (f"{spec.utilities} utility mount{'s' if spec.utilities != 1 else ''}"
          if spec.utilities else "no utility mounts")
    return f"{hp} and {ut}"


def optional_summary(spec: Spec) -> str:
    """'10 optional internal slots (largest size 6), 2 of them military' — the outfitting room,
    without pretending to know what's fitted."""
    if not spec.optional:
        return "no optional internal slots"
    n = len(spec.optional)
    largest = max(sz for sz, _ in spec.optional)
    military = sum(1 for _, kind in spec.optional if kind == "military")
    line = f"{n} optional internal slot{'s' if n != 1 else ''} (largest size {largest})"
    if military:
        line += f", {military} of them military"
    return line


def core_summary(spec: Spec) -> str:
    """'power plant 8, thrusters 8, FSD 7, …' — the core-internal sizes, in a fixed order."""
    return ", ".join(f"{label} {size}" for label, size in zip(_CORE_LABELS, spec.core))


def summary(spec: Spec) -> str:
    """A complete, spoken-friendly rundown. The tool returns this whole thing and the model
    relays the part the Commander actually asked for (mass, cargo, pad, hardpoints, …)."""
    return (
        f"{spec.name} — a {spec.pad}-pad ship by {spec.manufacturer}. "
        f"Hull mass {spec.hull_mass:g} tonnes; largest landing pad {spec.pad}. "
        f"{hardpoint_summary(spec).capitalize()}. "
        f"{optional_summary(spec).capitalize()}. "
        f"Core internals: {core_summary(spec)}. "
        f"Fuel tank {spec.fuel_capacity} tonnes; a class {spec.fsd_size} frame shift drive "
        f"(actual jump range depends on the fitted FSD and loadout). "
        f"Maximum cargo {spec.max_cargo} tonnes with every optional slot a cargo rack. "
        f"Seats {spec.crew}. Top speed {spec.top_speed} metres per second, boost "
        f"{spec.boost_speed}. Base shields {spec.base_shield}, base armour {spec.base_armour}."
    )
