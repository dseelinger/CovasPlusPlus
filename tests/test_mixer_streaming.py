"""Unit tests for the C9 mixer streaming/cancel primitives + cue routing. Offline, no device."""
from __future__ import annotations

import numpy as np

from covas.mixer import COVAS, BusMixer, float_to_pcm16, to_float_mono


def _mixer():
    return BusMixer({"audio": {"mix_sample_rate": 16000}})


def _pull(mixer, frames):
    out = np.zeros((frames, 1), dtype=np.float32)
    mixer._callback(out, frames, None, None)   # noqa: SLF001 — drive the callback synchronously
    return out[:, 0]


def test_speech_stream_feeds_and_finishes():
    mix = _mixer()
    st = mix.open_speech(COVAS, 16000)
    st.feed(float_to_pcm16(np.full(8, 0.5, dtype=np.float32)))
    st.finish()
    got = _pull(mix, 8)
    assert np.allclose(got, 0.5, atol=1e-3)
    # exhausted + finished -> the stream is dropped and marked done
    assert st.done
    assert _pull(mix, 4).tolist() == [0.0, 0.0, 0.0, 0.0]


def test_speech_stream_underrun_pads_with_silence():
    mix = _mixer()
    st = mix.open_speech(COVAS, 16000)
    st.feed(float_to_pcm16(np.full(4, 0.5, dtype=np.float32)))   # only 4 samples fed, not finished
    got = _pull(mix, 8)
    assert np.allclose(got[:4], 0.5, atol=1e-3)
    assert np.allclose(got[4:], 0.0)         # underrun -> silence, not a crash
    assert not st.done                        # still open (not finished)


def test_cancel_speech_drops_buffered_audio_immediately():
    mix = _mixer()
    st = mix.open_speech(COVAS, 16000)
    st.feed(float_to_pcm16(np.full(1000, 0.9, dtype=np.float32)))
    mix.cancel_speech()                        # barge-in
    assert st.done
    assert np.allclose(_pull(mix, 100), 0.0)   # nothing plays after cancel


def test_speech_active_tracks_live_streams_and_clears_on_cancel():
    """Barge-in relies on speech_active() reading False the instant cancel_speech() returns, so the
    mic can await confirmed silence without racing the async feeder teardown (issue #71)."""
    mix = _mixer()
    assert not mix.speech_active()             # nothing playing
    st = mix.open_speech(COVAS, 16000)
    st.feed(float_to_pcm16(np.full(1000, 0.9, dtype=np.float32)))
    assert mix.speech_active()                 # a live stream is queued
    mix.cancel_speech()
    assert not mix.speech_active()             # synchronously silent — no callback needed
    assert st.done


def test_wait_unblocks_on_finish_drain():
    mix = _mixer()
    st = mix.open_speech(COVAS, 16000)
    st.feed(float_to_pcm16(np.full(4, 0.3, dtype=np.float32)))
    st.finish()
    assert not st.wait(0)                       # not drained yet
    _pull(mix, 4)                               # mixer consumes it
    assert st.wait(0)                           # now done


def test_clear_bus_drops_pending_cue():
    mix = _mixer()
    from covas.mixer import ALERT
    mix.submit(ALERT, np.full(50, 0.5, dtype=np.float32), 16000)
    assert mix.active_sources == 1
    mix.clear_bus(ALERT)
    assert mix.active_sources == 0


def test_to_float_mono_downmixes_stereo():
    stereo = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    assert np.allclose(to_float_mono(stereo), [0.5, 0.5])
    assert np.allclose(to_float_mono(np.array([0.1, 0.2], dtype=np.float32)), [0.1, 0.2])
