"""Unit tests for the bundled outfitting taxonomy + resolve() (offline, DESIGN §9).

resolve() is a pure function — no network, no state — so these are fast and deterministic.
Covers the four outcomes: Resolved (exact + misheard), NeedAttrs (missing size/mount),
Ambiguous, and Unknown, plus the ED size-word / mount-word mapping.
"""
from __future__ import annotations

from covas.nav.modules import Ambiguous, NeedAttrs, Resolved, Unknown, resolve

# --- exact + misheard resolution -----------------------------------------------------------

def test_exact_name_with_attrs_resolves():
    r = resolve("Multi-Cannon", "medium", "gimballed")
    assert isinstance(r, Resolved)
    assert r.name == "Multi-Cannon"          # exact Spansh filter name preserved
    assert r.size == 2 and r.mount == "Gimbal"
    assert "medium" in r.label and "gimballed" in r.label


def test_misheard_name_maps_via_alias():
    # "multiple cannon" is a classic Whisper mishearing of "multicannon".
    r = resolve("multiple cannon", "small", "fixed")
    assert isinstance(r, Resolved)
    assert r.name == "Multi-Cannon" and r.size == 1 and r.mount == "Fixed"


def test_hyphen_and_spacing_normalized():
    for q in ("multicannon", "multi cannon", "multi-cannon", "MULTI  CANNON"):
        r = resolve(q, "large", "turreted")
        assert isinstance(r, Resolved) and r.name == "Multi-Cannon"
        assert r.size == 3 and r.mount == "Turret"


def test_size_accepts_class_number_for_internals():
    r = resolve("frame shift drive", "5")
    assert isinstance(r, Resolved)
    assert r.name == "Frame Shift Drive" and r.size == 5
    assert "class 5" in r.label


def test_size_tolerates_class_prefix():
    r = resolve("fuel scoop", "class 6")
    assert isinstance(r, Resolved) and r.size == 6


# --- NeedAttrs: missing / invalid attributes -----------------------------------------------

def test_weapon_missing_both_size_and_mount():
    r = resolve("multi-cannon")
    assert isinstance(r, NeedAttrs)
    assert r.missing == ["size", "mount"]
    assert r.options["size"] == ["small", "medium", "large", "huge"]
    assert r.options["mount"] == ["fixed", "gimballed", "turreted"]


def test_weapon_missing_only_mount():
    r = resolve("multi-cannon", "medium")
    assert isinstance(r, NeedAttrs)
    assert r.missing == ["mount"] and "size" not in r.options


def test_internal_missing_size_uses_class_options():
    r = resolve("fuel scoop")
    assert isinstance(r, NeedAttrs)
    assert r.missing == ["size"]
    assert r.options["size"][0] == "class 1"


def test_invalid_size_is_treated_as_missing():
    # rail gun only comes in small/medium — "huge" isn't valid, so ask again.
    r = resolve("rail gun", "huge")
    assert isinstance(r, NeedAttrs)
    assert r.missing == ["size"]
    assert r.options["size"] == ["small", "medium"]


# --- single-option attributes are filled, not asked ----------------------------------------

def test_single_mount_weapon_only_asks_size():
    # Plasma Accelerator is Fixed-only -> mount is determined, not a guess.
    r = resolve("plasma accelerator")
    assert isinstance(r, NeedAttrs) and r.missing == ["size"]


def test_single_mount_weapon_resolves_with_size_only():
    r = resolve("plasma accelerator", "large")
    assert isinstance(r, Resolved)
    assert r.mount == "Fixed" and r.size == 3


def test_utility_has_no_size_or_mount():
    r = resolve("shield booster")
    assert isinstance(r, Resolved)
    assert r.size is None and r.mount is None
    assert r.label == "Shield Booster"


# --- Ambiguous + Unknown -------------------------------------------------------------------

def test_ambiguous_bare_laser():
    r = resolve("laser")
    assert isinstance(r, Ambiguous)
    assert "Beam Laser" in r.candidates and "Pulse Laser" in r.candidates


def test_ambiguous_limpet_family():
    r = resolve("limpet")
    assert isinstance(r, Ambiguous)
    assert any("Limpet Controller" in c for c in r.candidates)


def test_unknown_returns_suggestions():
    r = resolve("flux capacitor")
    assert isinstance(r, Unknown)
    assert r.query == "flux capacitor"
    assert r.suggestions  # a few common modules offered


def test_empty_query_is_unknown():
    assert isinstance(resolve(""), Unknown)
