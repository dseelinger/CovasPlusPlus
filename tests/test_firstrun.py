"""Offline unit tests for the first-run gate + helpers (covas/firstrun.py, I3).

Pure logic only — the "configured?" gate, key presence/round-trip, STT-cache lookup (with a
monkeypatched huggingface_hub), and the default-voice resolution. The real model download,
mic enumeration, and ElevenLabs fetch are on-hardware and not exercised here.
"""
from __future__ import annotations

import pytest

from covas import firstrun


def _cfg(tmp_path, *, anth_file="anth.txt", el_file="el.txt", model="small.en",
         download_root=""):
    """A minimal cfg with the key-file + whisper fields firstrun reads, pointed at tmp so no
    real key/model is touched. Key paths are absolute (as config.py resolves them at load)."""
    return {
        "anthropic": {"api_key_file": str(tmp_path / anth_file)},
        "elevenlabs": {"api_key_file": str(tmp_path / el_file)},
        "whisper": {"model": model, "download_root": download_root},
    }


# ---- key presence + round-trip ------------------------------------------------------

def test_keys_absent_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = _cfg(tmp_path)
    assert firstrun.anthropic_key_available(cfg) is False
    assert firstrun.elevenlabs_key_available(cfg) is False


def test_save_and_read_keys_round_trip(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = _cfg(tmp_path)
    firstrun.save_anthropic_key(cfg, "  sk-ant-123  ")   # trimmed on write
    firstrun.save_elevenlabs_key(cfg, "el-key-9")
    assert firstrun.anthropic_key(cfg) == "sk-ant-123"
    assert firstrun.elevenlabs_key(cfg) == "el-key-9"
    assert firstrun.anthropic_key_available(cfg) is True
    assert firstrun.elevenlabs_key_available(cfg) is True


def test_empty_key_file_is_not_available(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = _cfg(tmp_path)
    firstrun.save_anthropic_key(cfg, "   ")              # whitespace only
    assert firstrun.anthropic_key(cfg) is None
    assert firstrun.anthropic_key_available(cfg) is False


def test_env_var_wins_over_missing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    cfg = _cfg(tmp_path)                                  # no file written
    assert firstrun.anthropic_key(cfg) == "env-key"
    assert firstrun.anthropic_key_available(cfg) is True


# ---- STT availability (monkeypatched cache lookup) ----------------------------------

def test_stt_available_when_cache_hits(tmp_path, monkeypatch):
    hit = tmp_path / "model.bin"
    hit.write_bytes(b"x")
    monkeypatch.setattr(firstrun, "try_to_load_from_cache", lambda **k: str(hit))
    assert firstrun.stt_model_available(_cfg(tmp_path)) is True


def test_stt_unavailable_when_cache_misses(tmp_path, monkeypatch):
    # try_to_load_from_cache returns None (or a sentinel) when the file isn't cached.
    monkeypatch.setattr(firstrun, "try_to_load_from_cache", lambda **k: None)
    assert firstrun.stt_model_available(_cfg(tmp_path)) is False


def test_stt_available_for_local_path_model(tmp_path):
    model_dir = tmp_path / "my-model"
    model_dir.mkdir()
    cfg = _cfg(tmp_path, model=str(model_dir))
    assert firstrun.stt_model_available(cfg) is True
    cfg2 = _cfg(tmp_path, model=str(tmp_path / "missing-model"))
    assert firstrun.stt_model_available(cfg2) is False


def test_stt_download_root_source_vs_frozen(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    # Source run: None (default HF cache — dev models reused).
    monkeypatch.setattr(firstrun, "_frozen", lambda: False)
    assert firstrun.stt_download_root(cfg) is None
    # Frozen: under data_dir/models.
    monkeypatch.setattr(firstrun, "_frozen", lambda: True)
    monkeypatch.setattr(firstrun, "data_dir", lambda: tmp_path)
    assert firstrun.stt_download_root(cfg) == str(tmp_path / "models")
    # Explicit override always wins.
    cfg2 = _cfg(tmp_path, download_root=str(tmp_path / "custom"))
    assert firstrun.stt_download_root(cfg2) == str(tmp_path / "custom")


# ---- the gate -----------------------------------------------------------------------

@pytest.mark.parametrize("has_key,has_stt,expected", [
    (True, True, True),
    (True, False, False),
    (False, True, False),
    (False, False, False),
])
def test_is_configured_needs_key_and_stt(tmp_path, monkeypatch, has_key, has_stt, expected):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = _cfg(tmp_path)
    if has_key:
        firstrun.save_anthropic_key(cfg, "k")
    monkeypatch.setattr(firstrun, "stt_model_available", lambda *a, **k: has_stt)
    assert firstrun.is_configured(cfg) is expected


def test_configured_status_shape(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = _cfg(tmp_path)
    firstrun.save_anthropic_key(cfg, "k")
    firstrun.save_elevenlabs_key(cfg, "e")
    monkeypatch.setattr(firstrun, "stt_model_available", lambda *a, **k: True)
    st = firstrun.configured_status(cfg)
    assert st == {"anthropic": True, "elevenlabs": True, "stt": True, "configured": True}
    # ElevenLabs is optional: absent EL key still leaves the app "configured".
    firstrun.save_elevenlabs_key(cfg, "")
    assert firstrun.configured_status(cfg)["configured"] is True
    assert firstrun.configured_status(cfg)["elevenlabs"] is False


# ---- default voice resolution -------------------------------------------------------

def test_resolve_voice_prefers_george_by_name():
    voices = [{"voice_id": "1", "name": "Sarah"}, {"voice_id": "2", "name": "George"}]
    assert firstrun.resolve_default_voice(voices) == {"voice_id": "2", "name": "George"}


def test_resolve_voice_is_case_insensitive():
    voices = [{"voice_id": "9", "name": "  george  "}]
    assert firstrun.resolve_default_voice(voices)["voice_id"] == "9"


def test_resolve_voice_falls_back_to_first_when_george_absent():
    voices = [{"voice_id": "a", "name": "Adam"}, {"voice_id": "b", "name": "Bella"}]
    assert firstrun.resolve_default_voice(voices) == {"voice_id": "a", "name": "Adam"}


def test_resolve_voice_none_on_empty_list():
    assert firstrun.resolve_default_voice([]) is None


# ---- text-only mode ------------------------------------------------------------------

def test_text_only_when_elevenlabs_and_no_key(tmp_path):
    cfg = _cfg(tmp_path)
    cfg["tts"] = {"provider": "elevenlabs"}
    assert firstrun.text_only_mode(cfg) is True                     # no EL key file


def test_not_text_only_with_key(tmp_path):
    cfg = _cfg(tmp_path)
    cfg["tts"] = {"provider": "elevenlabs"}
    firstrun.save_elevenlabs_key(cfg, "el-key")
    assert firstrun.text_only_mode(cfg) is False


def test_default_provider_is_elevenlabs(tmp_path):
    cfg = _cfg(tmp_path)                                            # no [tts] section at all
    assert firstrun.text_only_mode(cfg) is True                    # default provider = elevenlabs


def test_not_text_only_for_mock_or_injected_or_piper(tmp_path):
    cfg = _cfg(tmp_path)
    cfg["tts"] = {"provider": "elevenlabs"}
    assert firstrun.text_only_mode(cfg, mock=True) is False        # mock owns its behaviour
    assert firstrun.text_only_mode(cfg, tts_injected=True) is False  # injected provider wins
    cfg["tts"] = {"provider": "piper"}
    assert firstrun.text_only_mode(cfg) is False                   # piper is a local TTS
