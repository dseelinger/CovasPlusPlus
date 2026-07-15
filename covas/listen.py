"""Hands-free continuous listening — voice-activity detection (issue #63).

A no-PTT input mode: a voice-activity gate watches the mic, opens a capture window
on speech onset, closes it after trailing silence, and hands the captured utterance
to the SAME dispatch push-to-talk uses (transcribe locally → LLM → TTS, barge-in and
cancellation preserved). PTT stays the default; continuous mode is opt-in.

The design SPLITS pure logic from the mic thread so the interesting part is testable
offline with zero audio hardware:

* ``VadGate`` — a pure state machine fed one frame *energy* at a time. It decides
  SILENCE→SPEECH (onset above threshold, debounced) and SPEECH→SILENCE (a trailing
  "hangover" of silence ends the utterance), and rejects blips shorter than a minimum
  speech duration as noise. It has no audio device, no threads, and no real clock — it
  advances purely on the frames you push, so a synthetic energy sequence exercises every
  branch. This is the unit-tested core.

* ``VadListener`` — a thin daemon thread that opens the real mic, slices it into
  fixed-length frames, computes each frame's RMS energy, feeds ``VadGate``, and buffers
  the audio (with a little pre-roll so the first phoneme isn't clipped). On a confirmed
  onset it fires ``on_speech_start`` (the app's barge-in path); on utterance end it fires
  ``on_utterance`` with the captured audio (the app's dispatch). This is the on-hardware
  part — it is documented in MANUAL_TESTS and is NOT exercised by the offline suite.

Stdlib + numpy only (no new dependency): energy VAD is enough for a "did a human start
talking" gate, and it keeps the whole listen path local — no cloud burn.
"""
from __future__ import annotations

import enum
import threading
from collections import deque
from dataclasses import dataclass

import numpy as np


class VadEvent(enum.Enum):
    """What a single :meth:`VadGate.push` decided about the stream so far."""

    NONE = "none"                    # nothing changed — keep going
    SPEECH_START = "speech_start"    # onset confirmed: begin capture + barge in
    SPEECH_END = "speech_end"        # utterance complete: dispatch the captured audio
    NOISE_REJECTED = "noise_rejected"  # onset fired but stayed under min-speech: discard


@dataclass(frozen=True)
class VadTuning:
    """Tunables for the energy gate. Times are in milliseconds; ``energy_threshold``
    is compared against a frame's RMS amplitude (float32 audio in roughly [-1, 1]),
    so it's a small linear value. All are exposed as settings (``[listen]``)."""

    frame_ms: float = 30.0            # duration one pushed frame represents (the time unit)
    energy_threshold: float = 0.02    # RMS at/above which a frame counts as speech
    start_ms: float = 120.0           # voiced run needed to CONFIRM onset (debounces clicks)
    min_speech_ms: float = 250.0      # minimum voiced duration for a real utterance
    hangover_ms: float = 700.0        # trailing silence that ENDS an utterance


