"""Audio layer composition + runtime controls (C9).

`AudioLayer` is the composition root for the whole C1–C8 stack: it constructs the cue registry
(chatter + SFX), the governor + eligibility engine + driver, the comms gate→voicer, the music
director, and the interdiction cue over ONE shared BusMixer, and routes bus `ed_event`s to the
right consumer. `AudioControlsCapability` exposes the voice controls (mute chatter, music
up/down/stop, quiet comms, mute all) with full help metadata, flipping the same runtime state
the settings do. Everything is fail-soft — a dead component logs and is skipped, never breaking
the voice loop.

The LLM only ever produces text that is validated (comms variant validator / chatter fact gate)
then routed; it is never in the realtime audio path.
"""
from __future__ import annotations

import threading
import time
from dataclasses import replace
from typing import Callable, Optional

from ..capabilities.base import HelpMeta, Slot
from .buses import ALERT, AMBIENT, COMMS, COVAS, MUSIC
from .chatter import ChatterPlayer, chatter_cues
from .comms import capture
from .content import (
    ContentBundle,
    content_status,
    empty_bundle,
    merged_music_library,
    overlay_cues,
    status_summary,
    threat_lines,
)
from .cues import CueRegistry
from .driver import CueDriver
from .eligibility import EligibilityEngine
from .example_cues import DEFAULT_THREAT_LINES, InterdictionCue, SfxPlayer, sfx_cues
from .governor import CueGovernor, GovernorConfig
from .mixer import pcm16_to_float, speak_on_bus
from .music import MusicDirector
from .variants import CommsVoicer, make_variant_generator
from .voices import VoiceCast, build_cast


def _default_sting() -> str:
    """The shipped ORIGINAL interdiction sting (I8), used when the user supplies none. Returns ""
    if the asset is missing (frozen build without it) so the cue stays fail-soft/silent."""
    from ..config import app_dir
    p = app_dir() / "covas" / "assets" / "cues" / "interdiction_sting" / "interdiction_sting.wav"
    return str(p) if p.is_file() else ""


def _text_generator(llm, model: Optional[str]):  # noqa: ANN001
    """Adapt an LLMProvider to `generate(prompt) -> str` (used for chatter flavor musings)."""
    if llm is None:
        return None

    def generate(prompt: str) -> str:
        parts: list[str] = []
        for kind, chunk in llm.stream_reply(
            [{"role": "user", "content": prompt}], threading.Event(), lambda *_a: None,
            model=model, max_tokens=60,
        ):
            if kind == "text":
                parts.append(chunk)
        return "".join(parts).strip()

    return generate


