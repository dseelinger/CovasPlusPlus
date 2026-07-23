"""Multi-bus audio mixer.

Two layers, split for testability (DESIGN §9):
  * `mix_buffers()` — a PURE function that sums per-bus source buffers with per-bus gain.
    No device; deterministic; the unit-tested core.
  * `BusMixer` — the realtime wrapper: an sd.OutputStream whose callback pulls from active
    per-bus source queues and mixes them. It opens a device only in start(), so the default
    test run never touches audio hardware.

Buffers are mono float32 in [-1, 1]. The int16<->float helpers bridge the raw 16-bit mono
PCM the TTS providers emit and the float domain the DSP + mix work in.
"""
from __future__ import annotations

import threading
from collections import deque

import numpy as np

from . import buses, dsp
from .buses import COMMS


def to_float_mono(data: np.ndarray) -> np.ndarray:
    """Coerce a decoded audio buffer (mono or multi-channel, any float dtype) to mono float32 —
    used to route soundfile-decoded cues through the mixer."""
    a = np.asarray(data, dtype=np.float32)
    if a.ndim == 2:
        a = a.mean(axis=1)
    return a.reshape(-1).astype(np.float32)


def pcm16_to_float(pcm: bytes) -> np.ndarray:
    """Raw little-endian 16-bit mono PCM -> float32 in [-1, 1]."""
    if not pcm:
        return np.zeros(0, dtype=np.float32)
    return (np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0)


def float_to_pcm16(x: np.ndarray) -> bytes:
    """float32 [-1, 1] -> raw little-endian 16-bit mono PCM (clipped)."""
    x = np.clip(np.asarray(x, dtype=np.float32), -1.0, 1.0)
    return (x * 32767.0).astype("<i2").tobytes()


def resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    """Linear-interpolation resample. Cheap and good enough to bring a TTS/SFX buffer onto
    the mixer's output rate; exact when the rates already match."""
    x = np.asarray(x, dtype=np.float32)
    if sr_in == sr_out or x.shape[0] == 0:
        return x.copy()
    n_out = int(round(x.shape[0] * float(sr_out) / float(sr_in)))
    if n_out <= 0:
        return np.zeros(0, dtype=np.float32)
    t_out = np.linspace(0.0, x.shape[0] - 1, n_out)
    return np.interp(t_out, np.arange(x.shape[0]), x).astype(np.float32)


def bus_gains(configs: dict[str, buses.BusConfig]) -> dict[str, float]:
    """Per-bus LINEAR gain from its config: volume_db -> factor, or 0.0 when disabled."""
    return {
        name: (dsp.db_to_linear(c.volume_db) if c.enabled else 0.0)
        for name, c in configs.items()
    }


def mix_buffers(sources, bus_gain: dict[str, float]) -> np.ndarray:
    """Sum a list of (bus_name, mono float32 buffer) sources into one output buffer, each
    scaled by its bus's linear gain. Shorter buffers are zero-padded to the longest; the
    result is clipped to [-1, 1]. Pure + deterministic — the core the realtime callback and
    the tests both exercise."""
    prepared = [(b, np.asarray(buf, dtype=np.float32)) for b, buf in sources]
    length = max((buf.shape[0] for _, buf in prepared), default=0)
    out = np.zeros(length, dtype=np.float32)
    for bus, buf in prepared:
        g = float(bus_gain.get(bus, 1.0))
        if g == 0.0 or buf.shape[0] == 0:
            continue
        out[: buf.shape[0]] += buf * g
    np.clip(out, -1.0, 1.0, out=out)
    return out