class VadGate:
    """Pure SILENCE⇄SPEECH state machine driven by per-frame energy.

    Feed it one frame energy per :meth:`push`; it returns a :class:`VadEvent`. No audio,
    no threads, no wall clock — time is measured purely by counting frames of
    ``tuning.frame_ms`` each, which is exactly what makes it unit-testable with a
    synthetic sequence. Not thread-safe; the listener owns one gate on its own thread.
    """

    def __init__(self, tuning: VadTuning | None = None) -> None:
        self.t = tuning or VadTuning()
        self.reset()

    def reset(self) -> None:
        """Return to SILENCE and clear all running counters."""
        self._in_speech = False
        self._onset_run_ms = 0.0     # consecutive voiced ms while still in SILENCE
        self._voiced_ms = 0.0        # total voiced ms accrued in the current utterance
        self._silence_run_ms = 0.0   # consecutive silent ms while in SPEECH (the hangover)

    @property
    def in_speech(self) -> bool:
        """True between a SPEECH_START and its SPEECH_END/NOISE_REJECTED."""
        return self._in_speech

    def push(self, energy: float) -> VadEvent:
        """Advance the machine by one frame of the given RMS energy."""
        fm = self.t.frame_ms
        speech = energy >= self.t.energy_threshold
        if not self._in_speech:
            # SILENCE: count a run of voiced frames; confirm onset once it's long enough
            # so a single click or key clack can't open a capture window.
            if speech:
                self._onset_run_ms += fm
                if self._onset_run_ms >= self.t.start_ms:
                    self._in_speech = True
                    self._voiced_ms = self._onset_run_ms  # the debounce run IS speech
                    self._silence_run_ms = 0.0
                    return VadEvent.SPEECH_START
            else:
                self._onset_run_ms = 0.0
            return VadEvent.NONE

        # SPEECH: accumulate voiced time; a long enough silent tail ends the utterance.
        if speech:
            self._voiced_ms += fm
            self._silence_run_ms = 0.0
        else:
            self._silence_run_ms += fm
            if self._silence_run_ms >= self.t.hangover_ms:
                voiced = self._voiced_ms
                self.reset()
                # Enough real speech = a genuine utterance; otherwise it was a blip we
                # opened on but which never became words — reject it as noise.
                if voiced >= self.t.min_speech_ms:
                    return VadEvent.SPEECH_END
                return VadEvent.NOISE_REJECTED
        return VadEvent.NONE


def tuning_from_cfg(cfg: dict) -> VadTuning:
    """Build a :class:`VadTuning` from the ``[listen]`` config block, falling back to
    the dataclass defaults for anything missing so a partial config can't break it."""
    d = VadTuning()
    listen = (cfg or {}).get("listen", {}) or {}
    return VadTuning(
        frame_ms=float(listen.get("frame_ms", d.frame_ms)),
        energy_threshold=float(listen.get("energy_threshold", d.energy_threshold)),
        start_ms=float(listen.get("start_ms", d.start_ms)),
        min_speech_ms=float(listen.get("min_speech_ms", d.min_speech_ms)),
        hangover_ms=float(listen.get("hangover_ms", d.hangover_ms)),
    )


def frame_rms(frame: np.ndarray) -> float:
    """RMS amplitude of a mono float32 frame — the energy the gate compares. Empty or
    non-finite frames read as silence so a glitchy buffer can't spuriously trigger."""
    if frame.size == 0:
        return 0.0
    val = float(np.sqrt(np.mean(np.square(frame, dtype=np.float64))))
    return val if np.isfinite(val) else 0.0


