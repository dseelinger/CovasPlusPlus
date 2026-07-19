"""Offline unit tests for the first-run gate + helpers (covas/firstrun.py, I3).

Pure logic only — the "configured?" gate, key presence/round-trip, STT-cache lookup (with a
monkeypatched huggingface_hub), and the default-voice resolution. The real model download,
mic enumeration, and ElevenLabs fetch are on-hardware and not exercised here.
"""
from __future__ import annotations

import base64
import sys

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
    # Provider-aware shape (issue #87): active LLM/TTS provider + readiness + STT + per-section keys.
    assert st["llm_provider"] == "anthropic" and st["tts_provider"] == "edge"
    assert st["llm"] is True and st["stt"] is True and st["configured"] is True
    assert st["voice"] is True                       # edge (default) is a free voice, no key needed
    assert st["keys"]["anthropic"] is True and st["keys"]["elevenlabs"] is True
    assert st["keys"]["gemini"] is False


# ---- provider-aware gate (issue #87) -------------------------------------------------

def _provider_cfg(tmp_path, llm, tts="edge"):
    """A cfg with a key file per section and the chosen llm/tts providers — NO Anthropic key set."""
    return {
        "llm": {"provider": llm}, "tts": {"provider": tts},
        "anthropic": {"api_key_file": str(tmp_path / "anth.txt")},
        "openai": {"api_key_file": str(tmp_path / "openai.txt"),
                   "base_url": "https://api.groq.com/openai/v1", "model": "llama-3.3-70b"},
        "gemini": {"api_key_file": str(tmp_path / "gemini.txt"), "model": "gemini-flash-lite-latest"},
        "elevenlabs": {"api_key_file": str(tmp_path / "el.txt")},
        "azure": {"api_key_file": str(tmp_path / "azure.txt")},
        "cartesia": {"api_key_file": str(tmp_path / "cartesia.txt")},
        "whisper": {"model": "small.en", "download_root": ""},
    }


def test_is_configured_openai_llm_no_anthropic_key(tmp_path, monkeypatch):
    """A configured OpenAI-compatible LLM (its own key) finishes with NO Anthropic key."""
    cfg = _provider_cfg(tmp_path, "openai")
    monkeypatch.setattr(firstrun, "stt_model_available", lambda *a, **k: True)
    assert firstrun.is_configured(cfg) is False        # no OpenAI key yet
    firstrun.save_key(cfg, "openai", "sk-openai")
    assert firstrun.anthropic_key_available(cfg) is False
    assert firstrun.is_configured(cfg) is True          # ready on the OpenAI key alone


def test_is_configured_gemini_llm_no_anthropic_key(tmp_path, monkeypatch):
    cfg = _provider_cfg(tmp_path, "gemini")
    monkeypatch.setattr(firstrun, "stt_model_available", lambda *a, **k: True)
    assert firstrun.is_configured(cfg) is False
    firstrun.save_key(cfg, "gemini", "AIza-key")
    assert firstrun.is_configured(cfg) is True


def test_tts_ready_edge_and_piper_need_no_key(tmp_path):
    assert firstrun.tts_ready(_provider_cfg(tmp_path, "anthropic", tts="edge")) is True
    assert firstrun.tts_ready(_provider_cfg(tmp_path, "anthropic", tts="piper")) is True


def test_tts_ready_cloud_voice_needs_key(tmp_path):
    cfg = _provider_cfg(tmp_path, "anthropic", tts="elevenlabs")
    assert firstrun.tts_ready(cfg) is False
    firstrun.save_key(cfg, "elevenlabs", "el-key")
    assert firstrun.tts_ready(cfg) is True


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


# ---- default input device (mic) resolution (issue #165) -----------------------------
# The mic step is optional, so a fresh install can reach finish with a blank input_device →
# PortAudio's implicit default → silence → "no speech detected". These prove the resolver
# picks a concrete, non-blank device offline (synthetic device table / stubbed sounddevice).


def test_pick_default_input_prefers_reported_default():
    """PortAudio's reported default input wins when it actually has input channels."""
    devices = [
        {"name": "Speakers", "max_input_channels": 0},
        {"name": "USB Mic", "max_input_channels": 2},
        {"name": "Webcam Mic", "max_input_channels": 1},
    ]
    assert firstrun._pick_default_input(devices, 1) == 1        # index 1 = USB Mic


def test_pick_default_input_skips_output_only_default():
    """A reported default with NO input channels (an output device) is rejected in favour of the
    first real capture device — the crux of the silent-default bug."""
    devices = [
        {"name": "Speakers", "max_input_channels": 0},   # PortAudio's default points here…
        {"name": "USB Mic", "max_input_channels": 2},     # …but this is the first real input
    ]
    assert firstrun._pick_default_input(devices, 0) == 1


def test_pick_default_input_handles_missing_or_bad_default_index():
    devices = [{"name": "Line In", "max_input_channels": 0},
               {"name": "Headset", "max_input_channels": 1}]
    assert firstrun._pick_default_input(devices, None) == 1     # no default reported
    assert firstrun._pick_default_input(devices, -1) == 1       # sentinel "no default"
    assert firstrun._pick_default_input(devices, 99) == 1       # out-of-range default


def test_pick_default_input_none_when_no_capture_devices():
    devices = [{"name": "Speakers", "max_input_channels": 0}]
    assert firstrun._pick_default_input(devices, 0) is None
    assert firstrun._pick_default_input([], 0) is None


class _FakeSD:
    """Minimal sounddevice stand-in: a device table + a `default.device` (in, out) pair."""

    def __init__(self, devices, default):
        self._devices = devices
        self.default = type("D", (), {"device": default})()

    def query_devices(self):
        return list(self._devices)


def test_resolve_default_input_device_returns_name(monkeypatch):
    """The resolver returns the NAME of the chosen capture device (never a raw index), so it
    persists the same way a user's pick does — stable across reboots."""
    fake = _FakeSD(
        [{"name": "Speakers", "max_input_channels": 0},
         {"name": "Blue Yeti", "max_input_channels": 2}],
        default=[1, 0])
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    assert firstrun.resolve_default_input_device() == "Blue Yeti"


def test_resolve_default_input_device_never_blank_when_a_mic_exists(monkeypatch):
    """Even when PortAudio's default points at an OUTPUT device, we resolve a real, non-blank
    capture device rather than leaving it to the silent implicit default (issue #165)."""
    fake = _FakeSD(
        [{"name": "Speakers", "max_input_channels": 0},
         {"name": "Headset Mic", "max_input_channels": 1}],
        default=[0, 0])                                   # default = the output-only device
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    name = firstrun.resolve_default_input_device()
    assert name == "Headset Mic"
    assert name != ""                                     # never the blank/silent default


def test_resolve_default_input_device_none_when_no_input(monkeypatch):
    fake = _FakeSD([{"name": "Speakers", "max_input_channels": 0}], default=[0, 0])
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    assert firstrun.resolve_default_input_device() is None


def test_resolve_default_input_device_fail_soft_without_portaudio(monkeypatch):
    """No audio backend (import/query raises) → None, not a crash: the system default stands."""
    class _Boom:
        def query_devices(self):
            raise RuntimeError("no PortAudio")

        default = type("D", (), {"device": [0, 0]})()

    monkeypatch.setitem(sys.modules, "sounddevice", _Boom())
    assert firstrun.resolve_default_input_device() is None


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
