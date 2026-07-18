"""Engineering blueprint library — bundled recipes + material sourcing, journal-grounded (#66).

Loads the two offline tables under `data/` (see `data/regen_engineering_data.py` for how they
are regenerated from EDCD/coriolis-data + EDCD/FDevIDs) and turns them into answers to
"what do I need for a grade-5 FSD, and what am I short on?":

  * `resolve()`   — fuzzy-match a spoken blueprint request ("grade 5 FSD", "dirty drives",
                    "increased range") to the real blueprint(s), honestly returning several when
                    the request only names a module (so the caller disambiguates, never guesses).
  * `line_items()`— a blueprint grade's recipe crossed with the Commander's live material
                    inventory (a `MaterialsSnapshot`): per material, how many are NEEDED vs HELD,
                    so "missing" is computed, never invented.
  * `MaterialInfo.source` — where to source a material (trader group + evergreen farm hint).
  * `resolve_material()` / `materials_by_category()` — the material-side lookups (#132) behind the
                    DIRECT "how many X do I have" / bucket-listing queries in
                    `capabilities/materials_capability.py`, reusing this same catalogue.
  * `cap_for_grade()` — the fixed ED grade->storage-cap table (300/250/200/150/100), used to say
                    what a Commander is capped or close to capped on.

Pure + offline: the JSON is read once and cached; nothing here touches the network. Everything
spoken is derived from these tables plus the journal's own counts.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .materials import MaterialsSnapshot

_DATA = Path(__file__).resolve().parent / "data"

# Noise words to drop from a spoken blueprint request before token matching. "grade"/"g5" etc.
# are parsed out separately (they select the grade, not the blueprint).
_STOP = {"grade", "the", "a", "an", "my", "for", "to", "of", "on", "blueprint", "blueprints",
         "engineering", "engineer", "mod", "mods", "modification", "upgrade", "upgrades",
         "level", "tier", "please", "get", "need", "want", "make", "roll", "rolls"}
_GRADE_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
_TOKEN_RE = re.compile(r"[a-z0-9]+")

# ED's storage cap per material grade — the SAME table for Raw / Manufactured / Encoded (#132).
# Not part of the bundled JSON (it's a fixed game rule, not per-material data), so it lives here
# next to the other engineering-domain constants rather than as a second material table.
GRADE_CAPS: dict[int, int] = {1: 300, 2: 250, 3: 200, 4: 150, 5: 100}


def cap_for_grade(grade: int) -> int | None:
    """The storage cap ED enforces for `grade` (1-5), or None when the grade is unknown/0 — a
    cap is never invented for a material we don't have grade data for."""
    try:
        g = int(grade)
    except (TypeError, ValueError):
        return None
    return GRADE_CAPS.get(g)


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(str(text).lower()))


@dataclass(frozen=True)
class MaterialInfo:
    """One engineering material: its journal symbol, spoken name, category (Raw / Manufactured /
    Encoded), grade (1-5), and a sourcing hint (trader group + farm method)."""
    symbol: str
    name: str
    category: str
    grade: int
    source: str


@dataclass(frozen=True)
class LineItem:
    """One material line of a recipe crossed with inventory: how many are NEEDED for the roll and
    how many are HELD. `short` is the shortfall (0 when you have enough)."""
    info: MaterialInfo
    need: int
    have: int

    @property
    def short(self) -> int:
        return max(0, self.need - self.have)

    @property
    def missing(self) -> bool:
        return self.have < self.need


@dataclass(frozen=True)
class Blueprint:
    """A blueprint's identity and per-grade recipe. `grades` maps the grade string ("1".."5") to
    a tuple of `(material symbol, count)` pairs — the materials consumed per craft roll."""
    key: str
    name: str
    module: str
    aliases: tuple[str, ...]
    grades: dict[str, tuple[tuple[str, int], ...]]

    @property
    def max_grade(self) -> int:
        return max((int(g) for g in self.grades), default=0)

    def recipe(self, grade: int) -> tuple[tuple[str, int], ...]:
        """The `(symbol, count)` recipe for `grade`. Blueprints occasionally define grades
        non-contiguously, so if the exact grade is absent we fall back to the nearest DEFINED
        grade — preferring a lower one, else the lowest higher one. Empty only if the blueprint
        has no recipe at all."""
        exact = self.grades.get(str(int(grade)))
        if exact:
            return exact
        defined = sorted(int(g) for g in self.grades)
        if not defined:
            return ()
        lower = [g for g in defined if g <= grade]
        pick = lower[-1] if lower else defined[0]
        return self.grades[str(pick)]


