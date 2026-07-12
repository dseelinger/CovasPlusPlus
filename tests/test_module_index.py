"""Unit tests for ModuleIndex — the live-taxonomy reconciliation (offline, DESIGN §9).

The mirror of test_ship_index. The fetch is injected, so these never touch the network. They
lock in the contract the app relies on: `extra_names()` is exactly the modules Spansh knows that
the bundle doesn't, the index is fail-soft (a fetch error leaves the bundled taxonomy in charge),
and it's cached.
"""
from __future__ import annotations

from covas.nav.module_index import ModuleIndex
from covas.nav.modules import MODULE_NAMES


def test_extra_names_are_only_modules_missing_from_the_bundle():
    live = list(MODULE_NAMES) + ["Neutron Pulse Cannon", "Fictional Reactor"]
    idx = ModuleIndex(fetch=lambda: live)
    assert set(idx.extra_names()) == {"Neutron Pulse Cannon", "Fictional Reactor"}
    assert idx.loaded


def test_no_new_modules_means_no_extras():
    idx = ModuleIndex(fetch=lambda: list(MODULE_NAMES))
    assert idx.extra_names() == ()
    assert idx.loaded


def test_fetch_failure_is_fail_soft():
    def boom():
        raise ConnectionError("spansh down")
    idx = ModuleIndex(fetch=boom)
    assert idx.extra_names() == ()          # no crash — bundle stays in charge
    assert idx.loaded is False


def test_empty_fetch_is_not_loaded():
    idx = ModuleIndex(fetch=lambda: [])
    assert idx.loaded is False
    assert idx.extra_names() == ()


def test_fetch_is_cached_after_first_access():
    calls = {"n": 0}

    def counting():
        calls["n"] += 1
        return list(MODULE_NAMES) + ["Neutron Pulse Cannon"]

    idx = ModuleIndex(fetch=counting)
    idx.refresh()
    _ = idx.extra_names()
    _ = idx.loaded
    assert calls["n"] == 1                   # fetched once, then served from cache
