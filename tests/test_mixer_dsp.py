"""Unit tests for the pure per-bus DSP (C1). Offline, no audio device."""
from __future__ import annotations

import numpy as np
import pytest

from covas.mixer import dsp


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(np.asarray(x, dtype=np.float64)))))


def test_db_to_linear_and_gain_scales():
    assert dsp.db_to_linear(0.0) == pytest.approx(1.0)
    assert dsp.db_to_linear(6.0206) == pytest.approx(2.0, rel=1e-3)   # +6 dB ~= x2
    assert dsp.db_to_linear(-6.0206) == pytest.approx(0.5, rel=1e-3)

    x = np.ones(16, dtype=np.float32) * 0.25
    doubled = dsp.apply_gain(x, 6.0206)
    assert doubled.dtype == np.float32
    assert np.allclose(doubled, 0.5, rtol=1e-3)


def test_bandpass_passes_inband_and_attenuates_out_of_band():
    sr = 16000
    # Drop the filter transient before measuring steady-state energy.
    skip = sr // 4
    inband = dsp.tone(1000.0, 1.0, sr, amplitude=0.5)
    low = dsp.tone(100.0, 1.0, sr, amplitude=0.5)      # below the 300 Hz floor
    high = dsp.tone(7000.0, 1.0, sr, amplitude=0.5)    # above the 3000 Hz ceiling (< Nyquist)

    r_in = _rms(dsp.bandpass(inband, sr, 300.0, 3000.0)[skip:]) / _rms(inband[skip:])
    r_low = _rms(dsp.bandpass(low, sr, 300.0, 3000.0)[skip:]) / _rms(low[skip:])
    r_high = _rms(dsp.bandpass(high, sr, 300.0, 3000.0)[skip:]) / _rms(high[skip:])

    assert r_in > 0.6            # in-band tone largely survives
    assert r_low < 0.25          # sub-band strongly attenuated
    assert r_high < 0.3          # super-band strongly attenuated
    assert r_in > r_low and r_in > r_high


def test_bandpass_empty_buffer_is_safe():
    out = dsp.bandpass(np.zeros(0, dtype=np.float32), 16000, 300.0, 3000.0)
    assert out.shape[0] == 0


def test_compress_limits_peaks_but_spares_quiet_signal():
    # Loud signal (peak 1.0) above the -12 dB (~0.251) threshold gets pushed down.
    loud = dsp.tone(440.0, 0.1, 16000, amplitude=1.0)
    out = dsp.compress(loud, threshold_db=-12.0, ratio=4.0)
    thresh = dsp.db_to_linear(-12.0)
    expected_peak = thresh + (1.0 - thresh) / 4.0
    assert np.max(np.abs(out)) < np.max(np.abs(loud))
    assert np.max(np.abs(out)) == pytest.approx(expected_peak, rel=1e-2)

    # Quiet signal entirely under the threshold is untouched.
    quiet = dsp.tone(440.0, 0.1, 16000, amplitude=0.1)
    assert np.allclose(dsp.compress(quiet, threshold_db=-12.0, ratio=4.0), quiet)

    # ratio <= 1 is a no-op.
    assert np.allclose(dsp.compress(loud, threshold_db=-12.0, ratio=1.0), loud)


def test_add_noise_is_deterministic_and_additive():
    x = np.zeros(2048, dtype=np.float32)
    a = dsp.add_noise(x, 0.05, seed=7)
    b = dsp.add_noise(x, 0.05, seed=7)
    c = dsp.add_noise(x, 0.05, seed=8)
    assert np.array_equal(a, b)          # same seed -> identical hiss
    assert not np.array_equal(a, c)      # different seed -> different hiss
    assert _rms(a) > 0.0                 # noise actually added onto silence
    assert np.array_equal(dsp.add_noise(x, 0.0, seed=7), x)   # level 0 = no-op


def test_comms_radio_preserves_length_and_alters_signal():
    sr = 16000
    x = dsp.tone(1000.0, 0.2, sr, amplitude=0.5)
    out = dsp.comms_radio(x, sr)
    assert out.shape == x.shape
    assert out.dtype == np.float32
    assert np.all(np.isfinite(out))
    assert not np.allclose(out, x)       # it's clearly processed
