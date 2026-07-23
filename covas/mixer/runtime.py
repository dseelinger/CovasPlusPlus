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

import random
import threading
import time
from collections.abc import Callable
from dataclasses import replace

from ..capabilities.base import HelpMeta, Slot
from ..config import experimental
from ..persona_speech import DEFAULT_AMBIENT_TTL_S, Priority
from .buses import ALERT, AMBIENT, COMMS, COVAS, MUSIC
from .carrier import (
    CAPTAIN,
    CaptainDedup,
    CarrierEventResponder,
    CarrierPlayer,
    build_carrier_config,
    carrier_cues,
)
from .chatter import (
    ChatterPlayer,
    CrewChatterPlayer,
    chatter_cues,
    chatter_interval,
    crew_chatter_cue,
    situation_context,
)
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
from .cues import CREW, PERSONA, CueRegistry
from .driver import CueDriver
from .eligibility import EligibilityEngine
from .example_cues import DEFAULT_THREAT_LINES, InterdictionCue, SfxPlayer, sfx_cues
from .governor import CueGovernor, GovernorConfig
from .mixer import pcm16_to_float, speak_on_bus
from .music import MusicDirector
from .variants import CommsVoicer, make_variant_generator
from .voice_memory import StickyVoicePool
from .voices import VoiceCast, build_cast

# Journal arrival events that carry the current system + its Population; used to re-cast the
# comms voices per system and to scale chatter frequency by population.
_ARRIVAL_EVENTS = frozenset({"FSDJump", "CarrierJump", "Location"})
# Journal events that anchor a GUARANTEED captain line at the carrier transition (#137): a
# supercruise-arrival at/near the owned carrier and an undock leaving it.
_CARRIER_EVENT_TRIGGERS = frozenset({"SupercruiseExit", "Undocked"})
# How many recent players keep a stable random voice (a wing/operation's worth).
_PLAYER_VOICE_MEMORY = 25
# Anti-repeat window (issue #57): avoid re-handing-out any of the last N cast voices, so the ambient
# soundscape spreads across the pool instead of clustering on a few voices (a shuffled-soundboard
# feel). Relaxes automatically when the pool is smaller than the window, so it never starves.
_ANTI_REPEAT_WINDOW = 5


def _default_sting() -> str:
    """The shipped ORIGINAL interdiction sting (I8), used when the user supplies none. Returns ""
    if the asset is missing (frozen build without it) so the cue stays fail-soft/silent."""
    from ..config import app_dir
    p = app_dir() / "covas" / "assets" / "cues" / "interdiction_sting" / "interdiction_sting.wav"
    return str(p) if p.is_file() else ""


