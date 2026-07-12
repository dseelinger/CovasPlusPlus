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
from typing import Optional

import numpy as np

from . import buses, dsp
from .buses import COMMS


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


class BusMixer:
    """Realtime multi-bus mixer. Submit processed buffers to a bus; a sounddevice callback
    mixes all active sources with per-bus gain to the output device. Sources play once and
    are dropped when exhausted (fire-and-forget), so concurrent lines/SFX overlap naturally.

    The device is opened only by start(); construct + submit() are device-free (tests)."""

    def __init__(self, cfg: dict, *, sample_rate: Optional[int] = None, device=None) -> None:  # noqa: ANN001
        self.cfg = cfg
        self._configs = buses.load_bus_configs(cfg)
        self._gains = bus_gains(self._configs)
        audio = cfg.get("audio", {}) or {}
        self.sample_rate = int(sample_rate or audio.get("mix_sample_rate", 16000))
        self._device = device if device is not None else (audio.get("tts_output_device") or None)
        self._comms_params = buses.comms_params(cfg)
        # Each source: {"bus": str, "buf": np.ndarray(float32), "pos": int}.
        self._sources: list[dict] = []
        self._lock = threading.Lock()
        self._stream = None

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
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        with self._lock:
            self._sources = []


def speak_on_bus(mixer: BusMixer, provider, text: str, *, bus: str = COMMS, voice_id=None) -> np.ndarray:  # noqa: ANN001
    """Render `text` with the chosen voice via the TTS provider, then play it on `bus` (which
    applies that bus's DSP — e.g. the comms radio treatment). This is how comms/alert lines
    get a non-COVAS voice and a filtered character without a separate audio path; COVAS's own
    replies keep their existing direct tts.speak() path unchanged. Returns the processed
    buffer; an empty synth simply yields a silent (zero-length) source."""
    pcm, sr = provider.synth_pcm(text, voice_id=voice_id)
    return mixer.submit(bus, pcm16_to_float(pcm), sr)
