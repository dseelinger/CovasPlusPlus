"""Sound-cue playback and microphone capture.

UI cues (I8) are drop-in content, resolved by FOLDER not by config paths. Each cue TYPE is a
folder; the app plays a RANDOM file from whatever it holds, so a type can carry 1 file or 55
and users add variety just by dropping in more (no config edit, no fixed count). Two-tier
resolution, checked once at load, per type:

  1. User override — ``<data_dir>/sounds/<type>/`` — if it holds ≥1 audio file, the user's set
     REPLACES the default (predictable: overrides are all-or-nothing per type).
  2. Bundled default — ``covas/assets/cues/<type>/`` (shipped ORIGINALS) — otherwise.
  3. Neither — silence (preserve the fail-soft rule).

This replaces the old ``[sound_cues]`` explicit-path lists in config.toml. The shipped defaults
are originals we own (see ``tools/cuegen/``); ``sounds/`` stays git-ignored (user assets).
"""
from __future__ import annotations
import random
import threading
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

# Cue TYPES (folder names). Extensible — add a type here + a covas/assets/cues/<type>/ folder.
#   listen/processing/completed/failure — one-shot ticks at each turn stage.
#   thinking — a SOFT, LOOPING bed that fills the silence WHILE COVAS transcribes/thinks/searches
#     (issue #5): a one-shot tick acknowledges receipt, but nothing else fills the multi-second
#     THINKING wait, so a slow turn can read as "the AI ignored me". This loops until the reply
#     starts (or cancel/failure), stopped via stop_loop().
CUE_TYPES = ("listen", "processing", "completed", "failure", "thinking")
# play() names -> cue type. Keeps the loop's existing call sites (and intuitive synonyms) working
# after the listen/completed/failure rename.
_CUE_ALIASES = {"listening": "listen", "done": "completed", "complete": "completed",
                "failed": "failure", "fail": "failure", "working": "thinking"}
CUE_AUDIO_EXTS = (".wav", ".ogg", ".flac", ".mp3")


def _scan_cue_dir(folder: Path) -> list[str]:
    """Audio files in `folder`, deterministically ordered by filename. Missing folder -> []."""
    if not folder.is_dir():
        return []
    return [str(f) for f in sorted(folder.iterdir(), key=lambda p: p.name.lower())
            if f.is_file() and f.suffix.lower() in CUE_AUDIO_EXTS]


def resolve_cue_files(cue_type: str, *, user_base, asset_base) -> list[str]:  # noqa: ANN001 path-likes
    """Two-tier resolution for one cue type. `user_base` is the writable sounds root
    (``<data_dir>/sounds``); `asset_base` is the shipped cues root (``covas/assets/cues``).
    User override (≥1 file) REPLACES the bundled default; else the default; else [] (silent)."""
    user = _scan_cue_dir(Path(user_base) / cue_type)
    if user:
        return user
    return _scan_cue_dir(Path(asset_base) / cue_type)


def cue_roots(cfg: dict):
    """(user_base, asset_base) for cue resolution. The user override root is
    ``<content_root>/sounds`` (content_root defaults to the writable data dir; [audio].content_root
    overrides it — the same test seam the C11 drop-in pipeline uses). The asset root is the shipped
    ``covas/assets/cues`` under the read-only app dir (bundle dir when frozen)."""
    from .config import app_dir, data_dir
    base = (cfg.get("audio", {}) or {}).get("content_root") or str(data_dir())
    return Path(base) / "sounds", app_dir() / "covas" / "assets" / "cues"


_CUE_README = ("Drop your OWN cue audio for '{name}' here — {exts}. ANY file in this folder joins "
               "the rotation; a random one plays each time (1 file or 50, your call). While this "
               "folder holds ≥1 file it REPLACES the shipped default set; empty it to fall back.\n")


def ensure_cue_skeleton(user_base) -> None:  # noqa: ANN001 — a path-like (<data_dir>/sounds)
    """Create ``<data_dir>/sounds/<type>/`` + a README in each so the override location is
    discoverable (idempotent; only writes what's missing). Fail-soft — never blocks startup."""
    base = Path(user_base)
    try:
        for name in CUE_TYPES:
            d = base / name
            d.mkdir(parents=True, exist_ok=True)
            readme = d / "README.txt"
            if not readme.exists():
                readme.write_text(_CUE_README.format(name=name, exts=", ".join(CUE_AUDIO_EXTS)),
                                  encoding="utf-8")
    except OSError:
        pass


