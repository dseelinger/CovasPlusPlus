"""Unit tests for the multi-bus mixer (C1). Offline — no audio device is ever opened."""
from __future__ import annotations

import numpy as np
import pytest

from covas.mixer import (
    COMMS,
    COVAS,
    MUSIC,
    BusMixer,
    bus_gains,
    buses,
    dsp,
    float_to_pcm16,
    mix_buffers,
    pcm16_to_float,
    resample,
    speak_on_bus,
)
from covas.providers.fakes import FakeTTS


def _cfg(**audio) -> dict:
    return {"audio": audio}


def test_pcm16_float_roundtrip():
    x = np.array([0.0, 0.5, -0.5, 0.999], dtype=np.float32)
    back = pcm16_to_float(float_to_pcm16(x))
    assert np.allclose(back, x, atol=1e-3)
    assert pcm16_to_float(b"").shape[0] == 0


def test_load_bus_configs_defaults_and_overrides():
    cfg = _cfg(buses={"comms": {"volume_db": -9.0, "enabled": False}})
    configs = buses.load_bus_configs(cfg)
    assert set(configs) == set(buses.BUS_NAMES)
    assert configs[COVAS].volume_db == 0.0 and configs[COVAS].enabled       # default
    assert configs[COMMS].volume_db == -9.0 and not configs[COMMS].enabled  # overridden


def test_bus_gains_disabled_bus_is_silent():
    configs = buses.load_bus_configs(_cfg(buses={"music": {"enabled": False}}))
    gains = bus_gains(configs)
    assert gains[COVAS] == pytest.approx(1.0)        # 0 dB
    assert gains[MUSIC] == 0.0                       # disabled -> silent
    assert gains[COMMS] == pytest.approx(dsp.db_to_linear(-3.0))


def test_mix_buffers_sums_with_gain_and_zero_pads():
    a = np.ones(4, dtype=np.float32) * 0.4     # covas @ gain 1.0
    b = np.ones(2, dtype=np.float32) * 0.4     # comms @ gain 0.5, shorter
    out = mix_buffers([(COVAS, a), (COMMS, b)], {COVAS: 1.0, COMMS: 0.5})
    assert out.shape[0] == 4                    # padded to the longest source
    assert out[0] == pytest.approx(0.4 + 0.2)   # overlap: a + 0.5*b
    assert out[3] == pytest.approx(0.4)         # tail: only a
    assert out.dtype == np.float32


def test_mix_buffers_clips_and_handles_empty():
    loud = np.ones(3, dtype=np.float32)
    out = mix_buffers([(COVAS, loud), (COMMS, loud)], {COVAS: 1.0, COMMS: 1.0})
    assert np.max(out) <= 1.0                   # summed 2.0 -> clipped to 1.0
    assert mix_buffers([], {}).shape[0] == 0


def test_resample_changes_length_and_keeps_endpoints():
    x = dsp.tone(200.0, 0.1, 8000, amplitude=0.5)
    up = resample(x, 8000, 16000)
    assert up.shape[0] == pytest.approx(x.shape[0] * 2, abs=1)
    assert up[0] == pytest.approx(x[0], abs=1e-3)
    assert np.array_equal(resample(x, 8000, 8000), x)   # no-op when rates match


def test_submit_processes_comms_but_passes_covas_through():
    mix = BusMixer(_cfg(mix_sample_rate=16000))
    line = dsp.tone(1000.0, 0.1, 16000, amplitude=0.5)

    covas_out = mix.submit(COVAS, line, 16000)
    assert np.allclose(covas_out, line)          # clean passthrough
    comms_out = mix.submit(COMMS, line, 16000)
    assert not np.allclose(comms_out, line)      # radio-treated
    assert mix.active_sources == 2               # both queued, no device opened


def test_submit_resamples_to_mix_rate():
    mix = BusMixer(_cfg(mix_sample_rate=16000))
    line = dsp.tone(500.0, 0.1, 8000, amplitude=0.5)   # source at 8 kHz
    out = mix.submit(COVAS, line, 8000)
    assert out.shape[0] == pytest.approx(line.shape[0] * 2, abs=1)


def test_callback_mixes_and_drops_exhausted_sources():
    mix = BusMixer(_cfg(mix_sample_rate=16000))
    mix.submit(COVAS, np.ones(3, dtype=np.float32) * 0.3, 16000)
    outdata = np.zeros((4, 1), dtype=np.float32)
    mix._callback(outdata, 4, None, None)
    assert outdata[0, 0] == pytest.approx(0.3)
    assert outdata[3, 0] == 0.0                  # source was only 3 samples long
    assert mix.active_sources == 0               # exhausted source dropped


def test_speak_on_bus_uses_chosen_voice_and_bus():
    mix = BusMixer(_cfg(mix_sample_rate=16000))
    tts = FakeTTS()
    speak_on_bus(mix, tts, "incoming transmission", bus=COMMS, voice_id="npc-male-1")
    assert tts.voices_seen == ["npc-male-1"]     # the comms voice was requested
    # FakeTTS returns empty PCM -> an empty (silent) source, but it still enqueues safely.
    assert mix.active_sources == 1
