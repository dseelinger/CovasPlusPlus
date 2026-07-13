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
CUE_TYPES = ("listen", "processing", "completed", "failure")
# play() names -> cue type. Keeps the loop's existing call sites (and intuitive synonyms) working
# after the listen/completed/failure rename.
_CUE_ALIASES = {"listening": "listen", "done": "completed", "complete": "completed",
                "failed": "failure", "fail": "failure"}
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

    def __init__(self, cfg: dict, *, mixer=None, bus: str = "alert") -> None:  # noqa: ANN001
        self.cues: dict[str, list[tuple[np.ndarray, int]]] = {}
        self._mixer = mixer
        self._bus = bus
        self._mix_sr = int((cfg.get("audio", {}) or {}).get("mix_sample_rate", 16000))
        user_base, asset_base = cue_roots(cfg)
        try:
            ensure_cue_skeleton(user_base)
        except Exception:  # noqa: BLE001 — skeleton creation must never block startup
            pass
        for ctype in CUE_TYPES:
            loaded = []
            for p in resolve_cue_files(ctype, user_base=user_base, asset_base=asset_base):
                try:
                    data, sr = sf.read(p, dtype="float32", always_2d=False)
                    loaded.append((data, sr))
                except Exception:  # noqa: BLE001 — a missing/bad cue must not break startup
                    pass
            self.cues[ctype] = loaded

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
