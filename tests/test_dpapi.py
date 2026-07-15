"""Round-trip tests for the Windows DPAPI key wrapper (covas/dpapi.py, issue #22).

These hit the REAL crypt32 API, so they only run on Windows (`skipif` elsewhere). They prove the
encrypt/decrypt round-trip works, that a tampered blob fails closed rather than returning garbage,
and that the ``DPAPI:`` sentinel detection is correct. The hermetic/cross-platform coverage of the
firstrun read/write/migrate path lives in test_firstrun.py (which fakes protect/unprotect).
"""
from __future__ import annotations

import sys

import pytest

from covas import dpapi

win_only = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")


def test_is_available_matches_platform():
    assert dpapi.is_available() is (sys.platform == "win32")


def test_is_encrypted_detects_sentinel():
    assert dpapi.is_encrypted("DPAPI:abc") is True
    assert dpapi.is_encrypted("sk-ant-plaintext") is False
    assert dpapi.is_encrypted("") is False


@win_only
def test_round_trip_recovers_original():
    secret = "sk-ant-api03-Xy_Z-123456"
    blob = dpapi.protect(secret)
    assert blob.startswith("DPAPI:")
    assert secret not in blob                  # ciphertext, not the key in the clear
    assert dpapi.unprotect(blob) == secret


@win_only
def test_round_trip_handles_unicode_and_empty():
    for secret in ["", "clé-café-🔑", "a" * 4096]:
        assert dpapi.unprotect(dpapi.protect(secret)) == secret


@win_only
def test_each_encryption_is_salted_but_decrypts_the_same():
    # DPAPI salts each call, so two encryptions of the same key differ on disk yet both decrypt.
    a, b = dpapi.protect("same-key"), dpapi.protect("same-key")
    assert a != b
    assert dpapi.unprotect(a) == dpapi.unprotect(b) == "same-key"


@win_only
def test_tampered_blob_fails_closed():
    blob = dpapi.protect("secret")
    tampered = blob[:-4] + ("AAAA" if not blob.endswith("AAAA") else "BBBB")
    with pytest.raises(Exception):        # OSError from CryptUnprotectData (or a decode error)
        dpapi.unprotect(tampered)


@win_only
def test_unprotect_rejects_non_sentinel_value():
    with pytest.raises(ValueError):
        dpapi.unprotect("not-a-dpapi-blob")


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows guard")
def test_protect_unprotect_raise_off_windows():
    with pytest.raises(RuntimeError):
        dpapi.protect("x")
    with pytest.raises(RuntimeError):
        dpapi.unprotect("DPAPI:x")