def _text_generator(llm, model: str | None):  # noqa: ANN001
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
        cheap_model: str | None = None,
        cast_synth: Callable | None = None,  # noqa: ANN001 — (Voice, text) -> (pcm, sr)
        content: ContentBundle | None = None,
        allow_chatter_flavor: bool = True,  # tiering (#84): False -> canned chatter only, no LLM
        allow_comms_variants: bool = True,  # tiering (#84): False -> verbatim comms only, no LLM
        clock: Callable[[], float] = time.monotonic,
        log: Callable[[str], None] | None = None,
        persona_arbiter=None,  # noqa: ANN001 — PersonaSpeechArbiter (issue #146); None -> direct
    ) -> None:
        self.cfg = cfg
        self.mixer = mixer
        self.tts = tts
        # The app's ONE persona speech arbiter (issue #146). When present, an ambient PERSONA cue
        # is ENQUEUED here (AMBIENT priority + a short TTL) instead of spoken straight onto the
        # COVAS bus — so it can never mix with a reply/callout. None (tests building the layer
        # standalone) keeps the legacy direct `_speak_persona` path.
        self._persona_arbiter = persona_arbiter
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
        # The famous-filtered live EL voice list, cached from the first successful fetch so a later
        # settings change (which rebuilds via apply_settings, without re-fetching) still seeds the
        # random default pool instead of collapsing the whole cast to the single persona voice.
        self._el_voices: list[dict] | None = None
        self._cast: VoiceCast = build_cast(cfg, synth=self._cast_synth)
        self._clock = clock
        # Crew best-fit voice pairings (issue #124): {name.lower() -> voice_id}, pushed in by the
        # background pairing worker (bootstrap.pair_crew_voices) once it resolves. Consulted by
        # speak_crew as the MIDDLE precedence tier — explicit [crew].file voice_ref (issue #70)
        # still wins, an empty/absent entry falls through to the deterministic assign() (issue #69).
        self._crew_pairings: dict[str, str] = {}

        # Random-but-sticky voice memories over the cast pool (C10+). Comms speakers are re-cast
        # per system (cleared on a jump); players keep a session-long voice via an LRU. Chatter is
        # cast fresh per line (below). All degrade to the persona voice on an empty pool.
        self._rng = random.Random()
        self._comms_voices = StickyVoicePool(
            self._cast.pool, rng=self._rng, fallback=self._cast.persona(),
            anti_repeat=_ANTI_REPEAT_WINDOW)
        self._player_voices = StickyVoicePool(
            self._cast.pool, rng=self._rng, capacity=_PLAYER_VOICE_MEMORY,
            fallback=self._cast.persona(), anti_repeat=_ANTI_REPEAT_WINDOW)
        # Per-line chatter is the most repetition-prone (no stickiness to spread it), so it carries
        # the anti-repeat window too — consecutive lines won't reuse a recent voice.
        self._chatter_voices = StickyVoicePool(
            self._cast.pool, rng=self._rng, fallback=self._cast.persona(),
            anti_repeat=_ANTI_REPEAT_WINDOW)
        # Live game state that drives chatter frequency + comms re-casting.
        self._population: float | None = None
        self._system: str = ""

        audio = cfg.get("audio", {}) or {}
        # Runtime toggles, seeded from config. The governor's own `enabled` ([audio.cues]) gates
        # chatter+SFX at the throttle level; these add per-category voice/settings control.
        self.chatter_on = bool((audio.get("cues", {}) or {}).get("enabled", False))
        self.sfx_on = self.chatter_on
        self.comms_on = bool((audio.get("comms", {}) or {}).get("enabled", True))
        # Music is EXPERIMENTAL (issue #123): gated behind [experimental.music] here too, so this
        # runtime toggle (which mirrors onto the director's _enabled on a live reload) can't re-arm
        # a director that MusicDirector.from_cfg built disabled.
        self.music_on = (bool((cfg.get("music", {}) or {}).get("enabled", False))
                         and experimental(cfg, "music"))
        # Fleet-carrier context voices (issue #19) — captain/tower/carrier-chatter, gated on being
        # at/near the OWN carrier. Independently toggleable; default on (they're naturally silent
        # unless you own a carrier and are there).
        self._carrier_cfg = build_carrier_config(cfg)
        self.carrier_on = self._carrier_cfg.enabled
        self.muted = False

        # Cues: chatter + SFX in one registry the driver governs, with drop-in content overlaid
        # (folder SFX samples / chatter line files replace the shipped defaults when present). The
        # carrier context cues (#19) register straight in — they have curated pools + a voice_role,
        # so they're out of the drop-in overlay path.
        self._registry = CueRegistry()
        for cue in overlay_cues(list(chatter_cues()) + list(sfx_cues(cfg)), self._content):
            self._registry.register(cue)
        for cue in carrier_cues():
            self._registry.register(cue)
        # Ambient crew chatter (issue #126): one CREW-role cue, no phrasings pool (LLM-or-nothing),
        # voiced by whichever roster member's turn it is. Like the carrier cues it registers
        # straight in (no drop-in overlay — it carries no pool to overlay).
        self._registry.register(crew_chatter_cue())
        # The governor only THROTTLES (cooldowns + global rate) here — the audio layer's own
        # per-category flags are the on/off gates, so force it enabled (its [audio.cues].enabled
        # in config maps to the chatter/SFX toggle, not to whether comms may play).
        self._governor = CueGovernor(replace(GovernorConfig.from_cfg(cfg), enabled=True),
                                     clock=clock)
        self._engine = EligibilityEngine()
        # LLM use in the ambient layer is OFF by default (cost): fact-bearing chatter and NPC
        # comms are pool/verbatim unless explicitly opted in. Flavor chatter / comms variants
        # only wire the LLM when their flag is set. The tiering level (#84) is a SECOND gate on top
        # of the config flag: a lean level forces `allow_*` off so the layer FALLS BACK to the
        # canned/verbatim path (generate=None) and no background LLM call is ever spawned.
        # Stored so a later live LLM hot-swap (set_providers, issue #90) re-applies the SAME tier
        # gate — otherwise a swap could re-enable a background path the optimization level disabled.
        self._allow_chatter_flavor = allow_chatter_flavor
        self._allow_comms_variants = allow_comms_variants
        chatter_flavor = bool((audio.get("cues", {}) or {}).get("flavor", False)) and allow_chatter_flavor
        comms_variants = bool((audio.get("comms", {}) or {}).get("variants", False)) and allow_comms_variants
        chatter_gen = _text_generator(llm, cheap_model) if (llm is not None and chatter_flavor) else None
        # Context grounding (issue #85): a flavor musing is seeded from a COMPACT live-ED slice so
        # each line has a real reason. The slice is derived from the SAME EDContext the main loop
        # uses (no parallel tracker); it only shapes the mood — the output stays fact-safe.
        self._chatter = ChatterPlayer(self._speak_bus, generate=chatter_gen,
                                      context=self._chatter_context,
                                      min_interval=self._chatter_interval, clock=clock)
        # "Our"-perspective musings (issue #57): a cue tagged voice_role=PERSONA is something the
        # companion itself notices, so it speaks in COVAS's OWN voice on the clean COVAS bus instead
        # of a random radioed cast voice. Same player shape (flavor generator + frequency gate) as
        # ambient chatter, just a different `speak` seam: `_persona_enqueue` hands the line to the
        # app's persona speech arbiter (issue #146) so it queues behind — and yields to — replies
        # and callouts instead of mixing with them on the COVAS bus.
        self._persona_chatter = ChatterPlayer(self._persona_enqueue, generate=chatter_gen,
                                               context=self._chatter_context,
                                               min_interval=self._chatter_interval, clock=clock)
        # Ambient crew chatter (issue #126): same flavor generator (cheap tier) + situation
        # grounding as ambient chatter, but the speaker is a rotating ROSTER member voiced
        # fire-and-forget in their own cast voice (NOT the blocking conversation-path speak_crew,
        # which would stall this event-pump thread). Its interval is a CREW-specific sparse window,
        # NOT population-scaled — crew are aboard your ship regardless of the local population.
        self._crew_chatter = CrewChatterPlayer(
            self._crew_roster, self._speak_crew_ambient, generate=chatter_gen,
            context=self._chatter_context, min_interval=self._crew_chatter_interval,
            clock=clock, log=self._log)
        self._sfx = SfxPlayer(self._play_sample)
        # Carrier context voices (#19): each role speaks its own configured voice (or a stable
        # cast-pool fallback) with its display name woven in. Pool-only (fact-safe), no LLM.
        self._carrier = CarrierPlayer(self._submit_voice, self._carrier_voice,
                                      names=self._carrier_cfg.name_map(), log=self._log)
        # Event-anchored captain responses (#137): a GUARANTEED captain line on supercruise-arrival
        # at/near the owned carrier and on undock leaving it, fired straight off the journal event
        # (not the ambient budget). The shared CaptainDedup keeps a guaranteed line and a long-
        # cooldown ambient welcome from stacking into a back-to-back double-fire in the same tick.
        self._captain_dedup = CaptainDedup(clock=clock)
        self._carrier_events = CarrierEventResponder(
            self._play_carrier_event, at_near=self._carrier_at_near,
            owned_id=self._owned_carrier_id, dedup=self._captain_dedup, log=self._log)
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

    def reload_content(self, content: ContentBundle) -> dict:
        """Hot-swap the C11 drop-in ambient content (SFX samples, music tracks, chatter phrasings,
        interdiction sting/threat) into the ALREADY-COMPOSED layer — no restart (issue #110,
        follow-up to #109). Symmetric with `__init__`, which already takes a pre-scanned bundle; the
        app does the folder scan (keeping the `[audio].content_root` seam at the app boundary) and
        hands the fresh bundle here.

        The turn-stage cues reload via a single lock-free dict rebind (`CuePlayer.reload`); this
        content is instead woven into composed objects that HOLD LIVE STATE, so the swap rebuilds
        each AROUND that state rather than replacing the object:
          * the registry's SFX/chatter cues are re-overlaid and ATOMICALLY replaced (the carrier
            cues, which carry no drop-in content, are preserved) — the SAME registry the driver
            reads, so the governor's cooldowns and the Chatter/Sfx player rotation + the chatter
            frequency gate (all keyed by cue name / kept on the same player instances) are untouched;
          * the MusicDirector keeps its current context/track/rotation (an in-progress crossfade is
            not interrupted) and only its LIBRARY is swapped, so new tracks apply on the next context
            change while the current one plays on;
          * the interdiction cue keeps its rotation + governor and only its sting-sample set and
            threat pool are swapped.
        Fail-soft: never raises into the caller (a bad bundle leaves the live content in place).
        Returns per-category counts for a confirmation message."""
        try:
            # 1. Cue registry: re-overlay SFX + chatter onto fresh cue defs, keep the carrier cues.
            overlaid = overlay_cues(list(chatter_cues()) + list(sfx_cues(self.cfg)), content)
            self._registry.replace_all(list(overlaid) + list(carrier_cues()))
            # 2. Music: swap the library, keep the director's live context/track/rotation.
            self._music.set_library(merged_music_library(self.cfg, content))
            # 3. Interdiction: swap sting-sample set + threat pool, keep rotation + governor. A
            #    None threat pool falls back to the shipped defaults, matching __init__.
            self._interdiction.set_content(
                sting_samples=tuple(content.sfx.get("interdiction_sting", [])),
                threat_lines=threat_lines(content, DEFAULT_THREAT_LINES))
            self._content = content
            self._log(status_summary(content))
        except Exception as e:  # noqa: BLE001 — a reload must never take down the loop
            self._log(f"ambient content reload failed: {e}")
            return {}
        return {
            "sfx": sum(len(v) for v in content.sfx.values()),
            "music": sum(len(v) for v in content.music.values()),
            "chatter": sum(len(v) for v in content.chatter.values()),
            "threat": len(content.threat),
        }

    # -- system arrival: population (chatter scaling) + comms re-casting --------
    def _note_arrival(self, event: dict) -> None:
        """Fold a system-arrival event: remember the system's Population (drives chatter frequency)
        and, when the STAR SYSTEM changes, forget the comms speaker->voice map so the new system's
        speakers get freshly-cast random voices. Player voices persist across jumps (session LRU)."""
        pop = event.get("Population")
        if isinstance(pop, (int, float)) and not isinstance(pop, bool):
            self._population = float(pop)
        sysname = str(event.get("StarSystem") or "").strip()
        if sysname and sysname != self._system:
            self._system = sysname
            self._comms_voices.clear()

    # -- fuel for the driver ----------------------------------------------------
    def _fuel(self) -> float | None:
        if self._ed_ctx is None:
            return None
        try:
            return self._ed_ctx.fuel_pct()
        except Exception:  # noqa: BLE001
            return None

    # -- own-carrier location context (#19) -------------------------------------
    def _refresh_carrier_context(self) -> None:
        """Fold the live 'am I at / near my own carrier' context into the eligibility engine so the
        carrier voices become eligible there. Read from EDContext (updated by the journal watcher
        before the event reaches us), on every ed_event, so docking/undocking tracks immediately."""
        if self._ed_ctx is None:
            return
        try:
            self._engine.note_carrier(
                at_own=bool(self._ed_ctx.at_own_carrier()),
                near_own=bool(self._ed_ctx.near_own_carrier()),
            )
        except Exception:  # noqa: BLE001 — a context glitch must never break the pump
            pass

    def _carrier_at_near(self) -> tuple[bool, bool]:
        """The live (at_own, near_own) location context for the event-anchored responder (#137),
        read from EDContext. Fail-soft: any glitch reads as 'not there' so nothing fires."""
        if self._ed_ctx is None:
            return (False, False)
        try:
            return (bool(self._ed_ctx.at_own_carrier()), bool(self._ed_ctx.near_own_carrier()))
        except Exception:  # noqa: BLE001 — a context glitch just means no event-anchored line
            return (False, False)

    def _owned_carrier_id(self) -> int | None:
        """The owned carrier's CarrierID (== its MarketID), so an undock from a DIFFERENT carrier
        in the same system isn't mistaken for leaving ours (#137). None when unknown/unavailable."""
        if self._ed_ctx is None:
            return None
        try:
            cid = self._ed_ctx.carrier_snapshot().get("carrier_id")
            return int(cid) if isinstance(cid, int) and not isinstance(cid, bool) else None
        except Exception:  # noqa: BLE001 — no id -> the responder skips the id check, fail-soft
            return None

    def _play_carrier_event(self, cue) -> bool:  # noqa: ANN001 — a Cue
        """The event-anchored responder's play seam (#137): voice a captain cue through the same
        CarrierPlayer as the ambient cues, but gated on the carrier toggle + master mute here (the
        responder itself is transition-triggered, not budget-governed)."""
        if self.muted or not self.carrier_on:
            return False
        return self._carrier(cue)

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

    def speak_crew(self, name: str, text: str, cancel) -> bool:  # noqa: ANN001 — threading.Event
        """Voice a NAMED CREW line (issue #69) in that character's DETERMINISTIC cast voice on the
        radio-treated COMMS bus, BLOCKING until the line has played or `cancel` fires (barge-in).

        This is the crew seam on the conversation path: the persona (ship COVAS++) keeps its own
        direct `tts.speak` path for unprefixed lines, while a `[Name]`-prefixed line comes here and
        reuses the C10 cast exactly like comms/chatter. The voice is resolved via
        `VoiceCast.for_crew(name, voice_ref)`: an EXPLICIT `[crew].file` voice_ref (issue #70)
        overrides the deterministic pick, and a blank one falls back to `assign(name)` — same name
        -> same voice, distinct names -> distinct voices, empty pool -> the persona voice. The
        roster is read live from config so a control-panel edit applies to the very next crew line.

        Returns True when the line was voiced (or was empty — nothing to say); False only when
        nothing could be synthesized (dead provider), so the caller degrades THAT line to the
        persona voice. Fail soft — never raises into the reply loop."""
        text = str(text or "").strip()
        if not text:
            return True  # empty segment -> treat as handled, don't force a persona re-speak
        try:
            from .. import crew as crew_mod  # local import: keep the mixer package cycle-free
            # Precedence (issue #124): an EXPLICIT [crew].file voice_ref always wins; failing that,
            # a best-fit CREW PAIRING for this name (from the background LLM casting worker); only
            # with neither does for_crew() fall through to the deterministic assign(name). The
            # voice_ref is read from the ACTIVE ship's roster (issue #127) so a pinned voice tracks
            # the ship you're flying.
            ref = (crew_mod.voice_ref_for(self.cfg, name, crew_mod.active_ship_id(self._ed_ctx))
                  or self._crew_pairings.get(str(name or "").strip().lower(), ""))
            voice = self._cast.for_crew(name, ref)
            pcm, sr = self._cast.synth(voice, text)
        except Exception as e:  # noqa: BLE001 — a dead cast voice degrades to the persona voice
            self._log(f"crew synth failed ({name}): {e}")
            return False
        if not pcm:
            return False
        try:
            buf = self.mixer.submit(COMMS, pcm16_to_float(pcm), sr)
        except Exception as e:  # noqa: BLE001 — a mixer glitch degrades to the persona voice
            self._log(f"crew play failed ({name}): {e}")
            return False
        # The mixer plays buffer sources fire-and-forget, so block for the known duration to keep
        # segments in order — but wake immediately on barge-in: a `cancel` set mid-line drops the
        # rest of this crew line off the comms bus (mirrors the persona speak path's cancellation).
        sr_out = float(getattr(self.mixer, "sample_rate", 0) or sr or 16000)
        duration = (len(buf) / sr_out) if sr_out else 0.0
        if duration > 0 and cancel.wait(duration):
            self.mixer.clear_bus(COMMS)
        return True

    def _crew_roster(self) -> list:
        """The ENABLED crew roster for ambient crew chatter (issue #126), or [] when crew is off —
        the empty-roster case the CrewChatterPlayer treats as 'cue ineligible, skip'. Read live
        from config so a control-panel edit (or a `crew off` toggle) applies to the next line."""
        try:
            from .. import crew as crew_mod  # local import: keep the mixer package cycle-free
            if not crew_mod.is_enabled(self.cfg):
                return []
            # The ACTIVE ship's roster (issue #127): the fighter pilot you adopted onto the
            # Chieftain doesn't chatter on the Phantom.
            return crew_mod.load_members(self.cfg, crew_mod.active_ship_id(self._ed_ctx))
        except Exception as e:  # noqa: BLE001 — a roster read glitch just means no crew line
            self._log(f"crew roster read failed: {e}")
            return []

    def _speak_crew_ambient(self, name: str, text: str) -> bool:
        """Voice an AMBIENT crew line (issue #126) in member `name`'s cast voice on the COMMS bus,
        FIRE-AND-FORGET. This resolves the voice with the SAME precedence as the conversation-path
        `speak_crew` — explicit `[crew].file` voice_ref (issue #70) > best-fit #124 pairing >
        deterministic assign (issue #69) — but, crucially, submits to the mixer and returns
        IMMEDIATELY (no blocking `cancel.wait`): this runs on the audio event-pump thread, so it
        must not stall waiting for the clip to finish. Fail soft."""
        try:
            from .. import crew as crew_mod  # local import: keep the mixer package cycle-free
            ref = (crew_mod.voice_ref_for(self.cfg, name, crew_mod.active_ship_id(self._ed_ctx))
                  or self._crew_pairings.get(str(name or "").strip().lower(), ""))
            voice = self._cast.for_crew(name, ref)
        except Exception as e:  # noqa: BLE001 — a voice-resolve glitch degrades to silence
            self._log(f"crew chatter voice resolve failed ({name}): {e}")
            return False
        return self._submit_voice(voice, text, COMMS)

    def _crew_chatter_interval(self) -> float | None:
        """The CREW-specific seconds-between-ambient-crew-lines (issue #126) — a sparse gap drawn
        from `[crew].chatter_min_seconds`..`chatter_max_seconds`. Deliberately NOT population-scaled
        (crew are aboard your ship, so the local system's population is irrelevant): a randomized
        gap in the window gives natural, non-clockwork pacing. Read live so a Settings change
        applies immediately; the global C3 governor still rate-caps on top."""
        cr = self.cfg.get("crew", {}) or {}
        lo = float(cr.get("chatter_min_seconds", 180.0))
        hi = float(cr.get("chatter_max_seconds", 600.0))
        if hi < lo:
            lo, hi = hi, lo
        return self._rng.uniform(lo, hi)

    def _chatter_interval(self) -> float | None:
        """Current required seconds between chatter lines, scaled by the live system population
        (see chatter.chatter_interval). Read live so a Settings-page change applies immediately."""
        ch = (self.cfg.get("audio", {}) or {}).get("chatter", {}) or {}
        return chatter_interval(
            float(ch.get("min_seconds", 45.0)),
            float(ch.get("max_seconds", 240.0)),
            self._population,
            float(ch.get("full_population", 1_000_000_000.0)),
        )

    def _chatter_context(self) -> str:
        """The compact live-ED situation slice that grounds a flavor musing (issue #85). Reuses the
        shared EDContext (the same snapshot/recent feed the main loop injects) plus the population
        this layer already tracks from arrival events — NO parallel tracker. Fail-soft: any missing
        context or read error yields "" (an ungrounded musing), never an exception into the pump."""
        if self._ed_ctx is None:
            return ""
        try:
            snap = self._ed_ctx.snapshot()
            recent = self._ed_ctx.recent(3)
        except Exception:  # noqa: BLE001 — a context glitch just yields an ungrounded musing
            return ""
        return situation_context(snap, recent, self._population)

    def _speak_bus(self, text: str, bus: str) -> bool:
        """Speak an AMBIENT chatter line on its bus with a FRESH RANDOM cast voice per line, so the
        ambient radio sounds like many different anonymous speakers (issue #57: the anti-repeat
        window on the pool keeps those voices from clustering)."""
        return self._submit_voice(self._chatter_voices.random(), text, bus)

    def _persona_ttl(self) -> float:
        """Freshness window (seconds) for a queued ambient PERSONA musing (issue #146): a musing
        that waited longer than this behind a reply/callout is DROPPED rather than spoken late (a
        "nice system out here" line 10 s after you jumped away is noise). Read live from
        `[audio].persona_ttl_seconds` so a Settings change applies to the next cue."""
        return float((self.cfg.get("audio", {}) or {}).get("persona_ttl_seconds",
                                                            DEFAULT_AMBIENT_TTL_S))

    def _persona_enqueue(self, text: str, bus: str) -> bool:
        """The ambient PERSONA-cue `speak` seam (issue #146): instead of speaking straight onto the
        COVAS bus (which could MIX with a reply/callout), ENQUEUE the musing on the app's persona
        speech arbiter at AMBIENT priority with a short TTL. Returns True when accepted (the cue's
        frequency gate then advances) — the actual play happens later, one-line-at-a-time, on the
        arbiter's speaker thread. With no arbiter wired (a standalone test layer) it falls back to
        the legacy direct path so existing behaviour is unchanged."""
        arb = self._persona_arbiter
        if arb is None:
            return self._speak_persona(text, bus)
        try:
            arb.enqueue(text, priority=Priority.AMBIENT, ttl=self._persona_ttl(),
                        speak=lambda cancel: self._speak_persona_blocking(text, cancel, bus=bus))
            return True
        except Exception as e:  # noqa: BLE001 — an enqueue glitch just means no musing, never a crash
            self._log(f"persona musing enqueue failed: {e}")
            return False

    def _speak_persona_blocking(self, text: str, cancel, bus: str = COVAS) -> None:  # noqa: ANN001
        """Play an ambient PERSONA musing on `bus` and BLOCK until it finishes OR `cancel` fires —
        the arbiter's speaker thread calls this so exactly one persona line sounds at a time and a
        supersede/barge-in can cut it mid-word (issue #146). Mirrors `speak_crew`'s submit-then-
        wait-on-cancel shape: synth + submit the whole clip, then wait its duration; if cancel
        fires first, clear the bus to stop the audio immediately. Fails soft — a dead persona voice
        degrades to silence, never a crash."""
        try:
            pcm, sr = self.tts.synth_pcm(text)
            if not pcm or cancel.is_set():
                return
            buf = self.mixer.submit(bus, pcm16_to_float(pcm), sr)
        except Exception as e:  # noqa: BLE001 — a dead persona voice degrades to nothing
            self._log(f"persona musing failed: {e}")
            return
        sr_out = float(getattr(self.mixer, "sample_rate", 0) or sr or 16000)
        duration = (len(buf) / sr_out) if sr_out else 0.0
        if duration > 0 and cancel.wait(duration):
            self.mixer.clear_bus(bus)  # barged in / superseded mid-line -> stop the rest now

    def _speak_persona(self, text: str, bus: str) -> bool:
        """Speak an "our"-perspective musing in COVAS's OWN persona voice, via the app's real TTS
        provider, on the clean COVAS bus — the attribution rule (issue #57): something WE notice
        comes from the companion, never an anonymous radioed cast voice. Uses `self.tts` (the same
        provider as the companion's replies), mirroring the interdiction COVAS line. Fails soft.

        This is the LEGACY direct path, kept as the fallback for a layer built with no persona
        arbiter (a standalone test); the live app routes PERSONA cues through `_persona_enqueue`."""
        try:
            speak_on_bus(self.mixer, self.tts, text, bus=bus)
            return True
        except Exception as e:  # noqa: BLE001 — a dead persona voice degrades to nothing, never crashes
            self._log(f"persona musing failed: {e}")
            return False

    def _carrier_voice(self, role: str):  # noqa: ANN201 — a Voice
        """The Voice for a carrier role (#19): the one configured under `[audio.carrier].<role>`,
        else a deterministic distinct cast-pool voice (stable per role) so captain/tower/chatter
        still sound like separate people with zero configuration."""
        cr = self._carrier_cfg.roles.get(role)
        if cr is not None and cr.voice is not None:
            return cr.voice
        return self._cast.assign(f"carrier:{role}")

    def _comms_play(self, text: str, record) -> bool:  # noqa: ANN001 — a VoiceableComms
        """Voice a gated comms line with a random-but-sticky voice: a real player keeps one voice
        for the session (LRU of the last 25), an NPC/station speaker keeps one for as long as we're
        in this system (re-cast on a jump). Routed to its provider on the radio-treated comms bus."""
        if getattr(record, "kind", "") == "player":
            voice = self._player_voices.assign(getattr(record, "sender", "") or "player")
        else:
            hint = getattr(record, "voice", "") or ""
            hint = hint if hint in ("male", "female") else None
            identity = getattr(record, "sender", "") or getattr(record, "channel", "") or "npc"
            voice = self._comms_voices.assign(identity, gender_hint=hint)
        return self._submit_voice(voice, text, COMMS)

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
        # Attribution by voice_role (issue #57), checked first since these cues also carry phrasings
        # (which would otherwise route to the anonymous radioed-chatter path):
        #   * PERSONA -> an "our"-perspective line in COVAS's own voice on the clean bus;
        #   * any other role (captain/tower/chatter) -> the carrier cue's dedicated voice.
        role = getattr(cue, "voice_role", "")
        if role == PERSONA:
            return self._persona_chatter(cue) if self.chatter_on else False
        if role == CREW:
            # Ambient crew chatter (issue #126): gated on the ambient-audio toggle here; the
            # crew-enabled + roster-non-empty gate lives inside the player (empty roster -> skip),
            # and the [audio.cues].flavor gate rides the generator (None flavor gen -> silence).
            return self._crew_chatter(cue) if self.chatter_on else False
        if role:
            if not self.carrier_on:
                return False
            # Share the captain dedup (#137): if a guaranteed arrival/departure line JUST spoke,
            # skip the long-cooldown ambient captain welcome/status/duty so they don't stack.
            if role == CAPTAIN and not self._captain_dedup.allow():
                return False
            fired = self._carrier(cue)
            if fired and role == CAPTAIN:
                self._captain_dedup.mark()
            return fired
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
            if name in _ARRIVAL_EVENTS:
                self._note_arrival(event)
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
            self._refresh_carrier_context()
            if self.muted:
                return
            # Event-anchored captain responses (#137): fire BEFORE the driver tick so the shared
            # dedup blocks an ambient captain echo in this same tick. The responder is gated on the
            # carrier toggle inside its play seam, so it's naturally silent when carrier voices are off.
            if name in _CARRIER_EVENT_TRIGGERS:
                self._carrier_events.on_event(event)
            if self.music_on:
                states = self._engine.states(fuel_pct=self._fuel())
                self._realize_music(self._music.update(states))
            if self.chatter_on or self.sfx_on or self.carrier_on:
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

    def set_carrier(self, on: bool) -> bool:
        """Toggle the fleet-carrier context voices (captain/tower/carrier chatter, #19)."""
        self.carrier_on = bool(on)
        return self.carrier_on

    def bump_music_volume(self, delta_db: float) -> float:
        """Nudge the music bus volume (dB), clamped to a sane range, and apply it live."""
        m = self.cfg.setdefault("audio", {}).setdefault("buses", {}).setdefault("music", {})
        cur = float(m.get("volume_db", -12.0))
        new = max(-40.0, min(6.0, cur + float(delta_db)))
        m["volume_db"] = new
        self.mixer.set_bus_config(self.cfg)
        return new

    def set_crew_pairings(self, mapping: dict | None) -> None:
        """Push the crew best-fit voice pairings (issue #124) computed by the background pairing
        worker — `{name.lower() -> voice_id}` — so the VERY NEXT `speak_crew` line honors them.
        None/empty clears the map (e.g. no persona'd auto members left), falling back to the
        deterministic assign() for everyone, same as before this feature existed."""
        self._crew_pairings = dict(mapping or {})

    def rebuild_cast(self, el_voices=None) -> None:  # noqa: ANN001 — list[dict] of EL voices
        """Rebuild the cast from current config, reusing the synth backends. `el_voices` (the
        famous-filtered live list) feeds the exclusion hook AND seeds the random default pool.
        The voice memories are re-pooled but KEEP their live assignments (so the EL-list fetch or a
        settings change doesn't wipe the current system's comms voices or the player LRU).

        A fresh `el_voices` list is CACHED; a rebuild without one (e.g. from apply_settings on a
        settings change) reuses the cache, so toggling a setting can't collapse the random default
        pool to the single persona voice."""
        if el_voices is not None:
            self._el_voices = el_voices
        self._cast = build_cast(self.cfg, synth=self._cast_synth, el_voices=self._el_voices)
        persona = self._cast.persona()
        for mem in (self._comms_voices, self._player_voices, self._chatter_voices):
            mem.set_pool(self._cast.pool, fallback=persona)

    def set_providers(self, *, tts=None, cast_synth=None, llm=None,  # noqa: ANN001
                      cheap_model: str | None = None) -> None:
        """Re-point the ambient layer's TTS/LLM after a live provider hot-swap (issue #90 review).
        Without this, the layer keeps the TTS/LLM it captured at construction, so a Settings-page
        voice/model switch would leave ambient musings, interdiction/comms lines and the cast synth
        on the OLD provider (a half-swap). Voice lines pick up the new TTS (and a rebuilt cast synth);
        the opt-in LLM-generated chatter-flavor + comms-variants generators are rebuilt from the new
        LLM, honoring the same [audio.cues].flavor / [audio.comms].variants flags as __init__. Pool/
        verbatim chatter+comms never used the LLM, so they're untouched. Fail-soft: pass only what
        changed; a None arg leaves that seam as-is."""
        if tts is not None:
            self.tts = tts
        if cast_synth is not None:
            self._cast_synth = cast_synth
            self.rebuild_cast()
        if llm is not None:
            audio = self.cfg.get("audio", {}) or {}
            # AND with the tier gate (issue #84) so a live LLM swap can't re-enable a background
            # LLM path the active optimization level disabled — canned/verbatim stays canned.
            chatter_flavor = (bool((audio.get("cues", {}) or {}).get("flavor", False))
                              and self._allow_chatter_flavor)
            comms_variants = (bool((audio.get("comms", {}) or {}).get("variants", False))
                              and self._allow_comms_variants)
            chatter_gen = _text_generator(llm, cheap_model) if chatter_flavor else None
            comms_gen = make_variant_generator(llm, model=cheap_model) if comms_variants else None
            self._chatter.set_generate(chatter_gen)
            self._persona_chatter.set_generate(chatter_gen)
            self._crew_chatter.set_generate(chatter_gen)
            self._comms.set_generate(comms_gen)

    def apply_settings(self) -> None:
        """Re-read config after a settings change: bus volumes/treatment, enable toggles, and the
        voice cast (cast provider / pool / player voice), reusing the existing synth backends."""
        self.mixer.set_bus_config(self.cfg)
        self.rebuild_cast()
        audio = self.cfg.get("audio", {}) or {}
        self.chatter_on = bool((audio.get("cues", {}) or {}).get("enabled", False))
        self.sfx_on = self.chatter_on
        self.set_comms(bool((audio.get("comms", {}) or {}).get("enabled", True)))
        self.set_music(bool((self.cfg.get("music", {}) or {}).get("enabled", False))
                       and experimental(self.cfg, "music"))   # experimental gate (#123)
        # Re-read the carrier roles (voices/names/enable) so a Settings change applies live (#19).
        self._carrier_cfg = build_carrier_config(self.cfg)
        self.set_carrier(self._carrier_cfg.enabled)
        self._carrier.set_names(self._carrier_cfg.name_map())
        self._interdiction.set_enabled(
            bool((audio.get("interdiction", {}) or {}).get("enabled", False)))