class SpeechStream:
    """A live, growable audio source on a bus — for STREAMING TTS (the COVAS reply arrives from
    the network chunk by chunk). Feed float/PCM chunks as they come, `finish()` when the source
    is exhausted, `cancel()` to drop buffered audio immediately (barge-in). `wait()` blocks the
    feeder until the mixer has drained it (or it was cancelled), so the caller keeps the existing
    'speak returns when playback is done' contract. Thread-safe: the feeder thread calls
    feed/finish/cancel/wait, the audio callback calls `_read`."""

    def __init__(self, bus: str, src_sr: int, mix_sr: int) -> None:
        self.bus = bus
        self._src_sr = int(src_sr)
        self._mix_sr = int(mix_sr)
        self._q: deque[np.ndarray] = deque()
        self._cur: np.ndarray | None = None
        self._pos = 0
        self._lock = threading.Lock()
        self._finished = False
        self._cancelled = False
        self._done = threading.Event()

    def feed(self, pcm16: bytes) -> None:
        """Append raw 16-bit mono PCM (resampled to the mix rate if needed)."""
        if not pcm16:
            return
        buf = pcm16_to_float(pcm16)
        if self._src_sr != self._mix_sr:
            buf = resample(buf, self._src_sr, self._mix_sr)
        self.feed_float(buf)

    def feed_float(self, buf: np.ndarray) -> None:
        buf = np.asarray(buf, dtype=np.float32)
        if buf.size == 0:
            return
        with self._lock:
            if self._cancelled or self._finished:
                return
            self._q.append(buf)

    def finish(self) -> None:
        with self._lock:
            self._finished = True
            if self._cur is None and not self._q:
                self._done.set()

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            self._q.clear()
            self._cur = None
            self._done.set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._done.wait(timeout)

    @property
    def done(self) -> bool:
        return self._done.is_set()

    def _read(self, frames: int) -> tuple[np.ndarray, bool]:
        """Pull up to `frames` samples (mixer-callback side). Returns (samples, done); a short
        return is an underrun (feeder behind the device) -> the mixer pads with silence."""
        out = np.zeros(frames, dtype=np.float32)
        with self._lock:
            if self._cancelled:
                return out[:0], True
            got = 0
            while got < frames:
                if self._cur is None:
                    if not self._q:
                        break
                    self._cur = self._q.popleft()
                    self._pos = 0
                take = min(frames - got, self._cur.size - self._pos)
                out[got : got + take] = self._cur[self._pos : self._pos + take]
                got += take
                self._pos += take
                if self._pos >= self._cur.size:
                    self._cur = None
            done = self._cancelled or (self._finished and self._cur is None and not self._q)
            if done:
                self._done.set()
            return out[:got], done


