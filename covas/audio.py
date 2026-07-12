"""Sound-cue playback and microphone capture."""
from __future__ import annotations
import random
import threading
import time
import numpy as np
import sounddevice as sd
import soundfile as sf


class CuePlayer:
    """Preloads cue files into memory for zero-latency playback. Each cue can be a
    single file or a list of files — play() picks one at random for variety.

    By default it opens its own sounddevice playback. When a shared BusMixer is supplied
    (C9), cues route through it on the ALERT bus so the mixer is the single device owner and
    a cue can't fight the voice stream over the device."""

    def __init__(self, cfg: dict, *, mixer=None, bus: str = "alert") -> None:  # noqa: ANN001
        self.cues: dict[str, list[tuple[np.ndarray, int]]] = {}
        self._mixer = mixer
        self._bus = bus
        self._mix_sr = int((cfg.get("audio", {}) or {}).get("mix_sample_rate", 16000))
        sc = cfg.get("sound_cues", {})
        for name in ("listening", "processing", "done", "failed"):
            entry = sc.get(name)
            paths = entry if isinstance(entry, list) else ([entry] if entry else [])
            loaded = []
            for p in paths:
                try:
                    data, sr = sf.read(p, dtype="float32", always_2d=False)
                    loaded.append((data, sr))
                except Exception:  # noqa: BLE001 — a missing cue must not break startup
                    pass
            self.cues[name] = loaded

    def play(self, name: str, wait: bool = False) -> None:
        group = self.cues.get(name) or []
        if not group:
            return
        data, sr = random.choice(group)
        if self._mixer is not None:
            from .mixer import to_float_mono
            buf = to_float_mono(data)
            self._mixer.submit(self._bus, buf, sr)
            if wait:
                # No device to block on; sleep the cue's (resampled) duration so the "done"
                # cue still finishes before speech starts.
                dur = buf.shape[0] / float(sr) if sr else 0.0
                if dur > 0:
                    time.sleep(min(dur, 3.0))
            return
        sd.play(data, sr)
        if wait:
            sd.wait()

    def stop(self) -> None:
        if self._mixer is not None:
            self._mixer.clear_bus(self._bus)
            return
        sd.stop()


class Recorder:
    """Captures mono 16 kHz audio from the configured mic while PTT is held."""

    def __init__(self, cfg: dict) -> None:
        self.sr = int(cfg["audio"]["sample_rate"])
        self.device = self._resolve(cfg["audio"]["input_device"])
        self._frames: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._lock = threading.Lock()

    @staticmethod
    def _resolve(name) -> int | None:
        if name is None or name == "":
            return None
        if isinstance(name, int):
            return name
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0 and str(name) in d["name"]:
                return i
        return None

    def start(self) -> None:
        with self._lock:
            self._frames = []

            def cb(indata, frames, time_info, status):  # noqa: ANN001
                self._frames.append(indata.copy())

            self._stream = sd.InputStream(
                samplerate=self.sr, channels=1, dtype="float32",
                device=self.device, callback=cb,
            )
            self._stream.start()

    def stop(self) -> np.ndarray:
        with self._lock:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
                self._stream = None
            if not self._frames:
                return np.zeros(0, dtype=np.float32)
            return np.concatenate(self._frames, axis=0).flatten().astype(np.float32)