class CuePlayer:
    """Preloads cue files into memory for zero-latency playback. Each cue TYPE resolves to a
    folder (see module docstring); play() picks one file at random for variety.

    By default it opens its own sounddevice playback. When a shared BusMixer is supplied
    (C9), cues route through it on the ALERT bus so the mixer is the single device owner and
    a cue can't fight the voice stream over the device."""

    def __init__(self, cfg: dict, *, mixer=None, bus: str = "alert",
                 sleep=None) -> None:  # noqa: ANN001
        self.cues: dict[str, list[tuple[np.ndarray, int]]] = {}
        self._cfg = cfg  # kept so reload() can recompute cue_roots() without a restart (issue #109)
        self._mixer = mixer
        self._bus = bus
        self._mix_sr = int((cfg.get("audio", {}) or {}).get("mix_sample_rate", 16000))
        # Looping "thinking" bed lifecycle (issue #5). One loop at a time; guarded so start/stop
        # from the key-hook thread and the worker thread can't race. `sleep` is injectable so a
        # unit test can drive the loop without real-time waits.
        self._sleep = sleep or time.sleep
        self._loop_lock = threading.Lock()
        self._loop_thread: threading.Thread | None = None
        self._loop_stop: threading.Event | None = None
        self.reload()

    def reload(self) -> dict[str, int]:
        """Re-scan the cue folders and hot-swap the preloaded set — no restart (issue #109).
        Rebuilds a fresh ``{type: [(data, sr), …]}`` dict and ATOMICALLY rebinds ``self.cues`` to
        it (a single reference rebind is atomic in CPython, so a reader in flight — `play()` /
        `_loop_worker()`, both lock-free `.get()` reads — sees the whole old dict or the whole new
        one, never a torn one; a reader that already grabbed `(data, sr)` for the current clip is
        unaffected. No new lock needed). Returns per-type counts for a confirmation message.
        Fail-soft: a bad/missing file is skipped, an empty user folder falls back to the bundled
        default (as at startup), and a deleted folder is recreated (`ensure_cue_skeleton` is
        idempotent, so re-running it here is safe)."""
        user_base, asset_base = cue_roots(self._cfg)
        try:
            ensure_cue_skeleton(user_base)
        except Exception:  # noqa: BLE001 — skeleton creation must never block a reload
            pass
        fresh: dict[str, list[tuple[np.ndarray, int]]] = {}
        for ctype in CUE_TYPES:
            loaded = []
            for p in resolve_cue_files(ctype, user_base=user_base, asset_base=asset_base):
                try:
                    data, sr = sf.read(p, dtype="float32", always_2d=False)
                    loaded.append((data, sr))
                except Exception:  # noqa: BLE001 — a missing/bad cue must not break the reload
                    pass
            fresh[ctype] = loaded
        self.cues = fresh  # atomic rebind — see docstring
        return {k: len(v) for k, v in fresh.items()}

    def play(self, name: str, wait: bool = False) -> None:
        group = self.cues.get(_CUE_ALIASES.get(name, name)) or []
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

    def _play_once(self, data: np.ndarray, sr: int) -> float:
        """Route one preloaded cue buffer to the mixer bus (or the legacy device) and return its
        duration in seconds, so the loop can wait it out before re-triggering."""
        if self._mixer is not None:
            from .mixer import to_float_mono
            buf = to_float_mono(data)
            self._mixer.submit(self._bus, buf, sr)
            return buf.shape[0] / float(sr) if sr else 0.2
        sd.play(data, sr)
        return data.shape[0] / float(sr) if sr else 0.2

    def start_loop(self, name: str) -> None:
        """Start a SOFT looping bed (issue #5) — play the cue's files on repeat until stop_loop().
        Idempotent: a second call while a loop runs is a no-op. A cue type with no files is silent
        (no thread spawned), preserving the fail-soft rule."""
        with self._loop_lock:
            if self._loop_thread is not None and self._loop_thread.is_alive():
                return
            group = self.cues.get(_CUE_ALIASES.get(name, name)) or []
            if not group:
                return
            stop = threading.Event()
            self._loop_stop = stop
            self._loop_thread = threading.Thread(
                target=self._loop_worker, args=(name, stop), name="cue-thinking-loop",
                daemon=True)
            self._loop_thread.start()

    def _loop_worker(self, name: str, stop: threading.Event) -> None:
        """Play the cue on repeat until `stop` is set. Waits out each clip (interruptibly) before
        re-triggering, so the bed is continuous but a stop takes effect within one clip length."""
        while not stop.is_set():
            group = self.cues.get(_CUE_ALIASES.get(name, name)) or []
            if not group:
                return
            data, sr = random.choice(group)
            try:
                dur = self._play_once(data, sr)
            except Exception:  # noqa: BLE001 — a playback glitch must not kill the loop thread
                dur = 0.2
            # Interruptible wait: returns immediately once stop is set (Event.wait), so the bed
            # ends promptly on reply-start / cancel / failure rather than after a full clip.
            if self._wait_stop(stop, max(dur, 0.05)):
                return

    def _wait_stop(self, stop: threading.Event, seconds: float) -> bool:
        """Wait up to `seconds` or until `stop` is set; True if stopped. Uses the injected sleep in
        tests (so no real time passes) and the Event otherwise."""
        if self._sleep is time.sleep:
            return stop.wait(seconds)
        self._sleep(seconds)
        return stop.is_set()

    def stop_loop(self) -> None:
        """Stop the looping bed and drop its audio immediately. Safe to call when no loop runs."""
        with self._loop_lock:
            thread, stop = self._loop_thread, self._loop_stop
            self._loop_thread = None
            self._loop_stop = None
        if stop is not None:
            stop.set()
        # Drop whatever the bed already queued so it doesn't bleed over the next cue / the reply.
        if self._mixer is not None:
            self._mixer.clear_bus(self._bus)
        else:
            sd.stop()
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)

    def stop(self) -> None:
        self.stop_loop()   # a general "stop cues" also kills the looping bed (issue #5)
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
        self._drop_samples = 0  # leading samples to discard on stop() — the barge-in mute window

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

    def start(self, mute_ms: float = 0.0) -> None:
        """Open the mic. On a barge-in (``mute_ms`` > 0) the leading ``mute_ms`` of the capture is
        discarded on stop(): a belt-and-braces backstop for the residual TTS tail still draining
        from the output device's own buffer after the mixer is silenced (issue #71). It also covers
        the direct-device TTS path (no shared mixer) that the app can't synchronously stop. Kept
        small so a normal (mute_ms=0) press never clips the Commander's first word."""
        with self._lock:
            self._frames = []
            self._drop_samples = max(0, int(self.sr * mute_ms / 1000.0))

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
            audio = np.concatenate(self._frames, axis=0).flatten().astype(np.float32)
            if self._drop_samples:
                # Drop the barge-in mute window (any residual TTS tail leaking into the mic).
                audio = audio[self._drop_samples:]
                self._drop_samples = 0
            return audio