class AudioLayer:
    """Owns the composed audio components and routes events + controls. Does NOT own the mixer's
    device lifecycle (the app starts/stops the shared mixer, since COVAS speech uses it too)."""

    def __init__(
        self,
        cfg: dict,
        mixer,  # noqa: ANN001 — BusMixer
        tts,  # noqa: ANN001 — TTSProvider (for comms/interdiction lines)
        *,
        ed_ctx=None,  # noqa: ANN001 — EDContext (fuel %); also the driver's context
        llm=None,  # noqa: ANN001 — for comms variants + chatter flavor (cheap tier); None = pool/verbatim only
        cheap_model: Optional[str] = None,
        cast_synth: Optional[Callable] = None,  # noqa: ANN001 — (Voice, text) -> (pcm, sr)
        content: Optional[ContentBundle] = None,
        clock: Callable[[], float] = time.monotonic,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.cfg = cfg
        self.mixer = mixer
        self.tts = tts
        self._ed_ctx = ed_ctx
        self._log = log or (lambda _m: None)
        # Drop-in content (C11): dropped-in SFX/music/line files overlay the shipped defaults. An
        # empty bundle = no drop-in content (cues keep their built-in pools / config samples).
        self._content = content if content is not None else empty_bundle()

        # Voice cast (C10): assign a distinct, stable voice to each comms/chatter speaker and route
        # it to the right provider (the injected synth). When the app doesn't supply one, default
        # to a synth through the app's own TTS provider (today's single-voice behaviour).
        self._cast_synth = cast_synth or (
            lambda voice, text: self.tts.synth_pcm(text, voice.ref or None))
        self._cast: VoiceCast = build_cast(cfg, synth=self._cast_synth)

        audio = cfg.get("audio", {}) or {}
        # Runtime toggles, seeded from config. The governor's own `enabled` ([audio.cues]) gates
        # chatter+SFX at the throttle level; these add per-category voice/settings control.
        self.chatter_on = bool((audio.get("cues", {}) or {}).get("enabled", False))
        self.sfx_on = self.chatter_on
        self.comms_on = bool((audio.get("comms", {}) or {}).get("enabled", True))
        self.music_on = bool((cfg.get("music", {}) or {}).get("enabled", False))
        self.muted = False

        # Cues: chatter + SFX in one registry the driver governs, with drop-in content overlaid
        # (folder SFX samples / chatter line files replace the shipped defaults when present).
        self._registry = CueRegistry()
        for cue in overlay_cues(list(chatter_cues()) + list(sfx_cues(cfg)), self._content):
            self._registry.register(cue)
        # The governor only THROTTLES (cooldowns + global rate) here — the audio layer's own
        # per-category flags are the on/off gates, so force it enabled (its [audio.cues].enabled
        # in config maps to the chatter/SFX toggle, not to whether comms may play).
        self._governor = CueGovernor(replace(GovernorConfig.from_cfg(cfg), enabled=True),
                                     clock=clock)
        self._engine = EligibilityEngine()
        # LLM use in the ambient layer is OFF by default (cost): fact-bearing chatter and NPC
        # comms are pool/verbatim unless explicitly opted in. Flavor chatter / comms variants
        # only wire the LLM when their flag is set.
        chatter_flavor = bool((audio.get("cues", {}) or {}).get("flavor", False))
        comms_variants = bool((audio.get("comms", {}) or {}).get("variants", False))
        chatter_gen = _text_generator(llm, cheap_model) if (llm is not None and chatter_flavor) else None
        self._chatter = ChatterPlayer(self._speak_bus, generate=chatter_gen)
        self._sfx = SfxPlayer(self._play_sample)
        self._driver = CueDriver(self._registry, self._engine, self._governor,
                                 self._dispatch_play, context=ed_ctx, clock=clock)

        # Comms: gate (C4) -> voicer (C5), sharing the governor for dedup + the global budget.
        comms_gen = (make_variant_generator(llm, model=cheap_model)
                     if (llm is not None and comms_variants) else None)
        self._comms = CommsVoicer(self._comms_play, generate=comms_gen,
                                  governor=self._governor, clock=clock)

        # Music (C7): config tracks + dropped-in audio/music/<context> files.
        self._music = MusicDirector(merged_music_library(cfg, self._content),
                                    enabled=self.music_on)
        # Interdiction (C8): drop-in sting sample set + threat pool file override the defaults.
        # With no user sting (config path or drop-in folder), fall back to the shipped ORIGINAL
        # sting bundled under covas/assets/cues/interdiction_sting/ (I8) instead of silence.
        idn = (audio.get("interdiction", {}) or {})
        self._interdiction = InterdictionCue(
            self._emit, governor=self._governor, clock=clock,
            enabled=bool(idn.get("enabled", False)),
            sting=str(idn.get("sting", "")) or _default_sting(),
            sting_samples=tuple(self._content.sfx.get("interdiction_sting", [])),
            threat_lines=threat_lines(self._content, DEFAULT_THREAT_LINES))

        self._log(status_summary(self._content))

    def content_status(self) -> list[dict]:
        """Per cue/context/pool: how many files/lines were found and whether it's silent — so the
        web/settings readout can show what still needs content."""
        return content_status(self._content)

    # -- fuel for the driver ----------------------------------------------------
    def _fuel(self) -> Optional[float]:
        if self._ed_ctx is None:
            return None
        try:
            return self._ed_ctx.fuel_pct()
        except Exception:  # noqa: BLE001
            return None

    # -- routing helpers (all fail soft) ----------------------------------------
    def _submit_voice(self, voice, text: str, bus: str) -> bool:  # noqa: ANN001 — a Voice
        """Synthesize `text` with a cast Voice (routed to its provider) and play it on `bus`."""
        try:
            pcm, sr = self._cast.synth(voice, text)
            self.mixer.submit(bus, pcm16_to_float(pcm), sr)
            return True
        except Exception as e:  # noqa: BLE001
            self._log(f"voice synth/play failed: {e}")
            return False

    def _speak_bus(self, text: str, bus: str) -> bool:
        """Speak a chatter line on its bus with a stable chatter cast voice."""
        return self._submit_voice(self._cast.assign("chatter"), text, bus)

    def _comms_play(self, text: str, record) -> bool:  # noqa: ANN001 — a VoiceableComms
        """Voice a gated comms line: the cast picks the voice from the sender identity (player
        DMs get the fixed player voice), routed to its provider on the radio-treated comms bus."""
        return self._submit_voice(self._cast.for_record(record), text, COMMS)

    def _play_sample(self, sample: str, bus: str) -> bool:
        try:
            import soundfile as sf

            from .mixer import to_float_mono
            data, sr = sf.read(sample, dtype="float32", always_2d=False)
            self.mixer.submit(bus, to_float_mono(data), sr)
            return True
        except Exception as e:  # noqa: BLE001 — a missing SFX asset just means silence
            self._log(f"sfx load failed ({sample}): {e}")
            return False

    def _dispatch_play(self, cue) -> bool:  # noqa: ANN001 — the driver's play callback
        if self.muted:
            return False
        if cue.samples:
            return self._sfx(cue) if self.sfx_on else False
        if cue.phrasings:
            return self._chatter(cue) if self.chatter_on else False
        return False

    def _emit(self, layer) -> bool:  # noqa: ANN001 — an interdiction Layer
        if self.muted:
            return False
        if layer.kind == "sfx":
            return self._play_sample(layer.payload, layer.bus)
        if layer.bus == COVAS:
            # The assistant's own threat line -> the persona voice on the clean bus.
            try:
                speak_on_bus(self.mixer, self.tts, layer.payload, bus=COVAS)
                return True
            except Exception as e:  # noqa: BLE001
                self._log(f"interdiction covas line failed: {e}")
                return False
        # The pirate's line -> a cast voice (male) on the radio-treated comms bus.
        hint = layer.voice if layer.voice in ("male", "female") else None
        return self._submit_voice(self._cast.assign("pirate", gender_hint=hint),
                                  layer.payload, layer.bus)

    def _realize_music(self, transition) -> None:  # noqa: ANN001 — MusicTransition or None
        if transition is None:
            return
        self._play_sample(transition.to_track, MUSIC)
        self._log(f"music -> {transition.context}: {transition.to_track}")

    # -- the bus hook -----------------------------------------------------------
    def on_event(self, event: dict) -> None:
        """Route one bus `ed_event`. Comms lines go to the gate->voicer; Interdiction/UnderAttack
        fire the layered cue; every event updates the eligibility engine, then chatter/SFX and
        music react to the new state. Never raises."""
        try:
            if not isinstance(event, dict) or event.get("type") != "ed_event":
                return  # only real game events drive the audio layer, not log/status/usage
            name = event.get("event")
            if name == "ReceiveText":
                if self.comms_on and not self.muted:
                    rec = capture(event)
                    if rec is not None and rec.voiceable:
                        self._comms.voice(rec)
                return
            if name in ("Interdiction", "UnderAttack") and not self.muted:
                self._interdiction.on_event(event)
            # State-driven layers.
            self._engine.note_event(event)
            if self.muted:
                return
            if self.music_on:
                states = self._engine.states(fuel_pct=self._fuel())
                self._realize_music(self._music.update(states))
            if self.chatter_on or self.sfx_on:
                self._driver.tick()
        except Exception as e:  # noqa: BLE001 — the audio layer must never take down the pump
            self._log(f"audio layer error: {e}")

    # -- runtime controls (voice + settings both call these) --------------------
    def set_muted(self, on: bool) -> bool:
        self.muted = bool(on)
        if self.muted:
            for b in (COMMS, AMBIENT, MUSIC, ALERT):
                self.mixer.clear_bus(b)
        return self.muted

    def set_chatter(self, on: bool) -> bool:
        self.chatter_on = bool(on)
        return self.chatter_on

    def set_sfx(self, on: bool) -> bool:
        self.sfx_on = bool(on)
        return self.sfx_on

    def set_comms(self, on: bool) -> bool:
        self.comms_on = bool(on)
        if not self.comms_on:
            self.mixer.clear_bus(COMMS)
        return self.comms_on

    def set_music(self, on: bool) -> bool:
        self.music_on = bool(on)
        self._music._enabled = self.music_on  # noqa: SLF001 — director enable mirrors the toggle
        if not self.music_on:
            self.mixer.clear_bus(MUSIC)
        return self.music_on

    def bump_music_volume(self, delta_db: float) -> float:
        """Nudge the music bus volume (dB), clamped to a sane range, and apply it live."""
        m = self.cfg.setdefault("audio", {}).setdefault("buses", {}).setdefault("music", {})
        cur = float(m.get("volume_db", -12.0))
        new = max(-40.0, min(6.0, cur + float(delta_db)))
        m["volume_db"] = new
        self.mixer.set_bus_config(self.cfg)
        return new

    def rebuild_cast(self, el_voices=None) -> None:  # noqa: ANN001 — list[dict] of EL voices
        """Rebuild the cast from current config, reusing the synth backends. `el_voices` (the
        famous-filtered live list) feeds the exclusion hook so an unusable voice is dropped."""
        self._cast = build_cast(self.cfg, synth=self._cast_synth, el_voices=el_voices)

    def apply_settings(self) -> None:
        """Re-read config after a settings change: bus volumes/treatment, enable toggles, and the
        voice cast (cast provider / pool / player voice), reusing the existing synth backends."""
        self.mixer.set_bus_config(self.cfg)
        self.rebuild_cast()
        audio = self.cfg.get("audio", {}) or {}
        self.chatter_on = bool((audio.get("cues", {}) or {}).get("enabled", False))
        self.sfx_on = self.chatter_on
        self.set_comms(bool((audio.get("comms", {}) or {}).get("enabled", True)))
        self.set_music(bool((self.cfg.get("music", {}) or {}).get("enabled", False)))
        self._interdiction.set_enabled(
            bool((audio.get("interdiction", {}) or {}).get("enabled", False)))


# ---- voice controls -------------------------------------------------------------------------
_AUDIO_TOOLS = [
    {
        "name": "control_ambient_audio",
        "description": (
            "Turn the atmospheric AUDIO LAYER on or off by part: the ambient SPACE CHATTER, the "
            "COMMS voices (NPC/station/player radio), the ambient MUSIC (louder/quieter/off/on), "
            "or ALL ambient audio at once. Use when the Commander says things like 'mute the "
            "chatter', 'quiet the comms', 'turn the music up', 'turn the music down', 'stop the "
            "music', 'silence all the background audio', or 'turn the ambient audio back on'. This "
            "does NOT affect your own spoken replies — only the background/atmosphere layer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string",
                           "enum": ["chatter", "comms", "music", "all"],
                           "description": "Which part of the ambient audio to control."},
                "action": {"type": "string",
                           "enum": ["on", "off", "up", "down"],
                           "description": "on/off for any target; up/down adjust music volume."},
            },
            "required": ["target", "action"],
        },
    },
]


