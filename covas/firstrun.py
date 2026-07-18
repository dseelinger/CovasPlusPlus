"""First-run configuration: the "is this install set up yet?" gate and the helpers the
setup wizard (covas/setup_web.py) is built on (INSTALLER_DESIGN.md — first-run flow).

A fresh install has no API keys and no STT weights. `is_configured` decides whether the
wizard needs to run; the entry point (run_covas_ui.py) shows the wizard first when it
returns False, and skips straight to the control panel once satisfied. Everything the
wizard writes lands under `data_dir()` — keys in their key files, choices in overrides.json,
STT weights in the models cache — so a packaged build never writes into its read-only tree.

Keys are stored ENCRYPTED at rest with Windows DPAPI (covas/dpapi.py, issue #22): the key file
holds ``DPAPI:<base64(blob)>`` instead of plaintext. Reads are DPAPI-aware and transparently
MIGRATE any legacy plaintext key (or a manually-dropped ``*.txt``) to encrypted on first read; a
blob that won't decrypt on this machine (e.g. a copied ``%APPDATA%``) is treated as "no key" with
a clear re-enter message, never a crash. Environment-variable key reads were removed here (they
were a plaintext bypass and masked fresh-install testing) — keys are FILE-only now.

Split of concerns:
  * Keys — Anthropic (LLM, REQUIRED) and ElevenLabs (TTS, OPTIONAL: no key ⇒ text-only, the
    existing fail-soft path).
  * STT — faster-whisper weights, download-on-first-run. Availability is a cache lookup; the
    wizard's model step downloads `small.en` (the locked shipped default) into data_dir/models
    when frozen, or the default HF cache in a source run (dev cache untouched).
  * Default voice — resolve ElevenLabs "George" by NAME, else the first valid voice, so a
    rotated catalog never dead-ends the wizard.

The pure pieces here (the gate, key presence, voice resolution, cache lookup) are unit-tested
offline; the actual download / device enumeration / network fetch are on-hardware only.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from huggingface_hub import try_to_load_from_cache

from . import dpapi
from .config import _frozen, data_dir, deep_merge, load_overrides, save_overrides

# The shipped STT default (INSTALLER_DESIGN "Resolved"): English-only `small.en` — smaller and
# more accurate than multilingual `small` at the same size, right for an English companion.
DEFAULT_STT_MODEL = "small.en"

# faster-whisper resolves these names to Systran HF repos. Hardcoded for the ones we care about
# so an availability check never has to import faster-whisper's private tables; unknown names
# fall through to the Systran naming convention.
_WHISPER_REPOS = {
    "tiny": "Systran/faster-whisper-tiny",
    "tiny.en": "Systran/faster-whisper-tiny.en",
    "base": "Systran/faster-whisper-base",
    "base.en": "Systran/faster-whisper-base.en",
    "small": "Systran/faster-whisper-small",
    "small.en": "Systran/faster-whisper-small.en",
    "medium": "Systran/faster-whisper-medium",
    "medium.en": "Systran/faster-whisper-medium.en",
    "large-v3": "Systran/faster-whisper-large-v3",
}


def _whisper_repo(model: str) -> str:
    return _WHISPER_REPOS.get(model, f"Systran/faster-whisper-{model}")


# ---- API keys ------------------------------------------------------------------------

def _key_path(cfg: dict, section: str) -> Path | None:
    """The resolved (absolute) key file for a provider section, or None if unconfigured.
    config.py resolves `<section>.api_key_file` under data_dir at load time."""
    raw = (cfg.get(section, {}) or {}).get("api_key_file")
    return Path(raw) if raw else None


def _read_key(path: Path | None) -> str | None:
    """Read a key file, DPAPI-aware. An encrypted ``DPAPI:`` blob is decrypted; a blob that won't
    decrypt on THIS machine/account returns None with a clear re-enter message (not a crash). Any
    non-sentinel content is a legacy PLAINTEXT key — returned as-is and transparently re-encrypted
    in place (best-effort: a failed migration never fails the read)."""
    if not (path and path.exists()):
        return None
    try:
        content = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not content:
        return None
    if dpapi.is_encrypted(content):
        try:
            return dpapi.unprotect(content) or None
        except Exception:  # noqa: BLE001 — wrong machine/account or tampering => no usable key
            print(
                f"Could not decrypt the key in {path.name} on this machine — it was encrypted for "
                "a different Windows account or PC. Re-enter the key here to use it.",
                file=sys.stderr, flush=True)
            return None
    # Legacy plaintext (or a hand-dropped *.txt): migrate to encrypted, best-effort.
    _migrate_plaintext_key(path, content)
    return content or None


def _migrate_plaintext_key(path: Path, key: str) -> None:
    """Re-encrypt a legacy plaintext key file in place. Best-effort — swallow any failure (a
    non-Windows/CI run, or a write error) so it never breaks the read that triggered it."""
    try:
        _write_key(path, key)
    except Exception:  # noqa: BLE001 — migration is opportunistic; the plaintext read still stands
        pass


def _write_key(path: Path | None, key: str) -> None:
    """Write a key ENCRYPTED (``DPAPI:<blob>``). Empty/whitespace keys are written as-is (an empty
    file reads back as "no key") without encrypting nothing."""
    if path is None:
        raise ValueError("no api_key_file configured for this provider")
    path.parent.mkdir(parents=True, exist_ok=True)
    key = (key or "").strip()
    path.write_text(dpapi.protect(key) if key else "", encoding="utf-8")


# Env-var key reads were removed (issue #22): they were a plaintext bypass and masked
# fresh-install testing. Every key is now FILE-only (DPAPI-encrypted at rest). Note the Anthropic
# SDK still falls back to its own ANTHROPIC_API_KEY env var internally — but that's neutralized in
# practice because `is_configured` gates the whole app on a FILE key (anthropic_key_available), so
# an env var with no key file leaves the app unconfigured and the wizard runs.

def anthropic_key(cfg: dict) -> str | None:
    """The Anthropic key from its DPAPI-encrypted key file under data_dir (file-only, no env var)."""
    return _read_key(_key_path(cfg, "anthropic"))


def elevenlabs_key(cfg: dict) -> str | None:
    return _read_key(_key_path(cfg, "elevenlabs"))


def azure_key(cfg: dict) -> str | None:
    """The Azure Speech key from its DPAPI-encrypted key file under data_dir (file-only)."""
    return _read_key(_key_path(cfg, "azure"))


def openai_key(cfg: dict) -> str | None:
    """The OpenAI key, shared between the OpenAI LLM (#12) and TTS (#16) providers. Read from the
    LLM's `[openai].api_key_file`, falling back to `[openai_tts].api_key_file` (both default to the
    same OpenAIAPIKey.txt, so one file serves both). File-only, DPAPI-encrypted."""
    return _read_key(_key_path(cfg, "openai")) or _read_key(_key_path(cfg, "openai_tts"))


def gemini_key(cfg: dict) -> str | None:
    """The Gemini key from its DPAPI-encrypted key file under data_dir (file-only)."""
    return _read_key(_key_path(cfg, "gemini"))


def cartesia_key(cfg: dict) -> str | None:
    """The Cartesia key from its DPAPI-encrypted key file under data_dir (file-only)."""
    return _read_key(_key_path(cfg, "cartesia"))


def inara_key(cfg: dict) -> str | None:
    """The Inara Community-Goals key (issue #24). Prefers the DPAPI-encrypted key file
    (`[cg].api_key_file`, default InaraAPIKey.txt under data_dir); if that's empty but a LEGACY inline
    `[cg].inara_api_key` is present (the old plaintext-in-overrides.json path), MIGRATE it — encrypt
    into the key file, blank the inline value — so no plaintext CG key lingers. File-only thereafter,
    so the "zero plaintext keys anywhere" guarantee (issue #21) holds for the one inline secret."""
    key = _read_key(_key_path(cfg, "cg"))
    if key:
        return key
    legacy = str((cfg.get("cg", {}) or {}).get("inara_api_key", "") or "").strip()
    if legacy:
        return _migrate_inline_inara_key(cfg, legacy)
    return None


def _migrate_inline_inara_key(cfg: dict, key: str) -> str:
    """One-time move of a legacy inline `[cg].inara_api_key` into the encrypted key file: encrypt it
    into InaraAPIKey.txt, then blank the inline value in overrides.json AND the live cfg so it's never
    read (or re-migrated) again. Best-effort on the write side — if encryption/persist fails we still
    return the key so this run's feed keeps working; the plaintext just isn't cleared yet."""
    try:
        _write_key(_key_path(cfg, "cg"), key)
        overrides = load_overrides()
        overrides.setdefault("cg", {})["inara_api_key"] = ""
        save_overrides(overrides)
        cfg.setdefault("cg", {})["inara_api_key"] = ""
        print("Migrated the inline Inara API key to an encrypted InaraAPIKey.txt "
              "(the plaintext value has been cleared from overrides.json).",
              file=sys.stderr, flush=True)
    except Exception:  # noqa: BLE001 — migration is opportunistic; the key still works this run
        pass
    return key


# ---- Generic, section-keyed key management (issue #23) -------------------------------
# The masked "API keys" Settings card rotates ANY provider's key, so the write/clear/presence
# helpers are keyed by config SECTION (anthropic, elevenlabs, openai, gemini, azure, cartesia,
# cg) rather than one function per provider. Each section's `api_key_file` is the single store;
# the provider-specific savers/readers below stay as thin, named wrappers.

def save_key(cfg: dict, section: str, key: str) -> None:
    """Write a provider's key ENCRYPTED to its section's `api_key_file`. A blank/whitespace key
    writes an empty file (i.e. clears it) — callers that want blank to be a no-op guard first."""
    _write_key(_key_path(cfg, section), key)


def clear_key(cfg: dict, section: str) -> None:
    """Remove a provider's stored key by writing an empty key file (badge flips to not-set)."""
    _write_key(_key_path(cfg, section), "")


def key_available(cfg: dict, section: str) -> bool:
    """Whether a usable key is stored for a provider SECTION (its `api_key_file` decrypts to a
    non-empty key). Section-keyed so the Settings card's set/not-set badges need no per-provider
    knowledge. Reading also transparently migrates a legacy plaintext key file to encrypted."""
    return bool(_read_key(_key_path(cfg, section)))


def save_anthropic_key(cfg: dict, key: str) -> None:
    save_key(cfg, "anthropic", key)


def save_elevenlabs_key(cfg: dict, key: str) -> None:
    save_key(cfg, "elevenlabs", key)


def save_inara_key(cfg: dict, key: str) -> None:
    """Write the Inara CG key ENCRYPTED to its key file (Settings/wizard entry). Blank clears it."""
    save_key(cfg, "cg", key)


def anthropic_key_available(cfg: dict) -> bool:
    return key_available(cfg, "anthropic")


def elevenlabs_key_available(cfg: dict) -> bool:
    return key_available(cfg, "elevenlabs")


# ---- provider-aware readiness (issue #87) --------------------------------------------
# The wizard is no longer Anthropic-only: any supported cloud LLM + TTS combo can finish setup.
# "Ready" is per-provider — a cloud LLM needs its key, and a voice is free on Edge/Piper (no key)
# but key-gated on the cloud voices. These reuse the per-provider key helpers above so there's ONE
# source of truth for "is this provider usable".

def llm_ready(cfg: dict) -> bool:
    """Whether the ACTIVE [llm].provider has what it needs to answer: a usable key for the cloud
    provider (Anthropic/OpenAI-compatible/Gemini). This — not "has an Anthropic key" — is what gates
    the wizard now (issue #87). Every LLM provider is cloud (issue #128 removed the local one)."""
    provider = str(cfg.get("llm", {}).get("provider", "anthropic")).lower()
    if provider == "openai":
        return bool(openai_key(cfg))
    if provider == "gemini":
        return bool(gemini_key(cfg))
    # anthropic (and any unknown value) → the Anthropic key.
    return anthropic_key_available(cfg)


def tts_ready(cfg: dict) -> bool:
    """Whether the ACTIVE [tts].provider can produce a voice. Edge and Piper are FREE and need no
    key, so a keyless install still gets a voice (not dropped to text-only); the cloud voices are
    key-gated. This is the wizard's "has a voice" check — surfaced, not a hard gate (issue #87)."""
    provider = str(cfg.get("tts", {}).get("provider", "edge")).lower()
    if provider in ("edge", "piper"):
        return True                       # local/free — no key required
    if provider == "elevenlabs":
        return elevenlabs_key_available(cfg)
    if provider == "azure":
        return bool(azure_key(cfg))
    if provider == "openai":
        return bool(openai_key(cfg))
    if provider == "cartesia":
        return bool(cartesia_key(cfg))
    return True


def text_only_mode(cfg: dict, *, mock: bool = False, tts_injected: bool = False) -> bool:
    """Whether the app should run TEXT-ONLY: ElevenLabs is the selected TTS but no key is
    available. This is a first-class supported mode (INSTALLER_DESIGN decision #2 — Piper isn't
    offered in the packaged app), NOT a fault, so the voice loop skips TTS quietly instead of
    raising a per-turn error. A mock run or an explicitly injected TTS provider owns its own
    behaviour and is never forced text-only."""
    if mock or tts_injected:
        return False
    provider = str(cfg.get("tts", {}).get("provider", "elevenlabs")).lower()
    return provider == "elevenlabs" and not elevenlabs_key_available(cfg)


# ---- STT weights ---------------------------------------------------------------------

def stt_download_root(cfg: dict) -> str | None:
    """Where faster-whisper caches its weights. An explicit `[whisper].download_root` wins
    (a test seam, resolved under data_dir by config.py); otherwise a frozen build caches under
    data_dir/models (keeps weights out of the read-only install tree), and a source run returns
    None — the default HF cache, so a dev's existing models are reused, not re-downloaded."""
    explicit = (cfg.get("whisper", {}) or {}).get("download_root")
    if explicit:
        return str(explicit)
    if _frozen():
        return str(data_dir() / "models")
    return None


def stt_model_available(cfg: dict, model: str | None = None) -> bool:
    """True when the STT weights are already on disk (so the wizard can skip the download).
    A local path model counts if it exists; a named model is a lookup in the HF cache that
    `stt_download_root` points at (default cache in a source run)."""
    model = model or (cfg.get("whisper", {}) or {}).get("model") or DEFAULT_STT_MODEL
    # A filesystem path (a hand-placed model dir) is available iff it exists.
    if os.sep in str(model) or (os.altsep and os.altsep in str(model)):
        return Path(str(model)).exists()
    hit = try_to_load_from_cache(
        repo_id=_whisper_repo(model), filename="model.bin",
        cache_dir=stt_download_root(cfg))
    return isinstance(hit, str) and os.path.exists(hit)


def download_stt_model(model: str = DEFAULT_STT_MODEL, download_root: str | None = None) -> None:
    """Fetch the STT weights by constructing a throwaway WhisperModel — its init downloads the
    repo into `download_root` (or the default cache). We discard the instance; only the on-disk
    weights matter (the real Transcriber builds its own at startup). Blocking: the wizard runs
    this on a background thread and polls a status flag. Raises on failure (network/disk) so the
    wizard can surface it."""
    from faster_whisper import WhisperModel
    WhisperModel(model, device="cpu", compute_type="int8", download_root=download_root)


# ---- Microphones ---------------------------------------------------------------------

def list_input_devices() -> list[dict]:
    """Enumerate capture devices for the mic picker: {index, name} for every device with input
    channels. Imports sounddevice lazily so a headless/test import of this module needs no
    PortAudio."""
    import sounddevice as sd
    out: list[dict] = []
    for i, d in enumerate(sd.query_devices()):
        if int(d.get("max_input_channels", 0)) > 0:
            out.append({"index": i, "name": str(d.get("name", f"device {i}"))})
    return out


# ---- Default voice -------------------------------------------------------------------

def resolve_default_voice(voices: list[dict], preferred: str = "George") -> dict | None:
    """Pick the initial TTS voice from an ElevenLabs voice list: the one named `preferred`
    (case-insensitive), else the FIRST voice returned. None only when the list is empty. The
    caller passes an already famous-filtered list (elevenlabs.list_voices), so "first valid" is
    just the first entry. This is the *initial* default — set once, never re-applied over a
    voice the user later changed (INSTALLER_DESIGN decision #6)."""
    if not voices:
        return None
    want = preferred.strip().lower()
    for v in voices:
        if str(v.get("name", "")).strip().lower() == want:
            return {"voice_id": v.get("voice_id"), "name": v.get("name")}
    first = voices[0]
    return {"voice_id": first.get("voice_id"), "name": first.get("name")}


# ---- The gate ------------------------------------------------------------------------

def is_configured(cfg: dict) -> bool:
    """Can the app start its real voice loop? Needs the ACTIVE LLM provider ready (a usable key for
    the cloud LLM — issue #87, no longer Anthropic-specific) and the
    STT weights (voice input). The VOICE is OPTIONAL — Edge/Piper give one for free, a keyless cloud
    voice degrades to text-only — so it does NOT gate; a mic isn't gated either (default works)."""
    return llm_ready(cfg) and stt_model_available(cfg)


# The provider sections whose set/not-set key flag the wizard surfaces (so it can badge the active
# LLM/TTS provider's key without per-provider branching in the template).
_WIZARD_KEY_SECTIONS = ("anthropic", "openai", "gemini", "elevenlabs", "azure", "cartesia")


def configured_status(cfg: dict) -> dict:
    """Per-requirement view for the wizard's progress display, provider-aware (issue #87).
    `configured` mirrors `is_configured` (active LLM ready + STT); `voice` is surfaced so the wizard
    can show the text-only consequence without blocking on it; `keys` carries the set/not-set flag
    per managed provider section so the UI can badge whichever provider the user picked."""
    return {
        "llm_provider": str(cfg.get("llm", {}).get("provider", "anthropic")).lower(),
        "tts_provider": str(cfg.get("tts", {}).get("provider", "edge")).lower(),
        "llm": llm_ready(cfg),
        "voice": tts_ready(cfg),
        "stt": stt_model_available(cfg),
        "configured": is_configured(cfg),
        "keys": {sec: key_available(cfg, sec) for sec in _WIZARD_KEY_SECTIONS},
    }


# ---- Override writes (used by the wizard) --------------------------------------------

def apply_override(cfg: dict, patch: dict) -> None:
    """Persist a settings patch to overrides.json AND merge it into the live `cfg` so the
    wizard's subsequent status reads reflect the change (config.py doesn't reload mid-run).
    Used for the mic choice, the resolved voice, and the STT model the wizard installs."""
    overrides = load_overrides()
    deep_merge(overrides, patch)
    save_overrides(overrides)
    deep_merge(cfg, patch)
