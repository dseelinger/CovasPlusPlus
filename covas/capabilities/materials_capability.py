"""Materials-inventory query capability (#132) — "how many Chemical Manipulators do I have",
"list my raw materials", "what am I capped on".

The data already exists: the journal `Materials` event (nudged by Collected/Discarded deltas)
is parsed into a `MaterialsSnapshot` and held live on `EDContext` (`covas/ed/materials.py`).
Before this capability the ONLY way to reach it was through `BlueprintCapability`'s recipe
cross-reference — there was no DIRECT count/listing/cap query. This capability adds that direct
surface, reusing the SAME injected `get_materials` getter and the SAME bundled engineering tables
(`ed/blueprints.py`'s `BlueprintLibrary` owns material naming/categorization/grade; this module
never hardcodes a second material table) — it only adds the query shapes, not new data.

Grade caps (300/250/200/150/100 for G1..G5, `ed/blueprints.py.cap_for_grade`) are a fixed ED game
rule, so "capped" / "near cap" is computed from grade, never invented per-material. Every number
spoken comes from `MaterialsSnapshot.count()` or the bundled tables — nothing here guesses.
Fail soft: no snapshot yet (before the game's first `Materials` event) gets an honest "I haven't
read your materials yet" rather than a fabricated zero.
"""
from __future__ import annotations

from typing import Callable

from ..ed.blueprints import BlueprintLibrary, MaterialInfo, cap_for_grade
from ..ed.materials import MaterialsSnapshot
from .base import HelpMeta, Slot

_COUNT_TOOL = "material_count"
_LIST_TOOL = "list_materials"
_CAPPED_TOOL = "materials_capped"

# Near-cap threshold: close enough to the grade cap that farming more is close to wasted (issue
# #132 asks for "what am I capped on", which reads naturally as "at or near the cap").
_NEAR_CAP_RATIO = 0.9

# How many materials to name in a spoken listing before trimming (#132: "don't recite 100
# symbols"). The rest are summarized as a count.
_LIST_LIMIT = 8

_NO_MATERIALS = ("I haven't read your materials yet — Elite writes them when you load in, so "
                 "board your ship or restart the game and I'll pick your inventory up.")

_BUCKETS = ("Raw", "Manufactured", "Encoded")


def _nice(text: str) -> str:
    """Title-case for speech, preserving acronyms already in caps."""
    return " ".join(w if w.isupper() else w.capitalize() for w in str(text).split())


def _resolve_bucket(text: str) -> str | None:
    """A spoken bucket word ("raw", "raw materials", "data", "encoded") -> the canonical journal
    bucket name, or None when nothing matches (so the caller can ask rather than guess)."""
    t = str(text or "").strip().lower()
    if not t:
        return None
    if "raw" in t:
        return "Raw"
    if "manufactur" in t:
        return "Manufactured"
    if "encod" in t or "data" in t:
        return "Encoded"
    return None