class BlueprintLibrary:
    """Resolves spoken blueprint requests and computes material shortfalls against the bundled
    tables. Construct with `from_bundled()` (cached) in the app; tests can pass their own dicts."""

    def __init__(self, blueprints: dict, materials: dict) -> None:
        self._materials: dict[str, MaterialInfo] = {
            sym: MaterialInfo(symbol=sym, name=m.get("name", sym),
                              category=m.get("category", ""), grade=int(m.get("grade", 0) or 0),
                              source=m.get("source", ""))
            for sym, m in materials.items()
        }
        self._blueprints: dict[str, Blueprint] = {}
        for key, b in blueprints.items():
            grades = {
                g: tuple((str(c["m"]), int(c["n"])) for c in comps)
                for g, comps in (b.get("grades") or {}).items()
            }
            self._blueprints[key] = Blueprint(
                key=key, name=str(b.get("name") or key),
                module=str(b.get("module") or ""),
                aliases=tuple(b.get("aliases") or ()),
                grades=grades)

    @classmethod
    def from_bundled(cls) -> "BlueprintLibrary":
        return _bundled_library()

    # -- lookups ------------------------------------------------------------------------
    def material(self, symbol: str) -> MaterialInfo | None:
        return self._materials.get(str(symbol).strip().lower())

    def blueprint(self, key: str) -> Blueprint | None:
        return self._blueprints.get(key)

    def all_materials(self) -> list[MaterialInfo]:
        """Every known material, unordered — the bundled catalogue's own entries, nothing added."""
        return list(self._materials.values())

    def materials_by_category(self, category: str) -> list[MaterialInfo]:
        """Every known material in `category` ("Raw"/"Manufactured"/"Encoded", case-insensitive),
        sorted by grade then name. The bucket comes straight from the bundled table (#132) — never
        a second, hand-maintained material list."""
        want = str(category or "").strip().lower()
        out = [m for m in self._materials.values() if m.category.strip().lower() == want]
        return sorted(out, key=lambda m: (m.grade, m.name))

    def resolve_material_scored(self, query: str) -> list[tuple[int, MaterialInfo]]:
        """Rank materials matching a spoken request as `(score, MaterialInfo)`, best first —
        the material-side twin of `resolve_scored` (#132). Materials don't carry aliases, so this
        matches on the material's spoken NAME and its journal symbol. Empty list means no match."""
        q = _tokens(query) - _STOP
        if not q:
            return []
        scored: list[tuple[int, int, MaterialInfo]] = []
        for i, info in enumerate(self._materials.values()):
            name_tok = _tokens(info.name)
            symbol_tok = _tokens(info.symbol)
            name_hits = len(q & name_tok)
            exact_name = 40 if name_tok and name_tok <= q else 0
            exact_symbol = 30 if symbol_tok and symbol_tok <= q else 0
            score = exact_name + exact_symbol + name_hits * 10
            if score > 0:
                scored.append((-score, i, info))
        scored.sort(key=lambda t: (t[0], t[1]))
        return [(-neg, info) for neg, _i, info in scored]

    def resolve_material(self, query: str) -> MaterialInfo | None:
        """The single best material match for a spoken request, or None (see
        `resolve_material_scored`)."""
        scored = self.resolve_material_scored(query)
        return scored[0][1] if scored else None

    def resolve_scored(self, query: str) -> list[tuple[int, Blueprint]]:
        """Rank blueprints matching a spoken request as `(score, blueprint)`, best first. A
        blueprint whose NAME the request spells out scores highest; a request that only names a
        MODULE (e.g. "FSD") ties every blueprint for that module, so the caller can detect the
        ambiguity (equal top scores) and list them instead of guessing one. Grade words are
        ignored here (see `parse_grade`). Empty list means no match."""
        q = _tokens(query) - _STOP
        if not q:
            return []
        scored: list[tuple[int, int, Blueprint]] = []
        for i, bp in enumerate(self._blueprints.values()):
            name_tok = _tokens(bp.name)
            alias_tok = _tokens(" ".join(bp.aliases))
            name_hits = len(q & name_tok)
            # Query tokens that ARE module alias tokens (e.g. "fsd", "fuel", "scoop") are a strong
            # module signal — they lock the module even when the blueprint name isn't spoken. More
            # matched alias tokens = a tighter module lock ("fuel scoop" beats "fuel transfer").
            module_lock = len(q & alias_tok) * 15
            exact_name = 40 if name_tok and name_tok <= q else 0
            score = exact_name + name_hits * 10 + module_lock
            if score > 0:
                scored.append((-score, i, bp))
        scored.sort(key=lambda t: (t[0], t[1]))
        return [(-neg, bp) for neg, _i, bp in scored]

    def resolve(self, query: str) -> list[Blueprint]:
        """The ranked blueprints matching a request, best first (see `resolve_scored`)."""
        return [bp for _s, bp in self.resolve_scored(query)]

    def blueprints_for_module(self, query: str) -> list[Blueprint]:
        """Every blueprint whose module a request names (e.g. "FSD", "thrusters"), for a
        "what blueprints can I engineer on my X" listing. Matched on module aliases only."""
        q = _tokens(query) - _STOP
        if not q:
            return []
        out = [bp for bp in self._blueprints.values() if q & _tokens(" ".join(bp.aliases))]
        return sorted(out, key=lambda b: b.name)

    def line_items(self, bp: Blueprint, grade: int,
                   snapshot: MaterialsSnapshot | None) -> list[LineItem]:
        """Every material in `bp`'s recipe for `grade`, crossed with the inventory `snapshot`
        (None = nothing known yet, so everything reads as fully missing). Order follows the
        recipe. The caller filters `.missing` for the shortfall."""
        out: list[LineItem] = []
        for sym, count in bp.recipe(grade):
            info = self.material(sym) or MaterialInfo(symbol=sym, name=sym.title(),
                                                      category="", grade=0, source="")
            have = snapshot.count(sym) if snapshot is not None else 0
            out.append(LineItem(info=info, need=count, have=have))
        return out


def parse_grade(query: str, default: int = 5) -> int:
    """The grade a request asks for: a digit 1-5 ("grade 5", "g3", "5"), or a spelled number
    ("grade five"), clamped to 1-5. Falls back to `default` when none is spoken."""
    text = str(query).lower()
    m = re.search(r"\b(?:grade|g|tier|level)\s*([1-5])\b", text) or re.search(r"\b([1-5])\b", text)
    if m:
        return int(m.group(1))
    for word, val in _GRADE_WORDS.items():
        if re.search(rf"\b{word}\b", text):
            return val
    return default


@lru_cache(maxsize=1)
def _bundled_library() -> BlueprintLibrary:
    blueprints = json.loads((_DATA / "blueprints.json").read_text(encoding="utf-8"))
    materials = json.loads((_DATA / "materials.json").read_text(encoding="utf-8"))
    return BlueprintLibrary(blueprints, materials)
