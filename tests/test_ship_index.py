"""Unit tests for ShipIndex — the live-roster reconciliation (offline, DESIGN §9).

The fetch is injected, so these never touch the network. They lock in the contract the app
relies on: `extra_names()` is exactly the hulls Spansh knows that the bundle doesn't, the index
is fail-soft (a fetch error leaves the bundled roster in charge), and it's cached.
"""
from __future__ import annotations

from covas.nav.ship_index import ShipIndex
from covas.nav.ships import SHIP_NAMES


def test_extra_names_are_only_hulls_missing_from_the_bundle():
    live = list(SHIP_NAMES) + ["Cobra MkVI", "Fictional Destroyer"]
    idx = ShipIndex(fetch=lambda: live)
    assert set(idx.extra_names()) == {"Cobra MkVI", "Fictional Destroyer"}
    assert idx.loaded


def test_no_new_hulls_means_no_extras():
    idx = ShipIndex(fetch=lambda: list(SHIP_NAMES))
    assert idx.extra_names() == ()
    assert idx.loaded


def test_fetch_failure_is_fail_soft():
    def boom():
        raise ConnectionError("spansh down")
    idx = ShipIndex(fetch=boom)
    assert idx.extra_names() == ()          # no crash — bundle stays in charge
    assert idx.loaded is False


def test_empty_fetch_is_not_loaded():
    idx = ShipIndex(fetch=lambda: [])
    assert idx.loaded is False
    assert idx.extra_names() == ()


def test_fetch_is_cached_after_first_access():
    calls = {"n": 0}

    def counting():
        calls["n"] += 1
        return list(SHIP_NAMES) + ["Cobra MkVI"]

    idx = ShipIndex(fetch=counting)
    idx.refresh()
    _ = idx.extra_names()
    _ = idx.loaded
    assert calls["n"] == 1                   # fetched once, then served from cache