class MaterialsCapability:
    """Advertises direct materials-inventory queries, reading the same injected materials getter
    and bundled engineering library as `BlueprintCapability` (#66) — a separate small capability
    (single responsibility: reads-only, no recipe crossing) rather than growing that one further."""
    # Same tiering group as BlueprintCapability (#84): both read the live materials inventory and
    # the bundled engineering tables, so they cost/gate together.
    TIERING_GROUP = "engineering"

    def __init__(self, *, get_materials: Callable[[], MaterialsSnapshot | None],
                 library: BlueprintLibrary | None = None,
                 log: Callable[[str], None] | None = None) -> None:
        self._get_materials = get_materials
        self._lib = library or BlueprintLibrary.from_bundled()
        self._log = log

    # -- capability interface ---------------------------------------------------------
    def tools(self) -> list[dict]:
        return [
            {
                "name": _COUNT_TOOL,
                "description": (
                    "How many of ONE named engineering material the Commander is carrying right "
                    "now ('how many Chemical Manipulators do I have', 'do I have any arsenic'), "
                    "read straight from the live journal inventory — never invented. Fuzzy-matches "
                    "the spoken name to the real material."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "material": {
                            "type": "string",
                            "description": "The material as spoken, e.g. 'chemical manipulators', "
                                           "'arsenic', 'datamined wake exceptions'.",
                        },
                    },
                    "required": ["material"],
                },
            },
            {
                "name": _LIST_TOOL,
                "description": (
                    "List the materials the Commander is holding in ONE bucket — raw, "
                    "manufactured, or encoded ('list my raw materials', 'what manufactured "
                    "materials do I have') — from the live journal inventory. Only materials "
                    "actually held (count > 0) are listed, trimmed to the most notable ones; set "
                    "`near_cap_only` true for 'what raw materials am I full/near-full on'."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "bucket": {
                            "type": "string",
                            "description": "Which bucket: 'raw', 'manufactured', or 'encoded'.",
                        },
                        "near_cap_only": {
                            "type": "boolean",
                            "description": "True to list only materials at or near their grade "
                                           "cap in this bucket. Defaults to false (list everything "
                                           "held).",
                        },
                    },
                    "required": ["bucket"],
                },
            },
            {
                "name": _CAPPED_TOOL,
                "description": (
                    "What the Commander is capped (or close to capped) on, across ALL material "
                    "buckets by default ('what am I capped on', 'am I full on anything') — grade "
                    "caps are a fixed game rule (300/250/200/150/100 for grade 1-5), computed "
                    "against the live journal inventory. Optional `bucket` narrows to just raw, "
                    "manufactured, or encoded."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "bucket": {
                            "type": "string",
                            "description": "Optional: narrow to 'raw', 'manufactured', or "
                                           "'encoded'. Omit to check everything.",
                        },
                    },
                    "required": [],
                },
            },
        ]

    def help_meta(self) -> HelpMeta:
        return HelpMeta(
            category="materials inventory",
            group="your ship",
            one_liner=("I read your live engineering-materials inventory straight from the "
                       "journal — how many of one you're holding, a bucket listing, or what "
                       "you're capped on."),
            example="how many chemical manipulators do I have",
            slots=(
                Slot(param="material",
                     phrasings=("a material name", "chemical manipulators, arsenic, wake echoes"),
                     example="how many arsenic do I have",
                     help_text="Name the material — 'arsenic', 'chemical manipulators', "
                               "'datamined wake exceptions'."),
                Slot(param="bucket",
                     phrasings=("a bucket", "raw, manufactured, or encoded"),
                     example="list my raw materials",
                     help_text="Say the bucket — raw, manufactured, or encoded."),
            ),
            help_when_active=("Ask how many of a material you have, list a bucket, or ask what "
                              "you're capped on — all read from your live inventory."),
        )

    def run_tool(self, name: str, inp: dict) -> str:
        try:
            if name == _COUNT_TOOL:
                return self._material_count(inp)
            if name == _LIST_TOOL:
                return self._list_materials(inp)
            if name == _CAPPED_TOOL:
                return self._materials_capped(inp)
            return f"Unknown tool: {name}"
        except Exception as e:  # noqa: BLE001 — the voice loop must survive any tool error
            self._logline(f"error: {e}")
            return f"Materials lookup error: {e}"

    # -- how many of X -------------------------------------------------------------------
    def _material_count(self, inp: dict) -> str:
        query = str(inp.get("material") or "").strip()
        if not query:
            return "Which material? Name one, like 'arsenic' or 'chemical manipulators'."
        info = self._lib.resolve_material(query)
        if info is None:
            return (f"I don't recognize '{query}' as a material. Try naming it like 'chemical "
                    "manipulators' or 'arsenic'.")
        snap = self._snapshot()
        if snap is None:
            return _NO_MATERIALS
        count = snap.count(info.symbol)
        cap = cap_for_grade(info.grade)
        bucket = info.category.lower() or "engineering"
        if cap:
            if count >= cap:
                return f"You have {count} {_nice(info.name)} — capped at {cap} ({bucket})."
            if count >= cap * _NEAR_CAP_RATIO:
                return (f"You have {count} {_nice(info.name)} ({bucket}, grade {info.grade}) — "
                        f"close to the {cap} cap.")
            return f"You have {count} {_nice(info.name)} ({bucket}, grade {info.grade}; cap {cap})."
        return f"You have {count} {_nice(info.name)} ({bucket})."

    # -- list a bucket --------------------------------------------------------------------
    def _list_materials(self, inp: dict) -> str:
        bucket = _resolve_bucket(inp.get("bucket"))
        if bucket is None:
            return "Which bucket? Say 'raw', 'manufactured', or 'encoded'."
        near_cap_only = bool(inp.get("near_cap_only"))
        snap = self._snapshot()
        if snap is None:
            return _NO_MATERIALS

        held = self._held_in_bucket(bucket, snap)
        if near_cap_only:
            held = [row for row in held if row[3]]  # row[3] = near/at cap flag
        if not held:
            qualifier = "near-cap " if near_cap_only else ""
            return f"You're not holding any {qualifier}{bucket.lower()} materials right now."

        held.sort(key=lambda row: row[2], reverse=True)  # row[2] = fraction of cap (or count)
        shown = held[:_LIST_LIMIT]
        parts = [self._line(info, count) for info, count, _frac, _near in shown]
        body = ", ".join(parts)
        extra = len(held) - len(shown)
        tail = f" (+{extra} more)" if extra > 0 else ""
        return f"Your {bucket.lower()} materials: {body}{tail}."

    # -- what am I capped on ---------------------------------------------------------------
    def _materials_capped(self, inp: dict) -> str:
        wanted_bucket = _resolve_bucket(inp.get("bucket")) if inp.get("bucket") else None
        snap = self._snapshot()
        if snap is None:
            return _NO_MATERIALS

        buckets = [wanted_bucket] if wanted_bucket else list(_BUCKETS)
        at_cap: list[tuple[MaterialInfo, int, int]] = []
        near_cap: list[tuple[MaterialInfo, int, int]] = []
        for bucket in buckets:
            for info, count, frac, near in self._held_in_bucket(bucket, snap):
                cap = cap_for_grade(info.grade)
                if cap is None:
                    continue
                if count >= cap:
                    at_cap.append((info, count, cap))
                elif near:
                    near_cap.append((info, count, cap))

        if not at_cap and not near_cap:
            scope = wanted_bucket.lower() if wanted_bucket else "anything"
            return f"You're not capped or close to capped on {scope} right now."

        segments = []
        if at_cap:
            at_cap.sort(key=lambda t: t[0].name)
            names = ", ".join(f"{_nice(i.name)} ({c}/{cap})" for i, c, cap in at_cap[:_LIST_LIMIT])
            segments.append(f"Capped: {names}")
        if near_cap:
            near_cap.sort(key=lambda t: t[1] / t[2], reverse=True)
            names = ", ".join(f"{_nice(i.name)} ({c}/{cap})" for i, c, cap in near_cap[:_LIST_LIMIT])
            segments.append(f"Close to capped: {names}")
        return ". ".join(segments) + "."

    # -- helpers ----------------------------------------------------------------------------
    def _held_in_bucket(self, bucket: str, snap: MaterialsSnapshot
                        ) -> list[tuple[MaterialInfo, int, float, bool]]:
        """`(MaterialInfo, count, fraction-of-cap, near-or-at-cap)` for every material actually
        held (count > 0) in `bucket`, per the bundled categorization — never a hand-rolled list."""
        out: list[tuple[MaterialInfo, int, float, bool]] = []
        for info in self._lib.materials_by_category(bucket):
            count = snap.count(info.symbol)
            if count <= 0:
                continue
            cap = cap_for_grade(info.grade)
            frac = (count / cap) if cap else float(count)
            near = bool(cap) and count >= cap * _NEAR_CAP_RATIO
            out.append((info, count, frac, near))
        return out

    def _line(self, info: MaterialInfo, count: int) -> str:
        cap = cap_for_grade(info.grade)
        if cap:
            return f"{_nice(info.name)} {count}/{cap}"
        return f"{_nice(info.name)} {count}"

    def _snapshot(self) -> MaterialsSnapshot | None:
        snap = self._get_materials()
        return snap if isinstance(snap, MaterialsSnapshot) else None

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)
