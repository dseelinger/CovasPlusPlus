"""First-run configuration: the "is this install set up yet?" gate and the helpers the
setup wizard (covas/setup_web.py) is built on (INSTALLER_DESIGN.md — first-run flow).

A fresh install has no API keys and no STT weights. `is_configured` decides whether the
wizard needs to run; the entry point (run_covas_ui.py) shows the wizard first when it
returns False, and skips straight to the control panel once satisfied. Everything the
wizard writes lands under `data_dir()` — keys in their key files, choices in overrides.json,
STT weights in the models cache — so a packaged build never writes into its read-only tree.

Split of concerns:
  * Keys — Anthropic (LLM, REQUIRED) and ElevenLabs (TTS, OPTIONAL: no key ⇒ text-only, the
    existing fail-soft path). Anthropic's key also honours the ANTHROPIC_API_KEY env var so a
    source-run dev with it exported is "configured" with no file and no wizard.
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
from pathlib import Path

from huggingface_hub import try_to_load_from_cache

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
    if path and path.exists():
        try:
            return path.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None
    return None


def _write_key(path: Path | None, key: str) -> None:
    if path is None:
        raise ValueError("no api_key_file configured for this provider")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text((key or "").strip(), encoding="utf-8")


def anthropic_key(cfg: dict) -> str | None:
    """The Anthropic key, env var first (a source-run dev's exported ANTHROPIC_API_KEY wins,
    so they never see the wizard), else the key file under data_dir."""
    env = os.environ.get("ANTHROPIC_API_KEY")
    if env and env.strip():
        return env.strip()
    return _read_key(_key_path(cfg, "anthropic"))


def elevenlabs_key(cfg: dict) -> str | None:
    return _read_key(_key_path(cfg, "elevenlabs"))


def azure_key(cfg: dict) -> str | None:
    """The Azure Speech key, env var first (an exported AZURE_SPEECH_KEY wins for a source-run
    dev), else the key file under data_dir."""
    env = os.environ.get("AZURE_SPEECH_KEY")
    if env and env.strip():
        return env.strip()
    return _read_key(_key_path(cfg, "azure"))


def openai_key(cfg: dict) -> str | None:
    """The OpenAI key, shared between the OpenAI LLM (#12) and TTS (#16) providers. Env var first
    (an exported OPENAI_API_KEY wins — the natural way to share ONE key), else the key file: the
    LLM's `[openai].api_key_file`, falling back to `[openai_tts].api_key_file` (both default to the
    same OpenAIAPIKey.txt, so one file serves both)."""
    env = os.environ.get("OPENAI_API_KEY")
    if env and env.strip():
        return env.strip()
    return _read_key(_key_path(cfg, "openai")) or _read_key(_key_path(cfg, "openai_tts"))


def gemini_key(cfg: dict) -> str | None:
    """The Gemini key, env var first (GEMINI_API_KEY, then the more general GOOGLE_API_KEY that a
    Google-Cloud dev may already have exported), else the key file under data_dir."""
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        env = os.environ.get(name)
        if env and env.strip():
            return env.strip()
    return _read_key(_key_path(cfg, "gemini"))


def cartesia_key(cfg: dict) -> str | None:
    """The Cartesia key, env var first (an exported CARTESIA_API_KEY wins for a source-run dev),
    else the key file under data_dir."""
    env = os.environ.get("CARTESIA_API_KEY")
    if env and env.strip():
        return env.strip()
    return _read_key(_key_path(cfg, "cartesia"))


def save_anthropic_key(cfg: dict, key: str) -> None:
    _write_key(_key_path(cfg, "anthropic"), key)


def save_elevenlabs_key(cfg: dict, key: str) -> None:
    _write_key(_key_path(cfg, "elevenlabs"), key)


def anthropic_key_available(cfg: dict) -> bool:
    return bool(anthropic_key(cfg))


def elevenlabs_key_available(cfg: dict) -> bool:
    return bool(elevenlabs_key(cfg))


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
    """Can the app start its real voice loop? Needs the Anthropic key (the LLM is the core) and
    the STT weights (voice input). ElevenLabs is OPTIONAL — without it the app runs text-only —
    so it does NOT gate; a mic isn't gated either (the default device works)."""
    return anthropic_key_available(cfg) and stt_model_available(cfg)


def configured_status(cfg: dict) -> dict:
    """Per-requirement view for the wizard's progress display. `configured` mirrors
    `is_configured`; `elevenlabs` is surfaced so the wizard can show the text-only consequence
    without blocking on it."""
    a = anthropic_key_available(cfg)
    e = elevenlabs_key_available(cfg)
    s = stt_model_available(cfg)
    return {"anthropic": a, "elevenlabs": e, "stt": s, "configured": a and s}


# ---- Override writes (used by the wizard) --------------------------------------------

def apply_override(cfg: dict, patch: dict) -> None:
    """Persist a settings patch to overrides.json AND merge it into the live `cfg` so the
    wizard's subsequent status reads reflect the change (config.py doesn't reload mid-run).
    Used for the mic choice, the resolved voice, and the STT model the wizard installs."""
    overrides = load_overrides()
    deep_merge(overrides, patch)
    save_overrides(overrides)
    deep_merge(cfg, patch)