# ---- voice controls -------------------------------------------------------------------------
_AUDIO_TOOLS = [
    {
        "name": "control_ambient_audio",
        "description": (
            "Turn the atmospheric AUDIO LAYER on or off by part: the ambient SPACE CHATTER, the "
            "COMMS voices (NPC/station/player radio), the FLEET-CARRIER voices (your carrier's "
            "captain, tower control, and deck chatter when you're at your own carrier), the ambient "
            "MUSIC (louder/quieter/off/on), or ALL ambient audio at once. Use when the Commander "
            "says things like 'mute the chatter', 'quiet the comms', 'silence the carrier', 'mute "
            "my carrier captain', 'turn the music up', 'turn the music down', 'stop the music', "
            "'silence all the background audio', or 'turn the ambient audio back on'. This does NOT "
            "affect your own spoken replies — only the background/atmosphere layer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string",
                           "enum": ["chatter", "comms", "carrier", "music", "all"],
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

    def __init__(self, layer: AudioLayer, *, log: Callable[[str], None] | None = None) -> None:
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
            if target == "carrier":
                self.layer.set_carrier(action == "on")
                return "Carrier voices on." if action == "on" else "Carrier voices muted."
            return f"I can't control '{target}'."
        except Exception as e:  # noqa: BLE001 — a control glitch must never crash the loop
            self._log(f"audio control error: {e}")
            return "Sorry, I couldn't change the audio just then."

    def help_meta(self) -> HelpMeta:
        return HelpMeta(
            category="ambient audio",
            one_liner=("Control the atmospheric audio layer — space chatter, comms voices, your "
                       "fleet carrier's voices, and music."),
            example="mute the chatter",
            group="settings",
            slots=(
                Slot(param="target",
                     phrasings=("the chatter", "the comms", "the carrier", "the music",
                                "all ambient audio"),
                     example="turn the music down",
                     help_text="Pick what to control: chatter, comms, carrier, music, or all of it."),
                Slot(param="action",
                     phrasings=("on", "off", "up", "down", "mute", "quiet", "stop"),
                     example="stop the music",
                     help_text="Turn it on or off; for music you can also say up or down."),
            ),
        )
