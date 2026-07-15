"""Offline unit tests for the first-run gate + helpers (covas/firstrun.py, I3).

Pure logic only — the "configured?" gate, key presence/round-trip, STT-cache lookup (with a
monkeypatched huggingface_hub), and the default-voice resolution. The real model download,
mic enumeration, and ElevenLabs fetch are on-hardware and not exercised here.
"""
from __future__ import annotations

import base64

import pytest

from covas import dpapi, firstrun


@pytest.fixture(autouse=True)
def fake_dpapi(monkeypatch):
    """Reversible, in-process stand-in for Windows DPAPI so firstrun's encrypt/decrypt/migrate path
    runs hermetically on ANY OS (real crypt32 is Windows-only). `protect` base64s the key behind the
    sentinel; `unprotect` reverses it and raises on anything that isn't valid base64 — mimicking a
    blob copied from another machine that won't decrypt here."""
    def protect(s: str) -> str:
        return "DPAPI:" + base64.b64encode(s.encode("utf-8")).decode("ascii")

    def unprotect(v: str) -> str:
        payload = v[len("DPAPI:"):]
        try:
            return base64.b64decode(payload.encode("ascii"), validate=True).decode("utf-8")
        except Exception as e:  # noqa: BLE001
            raise OSError("cannot decrypt on this machine") from e

    monkeypatch.setattr(dpapi, "protect", protect)
    monkeypatch.setattr(dpapi, "unprotect", unprotect)


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

def test_keys_absent_by_default(tmp_path):
    cfg = _cfg(tmp_path)
    assert firstrun.anthropic_key_available(cfg) is False
    assert firstrun.elevenlabs_key_available(cfg) is False


def test_save_and_read_keys_round_trip(tmp_path):
    cfg = _cfg(tmp_path)
    firstrun.save_anthropic_key(cfg, "  sk-ant-123  ")   # trimmed on write
    firstrun.save_elevenlabs_key(cfg, "el-key-9")
    assert firstrun.anthropic_key(cfg) == "sk-ant-123"
    assert firstrun.elevenlabs_key(cfg) == "el-key-9"
    assert firstrun.anthropic_key_available(cfg) is True
    assert firstrun.elevenlabs_key_available(cfg) is True


def test_saved_key_file_is_encrypted_on_disk(tmp_path):
    """The wizard's save writes ciphertext (DPAPI: sentinel), never the raw key."""
    cfg = _cfg(tmp_path)
    firstrun.save_anthropic_key(cfg, "sk-ant-secret")
    raw = (tmp_path / "anth.txt").read_text(encoding="utf-8")
    assert raw.startswith("DPAPI:")
    assert "sk-ant-secret" not in raw


def test_empty_key_file_is_not_available(tmp_path):
    cfg = _cfg(tmp_path)
    firstrun.save_anthropic_key(cfg, "   ")              # whitespace only
    assert (tmp_path / "anth.txt").read_text(encoding="utf-8") == ""   # empty, not "DPAPI:"
    assert firstrun.anthropic_key(cfg) is None
    assert firstrun.anthropic_key_available(cfg) is False


def test_legacy_plaintext_key_is_read_then_migrated(tmp_path):
    """A hand-dropped/legacy PLAINTEXT key reads back verbatim AND is re-encrypted in place, so the
    next read is DPAPI-protected — without ever failing the first read."""
    cfg = _cfg(tmp_path)
    key_file = tmp_path / "anth.txt"
    key_file.write_text("sk-ant-plaintext", encoding="utf-8")   # legacy, no sentinel
    assert firstrun.anthropic_key(cfg) == "sk-ant-plaintext"    # transparent read
    migrated = key_file.read_text(encoding="utf-8")
    assert migrated.startswith("DPAPI:") and "sk-ant-plaintext" not in migrated  # rewritten
    assert firstrun.anthropic_key(cfg) == "sk-ant-plaintext"    # still reads correctly after


def test_inara_key_reads_encrypted_file(tmp_path):
    """`inara_key` reads the DPAPI-encrypted `[cg].api_key_file` — the same file-only path as every
    other provider key (issue #24)."""
    cfg = {"cg": {"source": "inara", "api_key_file": str(tmp_path / "InaraAPIKey.txt")}}
    assert firstrun.inara_key(cfg) is None                       # nothing stored yet
    firstrun.save_inara_key(cfg, "  inara-abc  ")                # trimmed on write
    stored = (tmp_path / "InaraAPIKey.txt").read_text(encoding="utf-8")
    assert stored.startswith("DPAPI:") and "inara-abc" not in stored   # encrypted at rest
    assert firstrun.inara_key(cfg) == "inara-abc"


def test_inara_key_migrates_and_blanks_legacy_inline(tmp_path, monkeypatch, capsys):
    """A legacy inline `[cg].inara_api_key` is migrated on first read: encrypted into InaraAPIKey.txt,
    then blanked in overrides.json AND the live cfg so it's never read as plaintext again."""
    key_file = tmp_path / "InaraAPIKey.txt"
    cfg = {"cg": {"source": "inara", "api_key_file": str(key_file),
                  "inara_api_key": "legacy-inline-key"}}
    saved: dict = {}
    monkeypatch.setattr(firstrun, "load_overrides",
                        lambda: {"cg": {"inara_api_key": "legacy-inline-key"}})
    monkeypatch.setattr(firstrun, "save_overrides", lambda o: saved.update(o))

    assert firstrun.inara_key(cfg) == "legacy-inline-key"        # migration returns the key
    assert key_file.read_text(encoding="utf-8").startswith("DPAPI:")   # now encrypted on disk
    assert saved["cg"]["inara_api_key"] == ""                    # blanked in overrides.json
    assert cfg["cg"]["inara_api_key"] == ""                      # and in the live cfg
    assert "migrated" in capsys.readouterr().err.lower()
    # Second read comes straight from the encrypted file — no inline value left to re-migrate.
    assert firstrun.inara_key(cfg) == "legacy-inline-key"


def test_undecryptable_blob_reads_as_no_key(tmp_path, capsys):
    """A DPAPI blob that won't decrypt here (copied %APPDATA%) is treated as "no key" with a clear
    message, not a crash — and the file is left untouched (not clobbered)."""
    cfg = _cfg(tmp_path)
    key_file = tmp_path / "anth.txt"
    key_file.write_text("DPAPI:@@@not-valid-base64@@@", encoding="utf-8")
    assert firstrun.anthropic_key(cfg) is None
    assert firstrun.anthropic_key_available(cfg) is False
    assert "re-enter" in capsys.readouterr().err.lower()
    assert key_file.read_text(encoding="utf-8") == "DPAPI:@@@not-valid-base64@@@"  # not clobbered


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
    cfg = _cfg(tmp_path)
    if has_key:
        firstrun.save_anthropic_key(cfg, "k")
    monkeypatch.setattr(firstrun, "stt_model_available", lambda *a, **k: has_stt)
    assert firstrun.is_configured(cfg) is expected


def test_configured_status_shape(tmp_path, monkeypatch):
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