class AudioControlsCapability:
    """LLM tool + help for the ambient-audio voice controls, delegating to an AudioLayer. Also
    forwards bus events to the layer (so registering this one capability wires both)."""

    def __init__(self, layer: AudioLayer, *, log: Optional[Callable[[str], None]] = None) -> None:
        self.layer = layer
        self._log = log or (lambda _m: None)

    # capability interface
    def tools(self) -> list[dict]:
        return _AUDIO_TOOLS

    def on_event(self, event: dict) -> None:
        self.layer.on_event(event)

    def run_tool(self, name: str, inp: dict) -> str:
        if name != "control_ambient_audio":
            return f"Unknown tool: {name}"
        target = str(inp.get("target", "")).strip().lower()
        action = str(inp.get("action", "")).strip().lower()
        try:
            if target == "all":
                self.layer.set_muted(action == "off")
                return "Muted all ambient audio." if action == "off" else "Ambient audio on."
            if target == "music":
                if action == "up":
                    return f"Music volume set to {self.layer.bump_music_volume(3.0):.0f} dB."
                if action == "down":
                    return f"Music volume set to {self.layer.bump_music_volume(-3.0):.0f} dB."
                self.layer.set_music(action == "on")
                return "Music on." if action == "on" else "Music off."
            if target == "comms":
                self.layer.set_comms(action == "on")
                return "Comms voices on." if action == "on" else "Comms voices quieted."
            if target == "chatter":
                self.layer.set_chatter(action == "on")
                return "Space chatter on." if action == "on" else "Space chatter muted."
            return f"I can't control '{target}'."
        except Exception as e:  # noqa: BLE001 — a control glitch must never crash the loop
            self._log(f"audio control error: {e}")
            return "Sorry, I couldn't change the audio just then."

    def help_meta(self) -> HelpMeta:
        return HelpMeta(
            category="ambient audio",
            one_liner="Control the atmospheric audio layer — space chatter, comms voices, and music.",
            example="mute the chatter",
            group="settings",
            slots=(
                Slot(param="target",
                     phrasings=("the chatter", "the comms", "the music", "all ambient audio"),
                     example="turn the music down",
                     help_text="Pick what to control: chatter, comms, music, or all of it."),
                Slot(param="action",
                     phrasings=("on", "off", "up", "down", "mute", "quiet", "stop"),
                     example="stop the music",
                     help_text="Turn it on or off; for music you can also say up or down."),
            ),
        )