class VadListener:
    """Daemon mic thread that drives a :class:`VadGate` and fires app callbacks.

    On a confirmed onset it calls ``on_speech_start()`` (the app barges in + goes to
    Listening); on utterance end it calls ``on_utterance(audio)`` with a mono float32
    ``np.ndarray`` — the same shape ``Recorder.stop()`` returns — for the app to dispatch
    down the existing transcribe→LLM→TTS path. Fail-soft throughout: any mic/VAD error is
    logged and never propagates, so continuous mode can't crash the voice loop (the app
    falls back to PTT if the listener won't start).

    ``sounddevice`` is imported lazily inside :meth:`start` so importing this module (and
    the pure ``VadGate`` tests) needs no audio backend. A ``stream_factory`` seam lets a
    test inject a fake stream, but the default offline suite only touches ``VadGate``.
    """

    def __init__(
        self,
        cfg: dict,
        *,
        on_speech_start,
        on_utterance,
        log=None,
        gate: VadGate | None = None,
        stream_factory=None,
    ) -> None:
        self.cfg = cfg
        self._on_speech_start = on_speech_start
        self._on_utterance = on_utterance
        self._log = log or (lambda _m: None)
        self.sr = int(cfg["audio"]["sample_rate"])
        self.tuning = gate.t if gate is not None else tuning_from_cfg(cfg)
        self.gate = gate or VadGate(self.tuning)
        self._stream_factory = stream_factory
        # Frame length in samples; a captured utterance is a list of these frames.
        self._frame_len = max(1, int(round(self.sr * self.tuning.frame_ms / 1000.0)))
        # Pre-roll: keep the last few frames so a captured utterance includes the audio
        # from just before onset was confirmed (the debounce run) — no clipped first word.
        pre_roll_frames = max(1, int(round(self.tuning.start_ms / self.tuning.frame_ms)) + 2)
        self._preroll: deque[np.ndarray] = deque(maxlen=pre_roll_frames)
        self._capture: list[np.ndarray] | None = None
        self._stream = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ---- lifecycle --------------------------------------------------------
    def start(self) -> bool:
        """Open the mic and begin listening. Returns True on success; on any failure it
        logs, cleans up, and returns False so the caller can fall back to PTT."""
        if self._thread is not None and self._thread.is_alive():
            return True
        self._stop.clear()
        self.gate.reset()
        self._preroll.clear()
        self._capture = None
        try:
            self._stream = self._open_stream()
            self._stream.start()
        except Exception as e:  # noqa: BLE001 — mic open must never crash the loop
            self._log(f"continuous listen failed to open the mic: {e}; staying on PTT.")
            self._stream = None
            return False
        self._thread = threading.Thread(
            target=self._run, name="vad-listener", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        """Stop listening and close the mic. Safe to call when already stopped."""
        self._stop.set()
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:  # noqa: BLE001 — teardown must never raise
                pass
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ---- internals --------------------------------------------------------
    def _open_stream(self):
        if self._stream_factory is not None:
            return self._stream_factory(self._on_frame)
        import sounddevice as sd  # lazy: keeps module import (and the gate tests) audio-free

        device = self._resolve_device(self.cfg["audio"]["input_device"])
        return sd.InputStream(
            samplerate=self.sr, channels=1, dtype="float32",
            device=device, blocksize=self._frame_len, callback=self._sd_callback,
        )

    @staticmethod
    def _resolve_device(name):
        """Resolve a mic name/index to a device index — mirrors Recorder._resolve so the
        listener honours the same ``[audio].input_device`` setting."""
        if name is None or name == "":
            return None
        if isinstance(name, int):
            return name
        import sounddevice as sd  # lazy

        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0 and str(name) in d["name"]:
                return i
        return None

    def _sd_callback(self, indata, frames, time_info, status):  # noqa: ANN001
        # Runs on PortAudio's thread: keep it light — copy the frame and hand it off.
        self._on_frame(indata.copy())

    def _run(self) -> None:
        # The listener thread is otherwise event-driven from the audio callback; it just
        # waits here until stopped so the object owns a joinable thread handle.
        self._stop.wait()

    def _on_frame(self, frame: np.ndarray) -> None:
        """Feed one mic frame through the gate and manage the capture buffer. Fail-soft:
        a bad frame is dropped, never raised, so the audio callback can't kill the stream."""
        if self._stop.is_set():
            return
        try:
            mono = np.asarray(frame, dtype=np.float32).reshape(-1)
            event = self.gate.push(frame_rms(mono))
            if not self.gate.in_speech and self._capture is None:
                # idle: remember recent audio for pre-roll
                self._preroll.append(mono)
            if event is VadEvent.SPEECH_START:
                # Seed capture with the pre-roll so the utterance keeps its opening.
                self._capture = list(self._preroll)
                self._capture.append(mono)
                self._preroll.clear()
                self._safe(self._on_speech_start)
            elif self.gate.in_speech and self._capture is not None:
                self._capture.append(mono)
            elif event is VadEvent.SPEECH_END and self._capture is not None:
                audio = np.concatenate(self._capture).astype(np.float32)
                self._capture = None
                self._safe(self._on_utterance, audio)
            elif event is VadEvent.NOISE_REJECTED:
                self._capture = None  # blip — throw it away, keep listening
        except Exception as e:  # noqa: BLE001 — never let a frame error break the mic
            self._log(f"listen frame error: {e}")
            self._capture = None

    def _safe(self, fn, *args) -> None:
        try:
            fn(*args)
        except Exception as e:  # noqa: BLE001 — a callback error must not stop listening
            self._log(f"listen callback error: {e}")
