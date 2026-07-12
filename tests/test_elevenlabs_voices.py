"""Unit tests for the voice-list filtering that hides ElevenLabs 'famous' voices.

Famous voices (John Wayne™, Michael Caine™, …) are ElevenReader-only: the TTS API
returns 401 famous_voice_not_permitted, so they must never reach the picker or any
random/atmospheric pool. Detection is the permission flag `sharing.category == 'famous'`,
NOT the ™ glyph or the top-level 'professional' category (which also covers usable voices).
"""
from __future__ import annotations

import types

from covas import elevenlabs as el


def _v(name, *, sharing_category=None, category="premade"):
    v = {"voice_id": name.lower().replace(" ", "_"), "name": name, "category": category}
    if sharing_category is not None:
        v["sharing"] = {"category": sharing_category}
    return v


def test_is_famous_true_only_for_sharing_category_famous():
    assert el.is_famous(_v("John Wayne™", sharing_category="famous", category="professional"))


def test_is_famous_false_for_professional_non_famous():
    # 'professional' is NOT sufficient — many real, usable voices are professional.
    assert not el.is_famous(_v("Kenneth - American Storyteller", category="professional"))


def test_is_famous_false_when_sharing_missing_or_none():
    assert not el.is_famous(_v("Sarah"))                       # no 'sharing' key
    assert not el.is_famous({"voice_id": "x", "name": "x", "sharing": None})


def test_list_voices_filters_out_famous(monkeypatch):
    roster = [
        _v("Sarah"),                                                   # premade -> keep
        _v("Kenneth - American Storyteller", category="professional"), # pro but not famous -> keep
        _v("John Wayne™", sharing_category="famous", category="professional"),   # drop
        _v("Sir Michael Caine™", sharing_category="famous", category="professional"),  # drop
        _v("George", sharing_category="high_quality"),                 # shared but not famous -> keep
    ]

    fake_resp = types.SimpleNamespace(
        json=lambda: {"voices": roster},
        raise_for_status=lambda: None,
    )
    monkeypatch.setattr(el, "_key", lambda cfg: "test-key")
    monkeypatch.setattr(el.requests, "get", lambda *a, **k: fake_resp)

    cfg = {"elevenlabs": {"api_key_file": "unused"}}
    names = {v["name"] for v in el.list_voices(cfg)}

    assert names == {"Sarah", "Kenneth - American Storyteller", "George"}
    assert not any("™" in n for n in names)
