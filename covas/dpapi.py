"""Encrypt API keys at rest with Windows DPAPI — no plaintext key files on disk (issue #22).

COVAS++ is a publicly distributed Windows app: every user pastes their OWN Anthropic /
ElevenLabs / OpenAI / … keys, which used to land as plaintext under ``%APPDATA%\\COVAS++``.
For a streamed audience that's a casual-disclosure risk (a copied file, a synced/backed-up
``%APPDATA%``, another local account, a key flashing on a Twitch overlay). We wrap each key in
Windows' Data Protection API (``CryptProtectData``/``CryptUnprotectData``, ``CurrentUser`` scope
— the Python equivalent of .NET ``ProtectedData`` / ``DataProtectionScope.CurrentUser``): Windows
owns the key material, the app stores none, and a protected blob is useless on any other machine
or account.

Threat model — IN scope: casual disclosure, a stolen key file being usable elsewhere. OUT of
scope (by design): malware or an admin already running AS the user; DPAPI cannot stop that, and
at that point the API key is the least of the user's problems.

No new dependency: DPAPI is called via ``ctypes``/``crypt32``, mirroring the existing
``covas/single_instance.py`` and ``covas/keybinds/executor.py`` ``WinDLL`` usage. Windows-only by
design — ``protect``/``unprotect`` raise on other platforms (the app targets Windows; the
cross-platform test suite fakes them). Storage format: the ciphertext is base64'd and prefixed
with a ``DPAPI:`` sentinel so a reader can tell an encrypted blob from a legacy plaintext key and
migrate transparently.
"""
from __future__ import annotations

import base64
import ctypes
import sys

# Sentinel that marks an encrypted value on disk: "DPAPI:<base64(blob)>". Any file content
# WITHOUT this prefix is treated as a legacy plaintext key and migrated on read (see firstrun).
_SENTINEL = "DPAPI:"

# Never let CryptProtect/UnprotectData pop a UI prompt — this is a headless key store.
_CRYPTPROTECT_UI_FORBIDDEN = 0x01


class _DATA_BLOB(ctypes.Structure):
    """The Win32 ``DATA_BLOB`` (a length + pointer pair) DPAPI passes data in and out through."""
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def is_available() -> bool:
    """True where DPAPI can actually be used (Windows). Callers gate encryption on this so a
    non-Windows dev/CI run never tries to call crypt32."""
    return sys.platform == "win32"


def is_encrypted(value: str) -> bool:
    """True if `value` is one of our DPAPI blobs (has the ``DPAPI:`` sentinel), i.e. NOT a legacy
    plaintext key. Cheap prefix check — used to decide decrypt-vs-migrate on read."""
    return value.startswith(_SENTINEL)


def _blob(data: bytes) -> tuple[_DATA_BLOB, ctypes.Array]:
    """Wrap `data` in a DATA_BLOB. Returns the blob AND the backing buffer — the caller must keep
    the buffer referenced until the API call returns, or Python may free it out from under DPAPI."""
    buf = ctypes.create_string_buffer(data, len(data))
    return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _crypt32() -> "ctypes.WinDLL":
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    proto = [ctypes.POINTER(_DATA_BLOB), ctypes.c_wchar_p, ctypes.POINTER(_DATA_BLOB),
             ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(_DATA_BLOB)]
    crypt32.CryptProtectData.argtypes = proto
    crypt32.CryptProtectData.restype = ctypes.c_int
    crypt32.CryptUnprotectData.argtypes = proto
    crypt32.CryptUnprotectData.restype = ctypes.c_int
    return crypt32


def _read_and_free(blob_out: _DATA_BLOB) -> bytes:
    """Copy the bytes DPAPI allocated in `blob_out`, then LocalFree its buffer (the API allocates
    ``pbData`` with LocalAlloc — we own it and must release it)."""
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


def protect(plaintext: str) -> str:
    """Encrypt `plaintext` under the CurrentUser DPAPI scope, returning ``DPAPI:<base64(blob)>``.
    Windows-only — raises RuntimeError elsewhere (the app targets Windows; tests fake this)."""
    if not is_available():
        raise RuntimeError("DPAPI is only available on Windows")
    crypt32 = _crypt32()
    blob_in, _buf = _blob(plaintext.encode("utf-8"))
    blob_out = _DATA_BLOB()
    ok = crypt32.CryptProtectData(
        ctypes.byref(blob_in), None, None, None, None,
        _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
    if not ok:
        raise OSError(ctypes.get_last_error(), "CryptProtectData failed")
    raw = _read_and_free(blob_out)
    return _SENTINEL + base64.b64encode(raw).decode("ascii")


def unprotect(value: str) -> str:
    """Decrypt a ``DPAPI:<base64(blob)>`` value produced by `protect` on THIS machine+account,
    returning the original string. Raises on a non-DPAPI value or a blob that won't decrypt here
    (wrong machine/account, or tampering) — callers treat that as "no usable key". Windows-only."""
    if not is_available():
        raise RuntimeError("DPAPI is only available on Windows")
    if not is_encrypted(value):
        raise ValueError("not a DPAPI-encrypted value")
    crypt32 = _crypt32()
    raw = base64.b64decode(value[len(_SENTINEL):])
    blob_in, _buf = _blob(raw)
    blob_out = _DATA_BLOB()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None,
        _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
    if not ok:
        raise OSError(ctypes.get_last_error(), "CryptUnprotectData failed")
    return _read_and_free(blob_out).decode("utf-8")