class BusMixer:
    """Realtime multi-bus mixer. Submit processed buffers to a bus (fire-and-forget) or open a
    streaming source; a sounddevice callback mixes all active sources with per-bus gain to the
    output device. Concurrent lines/SFX/music/speech overlap naturally.

    The device is opened only by start(); construct + submit()/open_speech() are device-free."""

    def __init__(self, cfg: dict, *, sample_rate: int | None = None, device=None) -> None:  # noqa: ANN001
        self.cfg = cfg
        self._configs = buses.load_bus_configs(cfg)
        self._gains = bus_gains(self._configs)
        audio = cfg.get("audio", {}) or {}
        self.sample_rate = int(sample_rate or audio.get("mix_sample_rate", 16000))
        self._device = device if device is not None else (audio.get("tts_output_device") or None)
        self._comms_params = buses.comms_params(cfg)
        # Each buffer source: {"bus": str, "buf": np.ndarray(float32), "pos": int}.
        self._sources: list[dict] = []
        self._streams: list[SpeechStream] = []
        self._lock = threading.Lock()
        self._stream = None

    def set_bus_config(self, cfg: dict) -> None:
        """Re-read [audio.buses]/[audio.comms] so a live settings change (bus volume/enable,
        comms treatment) takes effect without reopening the device."""
        self._configs = buses.load_bus_configs(cfg)
        self._gains = bus_gains(self._configs)
        self._comms_params = buses.comms_params(cfg)

    def open_speech(self, bus: str, sr: int) -> SpeechStream:
        """Open a streaming source on `bus` for chunked TTS. Feed it, then finish()/wait()."""
        st = SpeechStream(bus, sr, self.sample_rate)
        with self._lock:
            self._streams.append(st)
        return st

    def cancel_speech(self) -> None:
        """Barge-in: drop all in-flight streaming speech immediately (buffered audio discarded).
        Synchronous — every stream is marked cancelled/done before this returns, so the very next
        device callback outputs silence and :meth:`speech_active` reads False right away. This is
        the hard stop the app must call on barge-in (issue #71): merely setting the turn's cancel
        event only silences the feeder a chunk-read later, leaving the mixer to keep playing already
        buffered audio into an about-to-open mic."""
        with self._lock:
            streams = list(self._streams)
        for st in streams:
            st.cancel()

    def speech_active(self) -> bool:
        """True while any streaming speech source is still live (not finished/cancelled). Goes
        False immediately after :meth:`cancel_speech`, so a barge-in can briefly AWAIT confirmed
        silence before opening the mic — without racing the async feeder teardown (issue #71)."""
        with self._lock:
            return any(not st.done for st in self._streams)

    def clear_bus(self, bus: str) -> None:
        """Drop any pending buffer sources on `bus` (e.g. stop a cue). Streams are untouched."""
        with self._lock:
            self._sources = [s for s in self._sources if s["bus"] != bus]

    def submit(self, bus: str, buffer: np.ndarray, sr: int) -> np.ndarray:
        """Apply the bus's DSP, resample onto the mix rate, and enqueue for playback. Returns
        the processed buffer (handy for the demo script and tests). Device-free."""
        buf = buses.process(
            bus, buffer, sr, comms_params=self._comms_params if bus == COMMS else None
        )
        if sr != self.sample_rate:
            buf = resample(buf, sr, self.sample_rate)
        buf = np.asarray(buf, dtype=np.float32)
        with self._lock:
            self._sources.append({"bus": bus, "buf": buf, "pos": 0})
        return buf

    @property
    def active_sources(self) -> int:
        with self._lock:
            return len(self._sources)

    def _callback(self, outdata, frames, time_info, status) -> None:  # noqa: ANN001
        mix = np.zeros(frames, dtype=np.float32)
        with self._lock:
            still: list[dict] = []
            for s in self._sources:
                buf = s["buf"]
                pos = s["pos"]
                chunk = buf[pos : pos + frames]
                g = self._gains.get(s["bus"], 1.0)
                if chunk.shape[0] > 0 and g != 0.0:
                    mix[: chunk.shape[0]] += chunk * g
                s["pos"] = pos + frames
                if s["pos"] < buf.shape[0]:
                    still.append(s)
            self._sources = still
            live: list[SpeechStream] = []
            for st in self._streams:
                chunk, finished = st._read(frames)
                g = self._gains.get(st.bus, 1.0)
                if chunk.size > 0 and g != 0.0:
                    mix[: chunk.size] += chunk * g
                if not finished:
                    live.append(st)
            self._streams = live
        np.clip(mix, -1.0, 1.0, out=mix)
        outdata[:, 0] = mix

    def start(self) -> None:
        """Open the output device and begin mixing. Import sounddevice lazily so the default
        (device-free) test run never needs it."""
        if self._stream is not None:
            return
        import sounddevice as sd

        self._stream = sd.OutputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            device=self._device,
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> None:
        self.cancel_speech()
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        with self._lock:
            self._sources = []
            self._streams = []


def speak_on_bus(mixer: BusMixer, provider, text: str, *, bus: str = COMMS, voice_id=None) -> np.ndarray:  # noqa: ANN001
    """Render `text` with the chosen voice via the TTS provider, then play it on `bus` (which
    applies that bus's DSP — e.g. the comms radio treatment). This is how comms/alert lines
    get a non-COVAS voice and a filtered character without a separate audio path; COVAS's own
    replies keep their existing direct tts.speak() path unchanged. Returns the processed
    buffer; an empty synth simply yields a silent (zero-length) source."""
    pcm, sr = provider.synth_pcm(text, voice_id=voice_id)
    return mixer.submit(bus, pcm16_to_float(pcm), sr)
