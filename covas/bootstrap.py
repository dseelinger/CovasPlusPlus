"""COVAS++ capability wiring (issue #113).

The orchestrator (`covas/app.py`) owns the voice loop — PTT/threading/cancellation, the worker
turn, live-settings reconcile, and shutdown. This module owns the OTHER half that used to accrete
in `app.py`: the *construction* of every capability the app exposes.

Each capability's `_start_X` body lives here as a free `build_x(app)` function — the App instance
is passed in exactly where the method used to reach `self`, so the sharing of mutable state
(the one `KeyExecutor`, the keybind abort `Event`, the `WindowFocuser`, the parsed binds, the
event pump, the ED context) is unchanged: builders read/write it through `app` just as the
methods read/write it through `self`. Every per-capability fail-soft `try/except` guard moved
verbatim with its body.

The order + config-gating that used to spread across `App.__init__` is now the declarative
`MANIFEST`; `wire(app)` derives the None-defaults from it, then builds in list order (ED
monitoring before its journal consumers, the audio layer as the last registration). Adding
capability #41 means
adding one `build_x` function and one `Wiring` entry here — the orchestrator is not touched.
"""
from __future__ import annotations

import datetime as _dt
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from .config import deep_merge, experimental
from .router import Router

if TYPE_CHECKING:
    from .app import App


# ---- Always-on capabilities (help/settings/clipboard/version/specs) ---------
def build_help(app: "App") -> None:
    """(moved from App.__init__ — help registration)

    Help is first-class and always on: it registers ITSELF so "what can you do" always has one
    honest answer, and it projects the other capabilities' help metadata (it holds the registry
    live, so capabilities registered later still show up). Templated only — no LLM in the help
    path (Search Prompt 1)."""
    from .capabilities.help_capability import HelpCapability
    app.help = HelpCapability(app.registry,
                              log=lambda m: app._log("help", m))
    app.registry.register(app.help)


def build_checklist(app: "App") -> None:
    """(moved from App.__init__ — checklist-capability registration)

    Present only when a checklist file is configured, matching the prior tool-gating. on_change
    publishes a `checklist` event on every voice/tool CRUD so a live Checklist page reflects it
    in place instead of going stale until reload (#82)."""
    from .capabilities.checklist_capability import ChecklistCapability
    app.registry.register(
        ChecklistCapability(app.checklist, on_change=app.bus.publish))


def build_settings(app: "App") -> None:
    """(moved from App.__init__ — settings-by-voice, Prompt N2)

    Change any setting spoken aloud, projected from the SAME schema the web page uses so the two
    can't drift. Always on, like help — it reads/writes the live config through update_settings,
    validating against the schema."""
    from . import settings_schema as schema
    from .capabilities.settings_capability import SettingsCapability
    app.settings_cap = SettingsCapability(
        get_value=lambda s: schema.get_value(app.cfg, s),
        apply_patch=app.update_settings,
        options_for=app._settings_option_pairs,
        log=lambda m: app._log("settings", m))
    app.registry.register(app.settings_cap)


def build_clipboard(app: "App") -> None:
    """(moved from App.__init__ — "copy that to my clipboard", N11)

    One LLM-native tool — the model resolves what "that" refers to from the conversation and
    passes the exact text. Always on, like help/settings: local, harmless, no config."""
    from .capabilities.clipboard_capability import ClipboardCapability
    app.clipboard_cap = ClipboardCapability(log=lambda m: app._log("clipboard", m))
    app.registry.register(app.clipboard_cap)


def build_version(app: "App") -> None:
    """(moved from App.__init__ — "what version are you?", I7)

    Report the running app version by voice, read from the single-source-of-truth
    covas/__version__.py. Always on, like help/settings/clipboard — local and harmless. Checking
    FOR updates stays a control-panel action, never a voice command (INSTALLER_DESIGN.md #5)."""
    from .capabilities.version_capability import VersionCapability
    app.version_cap = VersionCapability(log=lambda m: app._log("version", m))
    app.registry.register(app.version_cap)


def build_ship_spec(app: "App") -> None:
    """(moved from App.__init__ — grounded ship specifications, issue #83)

    Answer "what can a Type-8 carry / what pad does a Mandalay need" from a bundled, refreshable
    dataset keyed to the SAME canonical names ships.py resolves. Always on, like help/version:
    pure, offline, no network (a resolved hull with no bundled spec is spoken as "no data,
    web-search" rather than confabulated)."""
    from .capabilities.ship_spec_capability import ShipSpecCapability
    app.ship_spec = ShipSpecCapability(log=lambda m: app._log("ship_spec", m))
    app.registry.register(app.ship_spec)


def build_game_data_status(app: "App") -> None:
    """(moved from App.__init__ — game-data freshness, issue #101)

    "How current is your ship/game data?" answered from the bundled dataset manifest (sources +
    generation dates) — the honest companion to the ship_spec "no data yet, web-search" path.
    Always on: pure, offline, no network."""
    from .capabilities.game_data_status_capability import GameDataStatusCapability
    app.game_data_status = GameDataStatusCapability(
        log=lambda m: app._log("game_data_status", m))
    app.registry.register(app.game_data_status)


# ---- Audio layer (C1-C8 composition, C9) ------------------------------------
def build_audio_layer(app: "App") -> None:
    """(moved from App._start_audio_layer)

    Build the AudioLayer over the shared mixer and register the voice-control capability
    (which also forwards bus events to the layer). Fail soft — a startup problem just leaves
    the ambient layer off; COVAS speech still routes through the mixer. Needs the event pump
    so comms/chatter/interdiction/music react to journal events."""
    try:
        from .mixer import AudioControlsCapability, AudioLayer
        # Drop-in content (C11): ensure the folder skeleton (idempotent) then scan it, so a
        # dropped-in file joins the cues with no code/config edits. Fail-soft.
        content = app._scan_audio_content()
        cheap = Router.from_cfg(app.cfg).cheap_route(None).model
        app.audio = AudioLayer(
            app.cfg, app.mixer, app.tts,
            ed_ctx=app.ed_ctx, llm=app.llm, cheap_model=cheap,
            cast_synth=app._build_cast_synth(), content=content,
            # Tiering (#84): the level gates the two LLM-generated background paths — below Full
            # these fall back to canned chatter / verbatim comms (no background LLM call).
            allow_chatter_flavor=app.tier_level.chatter_flavor,
            allow_comms_variants=app.tier_level.comms_variants,
            log=lambda m: app._log("audio", m))
        app.registry.register(
            AudioControlsCapability(app.audio, log=lambda m: app._log("audio", m)))
        app._start_event_pump()
        # Fetch the (famous-filtered) ElevenLabs voice list off the hot path and rebuild the
        # cast's exclusions when it lands — never block startup on a network call.
        threading.Thread(target=lambda: refresh_cast_exclusions(app), name="cast-exclusions",
                         daemon=True).start()
        if app.ed_ctx is None:
            app.bus.publish({"type": "log", "who": "system", "text":
                "Audio layer ON (bus mixer), but ED monitoring is OFF — no game events to "
                "drive comms/chatter/music."})
        else:
            app.bus.publish({"type": "log", "who": "system",
                              "text": "Audio layer ON (bus mixer)."})
    except Exception as e:  # noqa: BLE001 — optional; never block startup
        app.audio = None
        app.bus.publish({"type": "log", "who": "system",
                          "text": f"Audio layer failed to start: {e}"})


def _register_edge_cast(app: "App", cast_synth) -> None:  # noqa: ANN001 — a CastSynth
    """(moved from App._register_edge_cast)

    Register the FREE Edge (edge-tts) provider as a cast-eligible backend (issue #15), so any
    NPC/comms/chatter role can use it without touching CastSynth. The cast Edge provider has NO
    fallback — a broken endpoint fails soft to SILENCE for a background line (CastSynth catches
    the error), never to COVAS's own voice. Fail-soft: if edge-tts isn't importable we simply
    don't register, and an 'edge' voice degrades to silence."""
    try:
        from .providers.edge_tts import EdgeTTS
        edge = EdgeTTS(app.cfg)
        cast_synth.registry.register(
            "edge", lambda text, ref: edge.synth_pcm(text, ref or None))
    except Exception as e:  # noqa: BLE001 — optional provider; never block the cast
        app._log("audio", f"Edge cast provider unavailable: {e}")


def _register_azure_cast(app: "App", cast_synth) -> None:  # noqa: ANN001 — a CastSynth
    """(moved from App._register_azure_cast)

    Register official Azure Neural TTS as a cast-eligible backend (issue #17) — the reliable,
    free-tier sibling of Edge. Any NPC/comms/chatter role can use it. Fail-soft: a synth error
    (no key, service down) is caught by CastSynth and the voice degrades to silence.

    EXPERIMENTAL (issue #123): Azure is gated at registration — with [experimental.azure_tts]
    off (the public default) the cast never gains an 'azure' backend, so an 'azure' cast voice
    degrades to silence exactly as it does when edge-tts is unavailable."""
    if not experimental(app.cfg, "azure_tts"):
        return
    try:
        from .providers.azure_tts import AzureTTS
        azure = AzureTTS(app.cfg)
        cast_synth.registry.register(
            "azure", lambda text, ref: azure.synth_pcm(text, ref or None))
    except Exception as e:  # noqa: BLE001 — optional provider; never block the cast
        app._log("audio", f"Azure cast provider unavailable: {e}")


def _register_openai_cast(app: "App", cast_synth) -> None:  # noqa: ANN001 — a CastSynth
    """(moved from App._register_openai_cast)

    Register an OpenAI-compatible TTS backend as cast-eligible (issue #16) — a cheap cloud
    supplemental cast voice. Fail-soft: a synth error (no key, service down) is caught by
    CastSynth and the voice degrades to silence."""
    try:
        from .providers.openai_tts import OpenAITTS
        oai = OpenAITTS(app.cfg)
        cast_synth.registry.register(
            "openai", lambda text, ref: oai.synth_pcm(text, ref or None))
    except Exception as e:  # noqa: BLE001 — optional provider; never block the cast
        app._log("audio", f"OpenAI cast provider unavailable: {e}")


def refresh_cast_exclusions(app: "App") -> None:
    """(moved from App._refresh_cast_exclusions)

    Background: fetch the famous-filtered ElevenLabs voice list and rebuild the cast so a
    ™/unusable voice is dropped from the pool. Fail-soft — the cast works without it."""
    if app.text_only:
        return  # no ElevenLabs key: nothing to fetch, and the call would just error
    try:
        from . import elevenlabs as el
        voices = el.list_voices(app.cfg)
        if app.audio is not None:
            app.audio.rebuild_cast(el_voices=voices)
    except Exception as e:  # noqa: BLE001 — best-effort; no filtering if the API is unreachable
        app._log("audio", f"cast voice-exclusion refresh skipped: {e}")


def build_cast_synth(app: "App"):
    """(moved from App._build_cast_synth — the C10 cast synth router body)

    ElevenLabs for EL/persona voices, local Piper models (cached) for the cast pool. EL synth
    reuses the app's own provider (which keeps a fake/mock run offline); only when the main
    provider is REAL Piper do we build a dedicated ElevenLabs provider for EL voices. Either
    backend may be absent -> that voice fails soft to silence. `App._build_cast_synth` stays a
    thin wrapper onto this so the live TTS-reload path (and its test double) keep working."""
    from .mixer import CastSynth

    el_synth = None
    try:
        from .providers.elevenlabs_tts import ElevenLabsTTS
        from .providers.piper_tts import PiperTTS
        if isinstance(app.tts, PiperTTS):     # Piper is the main voice -> a separate EL cast
            el_prov = ElevenLabsTTS(app.cfg)
            el_synth = lambda text, vid: el_prov.synth_pcm(text, vid)  # noqa: E731
        else:                                   # EL main, or a fake/mock -> reuse it (offline-safe)
            el_synth = app.tts.synth_pcm
    except Exception:  # noqa: BLE001 — no EL available; EL voices fall to silence
        el_synth = None
    cs = CastSynth(el_synth=el_synth, piper_loader=lambda p: load_piper_voice(app, p),
                   log=lambda m: app._log("audio", m))
    _register_edge_cast(app, cs)
    _register_azure_cast(app, cs)
    _register_openai_cast(app, cs)
    return cs


# ---- Elite Dangerous monitoring (DESIGN §5) ---------------------------------
def build_ed_monitoring(app: "App") -> None:
    """(moved from App._start_ed_monitoring)

    Build the shared context + ED-context capability and start the journal/status
    watchers. Fail soft: a missing directory or import problem must not stop the app
    from starting — ED monitoring just stays dark until the next run."""
    try:
        from .ed import (EDContext, JournalWatcher, StatusWatcher,
                         resolve_journal_dir, status_path)
        from .capabilities.ed_context_capability import EDContextCapability
        from .capabilities.on_foot_srv_capability import OnFootSrvCapability
        from .capabilities.engineers_capability import EngineersCapability
        from .capabilities.on_foot_engineering_capability import OnFootEngineeringCapability
        from .capabilities.loadout_capability import LoadoutCapability
        from .capabilities.blueprint_capability import BlueprintCapability
        from .capabilities.materials_capability import MaterialsCapability
        from .capabilities.stored_capability import StoredCapability
        from .nav import copy as _nav_copy

        el = app.cfg.get("elite", {})
        jdir = resolve_journal_dir(app.cfg)
        app.ed_ctx = EDContext(recent_maxlen=int(el.get("recent_events_kept", 25)))
        # Hired NPC-crew registry (issue #125): load the persisted seen-set (path resolved under the
        # data dir by config) so the journal watcher can accumulate the Commander's fighter pilots
        # and the Crew editor can offer them to adopt. Fail-soft: a missing file loads empty.
        from .ed.npc_crew import NpcCrewRegistry
        _npc_reg_path = str((app.cfg.get("crew", {}) or {}).get("npc_registry_file", "") or "").strip()
        if _npc_reg_path:
            app.ed_ctx.set_npc_crew_registry(NpcCrewRegistry.load(_npc_reg_path))
        # Owned-ships registry (issue #134): load the persisted fleet identity (path resolved under
        # the data dir by config) so the journal watcher can fold buy/sell/switch events and the
        # voice CRUD can correct it. Fail-soft: a missing file loads empty.
        from .ed.owned_ships import OwnedShipsRegistry
        from .capabilities.owned_ships_capability import OwnedShipsCapability
        _ships_reg_path = str((app.cfg.get("ships", {}) or {}).get("registry_file", "") or "").strip()
        if _ships_reg_path:
            app.ed_ctx.set_owned_ships_registry(OwnedShipsRegistry.load(_ships_reg_path))
        app.registry.register(EDContextCapability(app.ed_ctx))
        # Owned-ships list + voice CRUD (#134): the conversational surface over the fleet identity.
        # All mutations go through the lock-protected EDContext methods (serialised vs the journal
        # thread), so "what ships do I own" / "I bought a Python" / "remove the Cobra" are safe.
        app.registry.register(OwnedShipsCapability(
            get_owned=app.ed_ctx.owned_ships,
            add_ship=app.ed_ctx.add_owned_ship,
            remove_ship=app.ed_ctx.remove_owned_ship,
            log=lambda m: app._log("owned_ships", m)))
        # On-foot / SRV / exobiology read tools (#54): situational awareness in the modes
        # ED context was silent in. Same live EDContext, mode-specific read answers.
        app.registry.register(OnFootSrvCapability(app.ed_ctx))
        # Ship loadout & engineering (N9): reads the snapshot the journal watcher keeps
        # on EDContext. Registered with monitoring since that's its only data source.
        app.registry.register(LoadoutCapability(
            get_loadout=app.ed_ctx.loadout_snapshot,
            log=lambda m: app._log("loadout", m)))
        # Blueprint / material sourcing (#66): crosses the bundled engineering tables with the
        # live material inventory the journal watcher keeps on EDContext (the Materials event).
        # Registered with monitoring since the journal inventory is its only live data source.
        app.registry.register(BlueprintCapability(
            get_materials=app.ed_ctx.materials_snapshot,
            log=lambda m: app._log("blueprint", m)))
        # Direct materials-inventory queries (#132): "how many X do I have" / bucket listing /
        # what's capped, reading the SAME live inventory getter as BlueprintCapability but without
        # a recipe cross-reference — a separate small capability for the direct-query surface.
        app.registry.register(MaterialsCapability(
            get_materials=app.ed_ctx.materials_snapshot,
            log=lambda m: app._log("materials", m)))
        # Stored ships & modules finder (issue #67): reads the StoredShips/StoredModules
        # snapshots the journal watcher keeps on EDContext. Copies a destination system to
        # the clipboard for a resolved remote ship/module (galaxy-map handoff).
        app.registry.register(StoredCapability(
            get_stored_ships=app.ed_ctx.stored_ships_snapshot,
            get_stored_modules=app.ed_ctx.stored_modules_snapshot,
            get_current_system=app._current_system,
            clipboard=_nav_copy,
            log=lambda m: app._log("stored", m)))
        # Engineers finder (#65): bundled reference table joined with live EngineerProgress
        # for journal-grounded unlock status. Copies an engineer's system for plotting.
        app.registry.register(EngineersCapability(
            get_progress=app.ed_ctx.engineer_progress,
            get_current_system=lambda: app.ed_ctx.snapshot().get("system"),
            clipboard=_nav_copy,
            log=lambda m: app._log("engineers", m)))
        # On-foot (Odyssey suit/weapon) engineering (#73): bundled reference for suits,
        # weapons, modifications and the 13 on-foot engineers. Joins the SAME live
        # EngineerProgress event (on-foot engineers share it) for grounded unlock status.
        app.registry.register(OnFootEngineeringCapability(
            get_progress=app.ed_ctx.engineer_progress,
            get_current_system=lambda: app.ed_ctx.snapshot().get("system"),
            clipboard=_nav_copy,
            log=lambda m: app._log("on_foot_engineering", m)))
        build_carriers(app, jdir)
        build_cg(app, jdir)

        def _err(e: Exception) -> None:  # watcher-thread errors -> log, don't crash
            app.bus.publish({"type": "log", "who": "system",
                              "text": f"ED watcher error: {e}"})

        app._ed_watchers = [
            JournalWatcher(jdir, app.bus, app.ed_ctx,
                           poll_interval=float(el.get("journal_poll_interval", 0.5)),
                           on_error=_err),
            StatusWatcher(status_path(jdir), app.bus, app.ed_ctx,
                          poll_interval=float(el.get("status_poll_interval", 1.0)),
                          on_error=_err),
        ]
        for w in app._ed_watchers:
            w.start()
        app.bus.publish({"type": "log", "who": "system",
                          "text": f"ED monitoring ON — watching {jdir}"})
    except Exception as e:  # noqa: BLE001 — monitoring is optional; never block startup
        app.bus.publish({"type": "log", "who": "system",
                          "text": f"ED monitoring failed to start: {e}"})


# ---- Location & carrier commands (N3) ---------------------------------------
def build_carriers(app: "App", jdir) -> None:
    """(moved from App._start_carriers)

    Register the location/carrier capability (copy current system, where's my fleet /
    squadron carrier). Called from ED monitoring since it reads the journal; fleet-carrier
    state is the live EDContext with a journal-scan fallback, and the squadron lookup goes
    through Spansh by the configured callsign. Fail soft — never blocks startup."""
    try:
        from .capabilities.location_capability import LocationCarrierCapability
        from .nav import (CarrierInfo, carrier_from_journals, copy as _nav_copy,
                          squadron_name_from_journals)

        def _fleet_carrier():
            # Prefer the live watcher state; fall back to a journal scan for a carrier the
            # current session hasn't seen jump yet.
            if app.ed_ctx is not None:
                snap = app.ed_ctx.carrier_snapshot()
                if snap["carrier_name"] or snap["carrier_callsign"] or snap["carrier_system"]:
                    return CarrierInfo(snap["carrier_name"], snap["carrier_callsign"],
                                       snap["carrier_system"], snap["carrier_pending_system"])
            return carrier_from_journals(jdir)

        app.carriers = LocationCarrierCapability(
            get_current_system=app._current_system,
            clipboard=_nav_copy,
            get_fleet_carrier=_fleet_carrier,
            get_squadron_name=lambda: squadron_name_from_journals(jdir),
            log=lambda m: app._log("carrier", m))
        app.registry.register(app.carriers)
        app.bus.publish({"type": "log", "who": "system",
                          "text": "Location & carrier commands ON."})
    except Exception as e:  # noqa: BLE001 — optional; never block startup
        app.carriers = None
        app.bus.publish({"type": "log", "who": "system",
                          "text": f"Location & carrier commands failed to start: {e}"})


# ---- Community Goals (N6) ---------------------------------------------------
def build_cg(app: "App", jdir) -> None:
    """(moved from App._start_cg)

    Register the Community-Goals capability. Journal-primary (works offline); an
    external Inara feed is added only when a key is configured. Fail soft — never blocks
    startup. The Inara key is a restart-level setting, so config is snapshotted here."""
    try:
        from . import firstrun
        from .capabilities.cg_capability import CGCapability
        from .cg import CGConfig, cg_from_journals, fetch_inara_goals
        from .nav import copy as _nav_copy
        from .search import RequestsHttp

        ccfg = CGConfig.from_cfg(app.cfg)
        # The Inara key now lives DPAPI-encrypted in InaraAPIKey.txt (issue #24); reading it here
        # also migrates any legacy inline `[cg].inara_api_key` off plaintext on first run.
        api_key = firstrun.inara_key(app.cfg) or ""
        use_feed = ccfg.source == "inara" and bool(api_key)
        fetch_external = None
        if use_feed:
            http = RequestsHttp()

            def fetch_external():   # stamp the Inara envelope timestamp per call
                return fetch_inara_goals(http, api_key=api_key,
                                         timestamp=_dt.datetime.now().isoformat())

        app.cg = CGCapability(
            get_journal_goals=lambda: cg_from_journals(jdir),
            get_current_system=app._current_system,
            clipboard=_nav_copy,
            fetch_external=fetch_external,
            log=lambda m: app._log("cg", m))
        app.registry.register(app.cg)
        src = "feed: Inara" if use_feed else "journal-only (no Inara key)"
        app.bus.publish({"type": "log", "who": "system",
                          "text": f"Community Goals ON ({src})."})
    except Exception as e:  # noqa: BLE001 — optional; never block startup
        app.cg = None
        app.bus.publish({"type": "log", "who": "system",
                          "text": f"Community Goals failed to start: {e}"})


# ---- Proactive callouts (DESIGN §5) -----------------------------------------
def build_proactive(app: "App") -> None:
    """(moved from App._start_proactive)

    Build the proactive-callout capability and start the event pump that feeds bus
    events to capability on_event hooks. Fail soft: a startup problem just leaves
    callouts off. Proactive needs ED monitoring for its events — warn (don't fail) if
    that's not on, since the two are independently toggled."""
    try:
        from .capabilities.proactive_capability import (ProactiveCapability,
                                                        ProactivePolicy)
        policy = ProactivePolicy.from_cfg(app.cfg)
        app.proactive = ProactiveCapability(
            policy, app._speak_proactive,
            log=lambda reason: app._log("proactive", reason))
        app.registry.register(app.proactive)
        app._start_event_pump()
        if app.ed_ctx is None:
            app.bus.publish({"type": "log", "who": "system", "text":
                "Proactive callouts ON, but ED monitoring is OFF — no events to react to."})
        else:
            app.bus.publish({"type": "log", "who": "system",
                              "text": "Proactive callouts ON."})
    except Exception as e:  # noqa: BLE001 — optional; never block startup
        app.bus.publish({"type": "log", "who": "system",
                          "text": f"Proactive callouts failed to start: {e}"})


# ---- Route callouts (DESIGN §5, N4) -----------------------------------------
def build_route(app: "App") -> None:
    """(moved from App._start_route)

    Build + register the route-callout capability and ensure the event pump is running.
    Fail soft: a startup problem just leaves route callouts off. Needs ED monitoring for
    its events (warn, don't fail, if that's off — the two are independently toggled)."""
    try:
        from .capabilities.route_capability import RouteCalloutCapability, RouteConfig
        from .ed import read_navroute, resolve_journal_dir

        rcfg = RouteConfig.from_cfg(app.cfg)
        jdir = resolve_journal_dir(app.cfg)
        # Route callouts honour the shared proactive mute ('stop the callouts') when
        # proactive is enabled; otherwise there's nothing muting them.
        is_muted = ((lambda: app.proactive.policy.muted) if app.proactive is not None
                    else (lambda: False))
        app.route = RouteCalloutCapability(
            rcfg,
            speak_line=app._speak_proactive_line,
            load_navroute=lambda: read_navroute(jdir),
            is_muted=is_muted,
            log=lambda m: app._log("route", m))
        app.route.prime()
        app.registry.register(app.route)
        app._start_event_pump()
        every = rcfg.every_n
        if app.ed_ctx is None:
            app.bus.publish({"type": "log", "who": "system", "text":
                "Route callouts ON, but ED monitoring is OFF — no route events to react to."})
        else:
            app.bus.publish({"type": "log", "who": "system",
                              "text": f"Route callouts ON (jumps-remaining every {every})."})
    except Exception as e:  # noqa: BLE001 — optional; never block startup
        app.route = None
        app.bus.publish({"type": "log", "who": "system",
                          "text": f"Route callouts failed to start: {e}"})


# ---- Companion HUD (issue #47) ----------------------------------------------
def build_hud(app: "App") -> None:
    """(moved from App._start_hud)

    Register the always-on HUD capability and ensure the event pump is running so it
    hears status/checklist/route/settings events. The capability keeps a pure HudModel and
    only opens a window when [hud].enabled AND a display are present — off by default, so
    this is inert until the Commander opts in (Settings page or 'turn the HUD on'). Fail
    soft: any wiring problem just leaves the HUD off; it must never block startup.

    EXPERIMENTAL (issue #123): the whole HUD (desktop / VR / web surfaces) is gated at
    registration behind [experimental.hud] (off by default). While off, NO HudCapability or
    HudPlacementCapability is registered — the overlay is genuinely absent (no tool, no help),
    not merely a shown window that's hidden, and it can't be brought up by voice or Settings."""
    if not experimental(app.cfg, "hud"):
        return
    try:
        from .capabilities.hud_capability import (
            HudCapability, HudModel, WebHudView, checklist_line)
        from .capabilities.vr_hud import make_vr_view
        from .ed import read_navroute, resolve_journal_dir

        jdir = resolve_journal_dir(app.cfg)
        model = HudModel(
            checklist_provider=lambda: checklist_line(app.checklist),
            load_navroute=lambda: read_navroute(jdir),
            state=app.state,
        )
        # The VR overlay is a SECOND view over the same model — placement is read live from
        # config when the sink is (lazily) created, so it reflects the current settings.
        def _vr_factory(provider):
            return make_vr_view(provider, app._vr_hud_placement(),
                                log=lambda m: app._log("hud", m))
        # The web overlay is a THIRD view over the same model — a transparent /hud page that
        # OpenKneeboard renders in-headset on any OpenXR runtime (#103). The page reads live
        # data straight from /api/hud, so the "view" holds nothing; the factory just returns
        # None (surface stays off) unless the control panel is actually serving /hud.
        def _web_factory(provider):
            if not app._web_ui_running:
                return None  # needs run_covas_ui.py; headless run_covas.py serves no /hud
            host = app.cfg.get("ui", {}).get("host", "127.0.0.1")
            port = app.cfg.get("ui", {}).get("port", 8765)
            return WebHudView(f"http://{host}:{port}/hud",
                              log=lambda m: app._log("hud", m))
        app.hud = HudCapability(
            model,
            is_enabled=app._hud_enabled,
            vr_is_enabled=app._vr_hud_enabled,
            vr_view_factory=_vr_factory,
            web_is_enabled=app._web_hud_enabled,
            web_view_factory=_web_factory,
            log=lambda m: app._log("hud", m))
        app.registry.register(app.hud)
        # Voice repositioning for the VR overlay (nudges + look-to-place). Reuses the HUD's
        # config + the app's live-apply settings path; pin reads the HMD gaze from the live
        # overlay. Registered even when the VR HUD is off — the tool just reports it's not up.
        from .capabilities.hud_placement_capability import HudPlacementCapability
        app.registry.register(HudPlacementCapability(
            get_hud=lambda: app.cfg.get("hud", {}),
            apply_patch=app.update_settings,
            pin=lambda: app.hud.pin_vr_here() if app.hud is not None else None,
            log=lambda m: app._log("hud", m)))
        # A SHOWN HUD (either surface) repaints from live bus events (status/checklist/route/
        # callout), so it needs the shared event pump — but only when actually enabled. The
        # toggle itself is driven directly (see _reconcile_hud), so a disabled HUD adds no
        # pump thread and can still be brought up live by voice/Settings.
        if app._hud_enabled() or app._vr_hud_enabled() or app._web_hud_enabled():
            app._start_event_pump()
    except Exception as e:  # noqa: BLE001 — optional; never block startup
        app.hud = None
        app.bus.publish({"type": "log", "who": "system",
                          "text": f"Companion HUD failed to start: {e}"})


# ---- Persistent memory capture (issue #60) ----------------------------------
def build_memory(app: "App") -> None:
    """(moved from App._start_memory)

    Wire persistent memory (CAPTURE #60 + RECALL #61): register a capability that
    (a) captures curated journal milestones off the bus (deterministic describers — no LLM
    per event), (b) exposes a 'remember that' store tool the LLM calls in-turn, (c) exposes a
    'recall_memory' tool for explicit look-ups, and (d) provides `recall_block` so the worker
    loop can prepend relevant facts to a recall-referencing turn's USER message (cache-safe —
    never the system prompt). Capture dedups + caps the git-ignored file; recall is keyword/tag
    only (free, offline). Opt-in ([memory].enabled). Fail soft — any wiring problem just leaves
    memory off; it must never block startup."""
    try:
        from .capabilities.memory_capability import MemoryCapability
        from .memory import MemoryCapture, Retriever, store_from_config

        store = store_from_config(app.cfg)
        cap = int(app.cfg.get("memory", {}).get("cap", 500))
        capture = MemoryCapture(store, cap=cap, log=lambda m: app._log("memory", m))
        # Recall side (#61): keyword/tag retriever over the SAME store — embedder stays None
        # (the default free, offline path), so recall never costs money or touches the network.
        retriever = Retriever(store, embedder=None)
        app.memory = MemoryCapability(capture, retriever,
                                       log=lambda m: app._log("memory", m))
        app.registry.register(app.memory)
        # Journal-highlight capture rides the shared bus/event pump (live-only, so an
        # existing journal isn't re-captured on every launch — the watcher primes context
        # WITHOUT publishing). The 'remember that' tool works regardless of the pump.
        app._start_event_pump()
        app.bus.publish({"type": "log", "who": "system",
                          "text": "Persistent memory ON (capture + recall)."})
    except Exception as e:  # noqa: BLE001 — optional; never block startup
        app.memory = None
        app.bus.publish({"type": "log", "who": "system",
                          "text": f"Memory failed to start: {e}"})


# ---- Shared ED binds / executor / focuser -----------------------------------
# The mutable handles live on `App` (app._binds_cache / _shared_executor / _shared_focuser); these
# factories parse/build them ONCE and cache them there, so keybinds, reflex, honk, comms and macros
# all share the SAME executor + abort + parsed binds — one hard abort releases every held key.
def ed_binds(app: "App") -> dict:
    """(moved from App._ed_binds)

    Parse the active ED key bindings once, shared by keybinds + auto-honk. Returns {}
    (with a logged reason) if the .binds file can't be located/read, so a capability
    degrades to a clear 'bind it in-game' message instead of vanishing silently."""
    if app._binds_cache is None:
        from .keybinds import BindsError, load_binds
        try:
            app._binds_cache = load_binds(app.cfg)
        except BindsError as e:
            app._binds_cache = {}
            app.bus.publish({"type": "log", "who": "system", "text": f"Keybinds: {e}"})
    return app._binds_cache


def key_executor(app: "App"):
    """(moved from App._key_executor)

    Build (once) the shared scancode executor used by both keybind actions and auto-honk,
    so a hard abort releases keys held by either. Raises ExecutorError off-Windows (the
    callers catch it and leave the feature off)."""
    if app._shared_executor is None:
        from .keybinds.executor import KeyExecutor
        app._shared_executor = KeyExecutor()
    return app._shared_executor


def window_focuser(app: "App"):
    """(moved from App._window_focuser)

    Build (once) the shared window focuser used to bring ED to the foreground before
    injection (#105). Returns None off-Windows (Win32Backend raises there) or if it can't be
    built — every caller treats None as 'focus feature absent' and degrades to ambient focus,
    so this never blocks startup. Unlike the executor it must NOT raise: a focus feature that
    won't build is optional, whereas a keystroke executor that won't build means keybinds
    genuinely can't run."""
    if app._shared_focuser is None:
        try:
            from .keybinds.focus import WindowFocuser
            app._shared_focuser = WindowFocuser(
                log=lambda msg: app._log("keybind", msg))
        except Exception as e:  # noqa: BLE001 — off-Windows/unavailable -> feature simply absent
            app.bus.publish({"type": "log", "who": "system",
                              "text": f"Window focus unavailable: {e}"})
            app._shared_focuser = None
    return app._shared_focuser


# ---- Keybind automation (DESIGN §6) -----------------------------------------
def build_keybinds(app: "App") -> None:
    """(moved from App._start_keybinds)

    Build the keybind capability: resolve + parse the active ED bindings, build the
    scancode executor, and register the capability behind its safety layer. Fail soft —
    a missing bindings file or a non-Windows host just leaves ship controls off; it must
    never block startup. The combat guard reads the live ED context snapshot (so keybinds
    needs [elite].enabled to positively confirm it's safe to act)."""
    try:
        from .capabilities.keybind_capability import KeybindConfig, KeybindCapability

        kcfg = KeybindConfig.from_cfg(app.cfg)
        binds = ed_binds(app)
        executor = key_executor(app)   # raises ExecutorError off-Windows -> caught below
        snapshot = ((lambda: app.ed_ctx.snapshot()) if app.ed_ctx is not None else None)
        focuser = window_focuser(app)   # None off-Windows -> focus_game absent, no auto-focus
        app.keybinds = KeybindCapability(
            binds=binds, executor=executor, config=kcfg,
            status_snapshot=snapshot, focuser=focuser,
            abort_event=app._keybind_abort,   # shared with custom macros (#50)
            log=lambda msg: app._log("keybind", msg))
        app.registry.register(app.keybinds)

        # Report per-macro readiness so the manual test knows what's wired.
        for macro in app.keybinds._allowed_macros():
            if macro.steps:
                # Sequence macro (#33): usable iff every key-pressing step is bound to a key.
                missing = [s.action for s in macro.steps if s.action
                           and (binds.get(s.action) is None or not binds[s.action].usable)]
                detail = (f"{macro.name} (sequence) READY" if not missing
                          else f"{macro.name} (sequence) UNUSABLE (bind: {', '.join(missing)})")
            else:
                b = binds.get(macro.action)
                if b is not None and b.usable:
                    detail = f"{macro.name} -> {b.key}"
                else:
                    detail = f"{macro.name} UNUSABLE (no keyboard bind for {macro.action})"
            app.bus.publish({"type": "log", "who": "system",
                              "text": f"Keybind macro: {detail}"})
        guard = "on" if kcfg.combat_guard else "off"
        if kcfg.combat_guard and app.ed_ctx is None:
            app.bus.publish({"type": "log", "who": "system", "text":
                "Keybinds ON but ED monitoring is OFF — combat guard can't verify safety, "
                "so actions will be refused until [elite].enabled."})
        else:
            app.bus.publish({"type": "log", "who": "system",
                              "text": f"Keybinds ON (confirmation "
                                      f"{'on' if kcfg.require_confirmation else 'off'}, "
                                      f"combat guard {guard})."})
    except Exception as e:  # noqa: BLE001 — optional; never block startup
        app.keybinds = None
        app.bus.publish({"type": "log", "who": "system",
                          "text": f"Keybinds failed to start: {e}"})


# ---- Tier-2 combat reflexes (#36) -------------------------------------------
def build_reflex(app: "App") -> None:
    """(moved from App._start_reflex)

    Build the Tier-2 combat-reflex capability: parse the active ED bindings (shared),
    build the shared scancode executor, and register the capability behind the SEPARATE
    combat-permissive guard. Fail soft — a missing bindings file or a non-Windows host just
    leaves reflexes off; it must never block startup. The guard reads the live ED context
    snapshot (so it needs [elite].enabled to positively confirm you're IN danger before
    firing a reflex)."""
    try:
        from .capabilities.reflex_capability import (
            REFLEX_ACTIONS, ReflexCapability, ReflexConfig)

        rcfg = ReflexConfig.from_cfg(app.cfg)
        binds = ed_binds(app)
        executor = key_executor(app)   # raises ExecutorError off-Windows -> caught below
        snapshot = ((lambda: app.ed_ctx.snapshot()) if app.ed_ctx is not None else None)
        app.reflex = ReflexCapability(
            binds=binds, executor=executor, config=rcfg,
            status_snapshot=snapshot,
            log=lambda msg: app._log("reflex", msg))
        app.registry.register(app.reflex)

        # Report per-reflex readiness so the manual test knows what's wired.
        for r in app.reflex._allowed_reflexes():
            b = binds.get(r.action)
            if b is not None and b.usable:
                detail = f"{r.name} -> {b.key}"
            else:
                detail = f"{r.name} UNUSABLE (no keyboard bind for {r.action})"
            app.bus.publish({"type": "log", "who": "system",
                              "text": f"Reflex: {detail}"})
        if rcfg.combat_guard and app.ed_ctx is None:
            app.bus.publish({"type": "log", "who": "system", "text":
                "Tier-2 reflexes ON but ED monitoring is OFF — the combat-permissive guard "
                "can't confirm you're in danger, so reflexes will be refused until "
                "[elite].enabled."})
        else:
            app.bus.publish({"type": "log", "who": "system", "text":
                f"Tier-2 combat reflexes ON (combat-permissive guard "
                f"{'on' if rcfg.combat_guard else 'off'}; allowlist: "
                f"{', '.join(rcfg.allowlist) or 'empty'})."})

        # AMBIENT auto-reflex layer (#37): fire the same reflexes automatically off Status/
        # journal thresholds, no voice. Opt-in per reflex ([reflex.auto.<name>].enabled) and
        # off by default. Shares the binds/executor/snapshot + the combat-permissive guard.
        build_auto_reflex(app, binds, executor)
    except Exception as e:  # noqa: BLE001 — optional; never block startup
        app.reflex = None
        app.bus.publish({"type": "log", "who": "system",
                          "text": f"Tier-2 reflexes failed to start: {e}"})


def build_auto_reflex(app: "App", binds: dict, executor: object) -> None:
    """(moved from App._start_auto_reflex)

    Build + register the ambient auto-reflex capability when opted in ([reflex.auto].
    enabled). Fail soft: a startup problem just leaves the automatic layer off — the verbal
    reflexes still work. Needs the event pump (it reacts to bus ed_events) and ED monitoring
    (for the trigger snapshot + the guard).

    EXPERIMENTAL (issue #123): the AUTOMATIC layer is gated at registration behind
    [experimental.auto_reflex] (off by default) — so the public build never registers an
    auto-reflex capability even with [reflex.auto].enabled set. The VERBAL Tier-2 reflex path
    (build_reflex) is unaffected."""
    if not experimental(app.cfg, "auto_reflex"):
        return
    from .capabilities.auto_reflex_capability import AutoReflexCapability, AutoReflexConfig
    from .capabilities.reflex_capability import REFLEX_ACTIONS

    acfg = AutoReflexConfig.from_cfg(app.cfg)
    if not acfg.enabled:
        return
    snapshot = ((lambda: app.ed_ctx.snapshot()) if app.ed_ctx is not None else None)
    app.auto_reflex = AutoReflexCapability(
        binds=binds, executor=executor, config=acfg,
        status_snapshot=snapshot,
        log=lambda msg: app._log("reflex", msg))
    app.registry.register(app.auto_reflex)
    app._start_event_pump()

    enabled = app.auto_reflex.enabled_reflexes()
    if not enabled:
        app.bus.publish({"type": "log", "who": "system", "text":
            "Auto-reflexes ON but no reflex is enabled — set [reflex.auto.<name>].enabled "
            "(heat_sink, chaff) to opt one in."})
        return
    for trig in enabled:
        b = binds.get(REFLEX_ACTIONS[trig.name].action)
        usable = b is not None and b.usable
        detail = (f"{trig.name} -> {b.key}" if usable
                  else f"{trig.name} UNUSABLE (no keyboard bind for "
                       f"{REFLEX_ACTIONS[trig.name].action})")
        app.bus.publish({"type": "log", "who": "system",
                          "text": f"Auto-reflex: {detail} ({trig.summary})"})
    if app.ed_ctx is None:
        app.bus.publish({"type": "log", "who": "system", "text":
            "Auto-reflexes ON but ED monitoring is OFF — no trigger events, and the guard "
            "can't confirm danger, so nothing will fire until [elite].enabled."})


# ---- Send in-game comms (issue #49) -----------------------------------------
def build_comms(app: "App") -> None:
    """(moved from App._start_comms)

    Build the comms-send capability: reuse the shared ED binds + scancode executor, wire
    the clipboard-paste text injector, and register it behind the read-back-before-send gate.
    Fail soft — a missing bindings file or a non-Windows host (no executor) just leaves comms
    off; it must never block startup. No combat/ED-monitoring dependency: the safety here is
    the mandatory read-back confirmation, not a game-state guard."""
    try:
        from .capabilities.comms_capability import CommsSendCapability, CommsSendConfig
        from .nav import clipboard

        from .capabilities.keybind_capability import KeybindConfig

        ccfg = CommsSendConfig.from_cfg(app.cfg)
        binds = ed_binds(app)
        executor = key_executor(app)   # raises ExecutorError off-Windows -> caught below
        # Auto-focus before a comms send is gated on [keybinds].focus_before_inject (#105):
        # only pass a focuser when it's on, so with it off the injector keeps the old
        # ambient-focus behaviour. None off-Windows regardless.
        focuser = (window_focuser(app)
                   if KeybindConfig.from_cfg(app.cfg).focus_before_inject else None)
        app.comms = CommsSendCapability(
            binds=binds, executor=executor, config=ccfg,
            copy=clipboard.copy, focuser=focuser,
            log=lambda msg: app._log("comms", msg))
        app.registry.register(app.comms)

        # Report readiness so the manual test knows what's wired: the open-comms bind and
        # each configured channel-select bind.
        ob = binds.get(ccfg.open_bind)
        if ob is not None and ob.usable:
            app.bus.publish({"type": "log", "who": "system",
                              "text": f"Comms: open box {ccfg.open_bind} -> {ob.key}"})
        else:
            app.bus.publish({"type": "log", "who": "system", "text":
                f"Comms UNUSABLE (bind {ccfg.open_bind} to a key to open the chat box)."})
        for ch, token in ccfg.channel_binds.items():
            if not token:
                continue
            b = binds.get(token)
            detail = (f"{ch} -> {b.key}" if b is not None and b.usable
                      else f"{ch} UNUSABLE (no keyboard bind for {token})")
            app.bus.publish({"type": "log", "who": "system",
                              "text": f"Comms channel: {detail}"})
        app.bus.publish({"type": "log", "who": "system", "text":
            "Comms send ON (read-back-before-send confirmation required)."})
    except Exception as e:  # noqa: BLE001 — optional; never block startup
        app.comms = None
        app.bus.publish({"type": "log", "who": "system",
                          "text": f"Comms send failed to start: {e}"})


# ---- Custom macros (#50) ----------------------------------------------------
def build_macros(app: "App") -> None:
    """(moved from App._start_macros)

    Build + register the custom-macro capability: the persisted spec store, the shared
    binds/executor/abort, and the allowlist provider the compiler validates against. Fail
    soft — a missing bindings file or non-Windows host just leaves authoring able to SAVE and
    VALIDATE macros (offline), degrading only the actual key-press at run time to a spoken
    'bind it in-game'. Ensures the event pump so triggered macros can auto-run. The combat
    guard reads the live ED snapshot (needs [elite].enabled to positively confirm safety)."""
    try:
        from .capabilities.keybind_capability import KeybindConfig
        from .capabilities.macro_capability import MacroCapability, MacroConfig
        from .macros.store import store_from_config

        mcfg = MacroConfig.from_cfg(app.cfg)
        store = store_from_config(app.cfg)
        binds = ed_binds(app)
        executor = key_executor(app)   # raises ExecutorError off-Windows -> caught below
        snapshot = ((lambda: app.ed_ctx.snapshot()) if app.ed_ctx is not None else None)
        # Live allowlist: a custom macro may only use actions the Commander has opted into via
        # [keybinds].allowlist, read fresh so a live settings change is honoured at run time.
        allowlist = (lambda: frozenset(KeybindConfig.from_cfg(app.cfg).allowlist))
        app.macros = MacroCapability(
            store=store, config=mcfg, binds=binds, executor=executor,
            allowlist=allowlist, status_snapshot=snapshot,
            abort_event=app._keybind_abort,          # one hard abort covers keybinds + macros
            speak=app._speak_proactive_line,         # triggered arm prompt / outcome
            log=lambda msg: app._log("macro", msg))
        app.registry.register(app.macros)
        app._start_event_pump()                      # triggered macros need the bus pump

        saved = store.all()
        triggered = sum(1 for s in saved if s.trigger)
        if app.ed_ctx is None:
            app.bus.publish({"type": "log", "who": "system", "text":
                "Custom macros ON, but ED monitoring is OFF — the combat guard can't verify "
                "safety and triggers won't fire, so macros will be refused until "
                "[elite].enabled."})
        else:
            app.bus.publish({"type": "log", "who": "system", "text":
                f"Custom macros ON ({len(saved)} saved, {triggered} triggered; confirmation "
                f"{'on' if mcfg.require_confirmation else 'off'}, combat guard "
                f"{'on' if mcfg.combat_guard else 'off'})."})
    except Exception as e:  # noqa: BLE001 — optional; never block startup
        app.macros = None
        app.bus.publish({"type": "log", "who": "system",
                          "text": f"Custom macros failed to start: {e}"})


# ---- Auto-honk (N5) ---------------------------------------------------------
def build_honk(app: "App") -> None:
    """(moved from App._start_honk)

    Build + register the auto-honk capability and ensure the event pump is running. Fail
    soft — a missing bindings file or a non-Windows host just leaves it off; it must never
    block startup. Needs ED monitoring for the arrival event, the current fire group, and
    the combat guard, so warn (don't fail) if that's off."""
    try:
        from .capabilities.honk_capability import HonkCapability, HonkConfig

        hcfg = HonkConfig.from_cfg(app.cfg)
        binds = ed_binds(app)
        executor = key_executor(app)   # raises ExecutorError off-Windows -> caught below
        snapshot = ((lambda: app.ed_ctx.snapshot()) if app.ed_ctx is not None else None)
        app.honk = HonkCapability(
            hcfg, binds=binds, executor=executor,
            status_snapshot=snapshot,
            speak=app._speak_proactive_line,   # spoken Surface-Scanner-misfire warning (K2)
            log=lambda msg: app._log("honk", msg))
        app.registry.register(app.honk)
        app._start_event_pump()

        fire = binds.get(hcfg.fire_action)
        fire_ok = fire is not None and fire.usable
        if app.ed_ctx is None:
            app.bus.publish({"type": "log", "who": "system", "text":
                "Auto-honk ON, but ED monitoring is OFF — no arrival events, and the "
                "combat guard can't verify safety, so it won't fire until [elite].enabled."})
        elif not fire_ok:
            app.bus.publish({"type": "log", "who": "system", "text":
                f"Auto-honk ON, but {hcfg.fire_action} has no keyboard binding — bind the "
                "Discovery Scanner's fire button to a key in-game so COVAS can honk."})
        else:
            app.bus.publish({"type": "log", "who": "system",
                              "text": f"Auto-honk ON (probe + hold {hcfg.fire_action} on the "
                                      f"current fire group; backs out of a Surface-Scanner "
                                      f"misfire; combat guard "
                                      f"{'on' if hcfg.combat_guard else 'off'})."})
    except Exception as e:  # noqa: BLE001 — optional; never block startup
        app.honk = None
        app.bus.publish({"type": "log", "who": "system",
                          "text": f"Auto-honk failed to start: {e}"})


# ---- Find-closest-module ----------------------------------------------------
def build_nav(app: "App") -> None:
    """(moved from App._start_nav)

    Build + register the find-closest-module capability. Fail soft: a startup problem
    just leaves the feature off. The Spansh HTTP client is built here (composition root)
    so tests never need it; current-system is read live from ED context with a journal
    fallback."""
    try:
        from .nav import RequestsHttp, ModuleIndex
        from .capabilities.find_closest_capability import NavConfig, FindClosestCapability

        ncfg = NavConfig.from_cfg(app.cfg)
        # Live taxonomy so newly-released Frontier modules are findable without a CSV
        # refresh: reconciled against the bundled taxonomy on a background startup thread
        # (below), and resolution falls back to the bundle until/if that fetch lands.
        module_index = ModuleIndex()
        app.nav = FindClosestCapability(
            ncfg, http=RequestsHttp(),
            get_current_system=app._current_system,
            get_current_ship_size=app._current_ship_pad_size,
            module_index=module_index,
            log=lambda msg: app._log("nav", msg))
        app.registry.register(app.nav)
        if app.ed_ctx is None:
            app.bus.publish({"type": "log", "who": "system", "text":
                f"Find-closest-module ON (pad {ncfg.default_pad_size or 'any'}); ED "
                "monitoring is OFF, so current system falls back to the newest journal."})
        else:
            app.bus.publish({"type": "log", "who": "system",
                              "text": f"Find-closest-module ON "
                                      f"(pad {ncfg.default_pad_size or 'any'})."})
        threading.Thread(target=lambda: refresh_module_index(app, module_index),
                         name="module-index-refresh", daemon=True).start()
    except Exception as e:  # noqa: BLE001 — optional; never block startup
        app.nav = None
        app.bus.publish({"type": "log", "who": "system",
                          "text": f"Find-closest-module failed to start: {e}"})


def refresh_module_index(app: "App", module_index) -> None:
    """(moved from App._refresh_module_index)

    Background startup task: fetch Spansh's current module list and log any modules newer
    than the bundled taxonomy (they're now findable). Fail-soft — off the hot path, never
    blocks the voice loop, and a fetch failure just leaves the bundle in charge."""
    try:
        module_index.refresh()
        new = module_index.extra_names()
        if new:
            app._log("nav", f"live taxonomy added {len(new)} module(s) not in the "
                             f"bundle: {', '.join(new)}.")
    except Exception as e:  # noqa: BLE001 — best-effort; the bundled taxonomy still works
        app._log("nav", f"live taxonomy refresh failed: {e}")


# ---- Find-closest-ship ------------------------------------------------------
def build_ship_nav(app: "App") -> None:
    """(moved from App._start_ship_nav)

    Build + register the find-closest-ship capability (shares [nav]). Fail soft: a
    startup problem just leaves the feature off. Same seams as find-closest-module —
    Spansh client built here, current-system read live with a journal fallback."""
    try:
        from .nav import RequestsHttp, ShipIndex
        from .nav.edsm_stock import EdsmStockLookup
        from .capabilities.find_closest_capability import FindClosestShipCapability, NavConfig
        from .ed.journal import resolve_journal_dir
        from .ed.shipyard import read_shipyard_snapshot

        ncfg = NavConfig.from_cfg(app.cfg)
        # Live roster so newly-released Frontier hulls are findable without a code change:
        # the index is reconciled against the bundled roster on a background startup thread
        # (below), and resolution falls back to the bundle until/if that fetch lands.
        ship_index = ShipIndex()
        # Ground-truth stock for the last-visited shipyard (Spansh lists the CATALOG, not
        # stock). Re-read per lookup — the file is tiny and ED rewrites it on each visit.
        shipyard_path = resolve_journal_dir(app.cfg) / "Shipyard.json"
        # EDSM current-stock check for every OTHER station — what makes the answer agree
        # with Inara (Spansh unions ships into a catalog; EDSM keeps the live snapshot).
        stock_lookup = (EdsmStockLookup(RequestsHttp(), user_agent=ncfg.user_agent)
                        if ncfg.verify_stock else None)
        app.ship_nav = FindClosestShipCapability(
            ncfg, http=RequestsHttp(),
            get_current_system=app._current_system,
            get_current_ship_size=app._current_ship_pad_size,
            get_local_shipyard=lambda: read_shipyard_snapshot(shipyard_path),
            stock_lookup=stock_lookup,
            ship_index=ship_index,
            log=lambda msg: app._log("ship_nav", msg))
        app.registry.register(app.ship_nav)
        app.bus.publish({"type": "log", "who": "system",
                          "text": f"Find-closest-ship ON (pad {ncfg.default_pad_size or 'any'}, "
                                  f"stock check {'EDSM' if stock_lookup else 'off'})."})
        threading.Thread(target=lambda: refresh_ship_index(app, ship_index),
                         name="ship-index-refresh", daemon=True).start()
    except Exception as e:  # noqa: BLE001 — optional; never block startup
        app.ship_nav = None
        app.bus.publish({"type": "log", "who": "system",
                          "text": f"Find-closest-ship failed to start: {e}"})


def refresh_ship_index(app: "App", ship_index) -> None:
    """(moved from App._refresh_ship_index)

    Background startup task: fetch Spansh's current ship list and log any hulls newer
    than the bundled roster (they're now findable). Fail-soft — off the hot path, never
    blocks the voice loop, and a fetch failure just leaves the bundle in charge."""
    try:
        ship_index.refresh()
        new = ship_index.extra_names()
        if new:
            app._log("ship_nav", f"live roster added {len(new)} ship(s) not in the "
                                  f"bundle: {', '.join(new)}.")
    except Exception as e:  # noqa: BLE001 — best-effort; the bundled roster still works
        app._log("ship_nav", f"live roster refresh failed: {e}")


# ---- Star-system search -----------------------------------------------------
def build_system_search(app: "App") -> None:
    """(moved from App._start_system_search)

    Build + register the star-system search capability. Fail soft: a startup problem
    just leaves the feature off. The Spansh HTTP client is built here (composition root)
    so tests never need it; current-system is read live from ED context with a journal
    fallback (same seam as find-closest)."""
    try:
        from .search import RequestsHttp
        from .capabilities._search_support import SearchConfig
        from .capabilities.search_family import SystemSearchCapability
        scfg = SearchConfig.from_cfg(app.cfg, "star_systems")
        app.system_search = SystemSearchCapability(
            scfg, http=RequestsHttp(),
            get_current_system=app._current_system,
            log=lambda msg: app._log("systems", msg))
        app.registry.register(app.system_search)
        app.bus.publish({"type": "log", "who": "system", "text": "Star-system search ON."})
    except Exception as e:  # noqa: BLE001 — optional; never block startup
        app.system_search = None
        app.bus.publish({"type": "log", "who": "system",
                          "text": f"Star-system search failed to start: {e}"})


# ---- Remaining Spansh search categories (stations/factions/signals/misc) ----
def build_searches(app: "App") -> None:
    """(moved from App._start_searches)

    Build + register the four remaining LLM-native Spansh search capabilities. Fail
    soft: a startup problem just leaves them off. One [search] toggle enables the group;
    each shares the injected HTTP client + current-system seam."""
    try:
        from .search import RequestsHttp
        from .search.faction_index import FactionIndex
        from .capabilities._search_support import SearchConfig
        from .capabilities.search_family import SEARCH_GROUP, SpecSearchCapability

        scfg = SearchConfig.from_cfg(app.cfg, "search")
        http = RequestsHttp()
        # One faction-name index shared by the faction-using capabilities (lazily fetched
        # from Spansh on first use, then cached) so a mistranscribed faction name resolves
        # to its exact string instead of returning zero systems.
        factions = FactionIndex()
        # Loop the declarative family table (issue #111), in the SAME registration order as
        # before, so the tools() ordering prompt caching keys off is unchanged. Every category
        # gets the shared index — FactionIndex is lazy, so a faction-less category never
        # touches it.
        app.searches = [
            SpecSearchCapability(desc, scfg, http=http, factions=factions,
                                 get_current_system=app._current_system,
                                 log=lambda msg: app._log("search", msg))
            for desc in SEARCH_GROUP
        ]
        for cap in app.searches:
            app.registry.register(cap)
        app.bus.publish({"type": "log", "who": "system",
                          "text": "Search categories ON (stations, minor factions, "
                                  "signals, faction states)."})
    except Exception as e:  # noqa: BLE001 — optional; never block startup
        app.searches = []
        app.bus.publish({"type": "log", "who": "system",
                          "text": f"Search categories failed to start: {e}"})


# ---- Body / bio-geo signal finder (#68) -------------------------------------
def build_bodies(app: "App") -> None:
    """(moved from App._start_bodies)

    Build + register the body finder (#68) — nearest body by type / biological signal over
    the `bodies/search` endpoint. Fail soft: a startup problem just leaves it off. Its own
    `[bodies]` toggle (defaults OFF); shares the injected HTTP client + current-system seam;
    the nearest match's system is copied to the clipboard for the galaxy map."""
    try:
        from .search import RequestsHttp
        from .capabilities._search_support import SearchConfig
        from .capabilities.search_family import BodySearchCapability

        bcfg = SearchConfig.from_cfg(app.cfg, "bodies")
        app.body_search = BodySearchCapability(
            bcfg, http=RequestsHttp(),
            get_current_system=app._current_system,
            log=lambda msg: app._log("bodies", msg))
        app.registry.register(app.body_search)
        app.bus.publish({"type": "log", "who": "system",
                          "text": "Body finder ON (nearest world / biological signal)."})
    except Exception as e:  # noqa: BLE001 — optional; never block startup
        app.body_search = None
        app.bus.publish({"type": "log", "who": "system",
                          "text": f"Body finder failed to start: {e}"})


# ---- Route planning (#41 foundation proof) ----------------------------------
def build_route_plan(app: "App") -> None:
    """(moved from App._start_route_plan)

    Build + register the trade-route planner (#41), the foundation proof for the Spansh
    route client + galaxy-map plot handoff. Fail soft — a startup problem just leaves it off.
    Shares the current-system/station seams; the plot handoff copies the next stop to the
    clipboard until the galaxy-map keybind automation (#32) lands."""
    try:
        from .search import RequestsHttp
        from .capabilities.route_plan_capability import RoutePlanCapability, RoutePlanConfig

        rcfg = RoutePlanConfig.from_cfg(app.cfg)
        app.route_plan = RoutePlanCapability(
            rcfg, http=RequestsHttp(),
            get_current_system=app._current_system,
            get_current_station=app._current_station,
            log=lambda msg: app._log("route", msg))
        app.registry.register(app.route_plan)
        app.bus.publish({"type": "log", "who": "system",
                          "text": "Trade-route planner ON (plot handoff via clipboard)."})
    except Exception as e:  # noqa: BLE001 — optional; never block startup
        app.route_plan = None
        app.bus.publish({"type": "log", "who": "system",
                          "text": f"Trade-route planner failed to start: {e}"})


def build_neutron_plan(app: "App") -> None:
    """(moved from App._start_neutron_plan)

    Build + register the neutron / long-range galaxy planner (#43), the second capability on
    the #41 route foundation. Fail soft — a startup problem just leaves it off. Shares the
    current-system seam for the default start; the plot handoff copies the first waypoint to the
    clipboard until the galaxy-map keybind automation (#32) lands."""
    try:
        from .search import RequestsHttp
        from .capabilities.route_plan_capability import (NeutronPlanCapability,
                                                         NeutronPlanConfig)

        ncfg = NeutronPlanConfig.from_cfg(app.cfg)
        app.neutron_plan = NeutronPlanCapability(
            ncfg, http=RequestsHttp(),
            get_current_system=app._current_system,
            log=lambda msg: app._log("neutron", msg))
        app.registry.register(app.neutron_plan)
        app.bus.publish({"type": "log", "who": "system",
                          "text": "Neutron-route planner ON (plot handoff via clipboard)."})
    except Exception as e:  # noqa: BLE001 — optional; never block startup
        app.neutron_plan = None
        app.bus.publish({"type": "log", "who": "system",
                          "text": f"Neutron-route planner failed to start: {e}"})


# ---- Road to Riches (#42, on the #41 foundation) ----------------------------
def build_riches_plan(app: "App") -> None:
    """(moved from App._start_riches_plan)

    Build + register the Road-to-Riches planner (#42) — nearby high-value UNSCANNED bodies
    to First-Discovery-scan for exploration credits — on the shared Spansh route client +
    galaxy-map plot handoff. Fail soft: a startup problem just leaves it off. Only needs the
    current SYSTEM (not a docked station); the plot handoff copies the first system to the
    clipboard until the galaxy-map keybind automation (#32) lands."""
    try:
        from .search import RequestsHttp
        from .capabilities.route_plan_capability import RichesPlanCapability, RichesPlanConfig

        rcfg = RichesPlanConfig.from_cfg(app.cfg)
        app.riches_plan = RichesPlanCapability(
            rcfg, http=RequestsHttp(),
            get_current_system=app._current_system,
            log=lambda msg: app._log("route", msg))
        app.registry.register(app.riches_plan)
        app.bus.publish({"type": "log", "who": "system",
                          "text": "Road-to-Riches planner ON (plot handoff via clipboard)."})
    except Exception as e:  # noqa: BLE001 — optional; never block startup
        app.riches_plan = None
        app.bus.publish({"type": "log", "who": "system",
                          "text": f"Road-to-Riches planner failed to start: {e}"})


# ---- Mining helper (#45, on the Spansh search layer) ------------------------
def build_mining_helper(app: "App") -> None:
    """(moved from App._start_mining_helper)

    Build + register the mining helper (#45) — nearest ring hotspot for a material + the best
    FRESHNESS-VERIFIED place to sell it + the mining loop dropped onto the checklist. Uses the
    synchronous Spansh /search layer (not the async route client), shares the current-system
    seam and the same checklist model the checklist capability serves, and hands the hotspot
    system to the galaxy map via the clipboard until the #32 keybind course-set lands. Fail
    soft — a startup problem just leaves it off."""
    try:
        from .search import RequestsHttp
        from .capabilities.mining_helper_capability import (MiningHelperCapability,
                                                            MiningHelperConfig)

        mcfg = MiningHelperConfig.from_cfg(app.cfg)
        app.mining_helper = MiningHelperCapability(
            mcfg, http=RequestsHttp(),
            get_current_system=app._current_system,
            checklist=app.checklist,
            log=lambda msg: app._log("mining", msg))
        app.registry.register(app.mining_helper)
        app.bus.publish({"type": "log", "who": "system",
                          "text": "Mining helper ON (hotspots + fresh sell price + checklist)."})
    except Exception as e:  # noqa: BLE001 — optional; never block startup
        app.mining_helper = None
        app.bus.publish({"type": "log", "who": "system",
                          "text": f"Mining helper failed to start: {e}"})


# ---- Auto persona->voice pairing (issue #96) --------------------------------
def build_voice_pairing(app: "App") -> None:
    """(moved from App._start_voice_pairing)

    Kick off the background pairing thread (never blocks startup). Gated by
    `app._voice_pairing_allowed`; skipped quietly otherwise. The pairing worker and the
    apply/remember/reconcile helpers live just below in this module; only the
    `_voice_pairing_allowed` gate stays on `App` (`_after_settings_change` calls
    `reconcile_persona_voice` on every live settings change)."""
    if not app._voice_pairing_allowed():
        return
    threading.Thread(target=lambda: pair_persona_voices(app), name="voice-pairing",
                     daemon=True).start()


def load_piper_voice(app: "App", model_path: str):
    """(moved from App._load_piper_voice)

    Load a Piper model as a cast voice (lazy, one per path). Returns an object with
    synth_pcm(text) -> (pcm, sr). Raises if Piper/the model isn't available (CastSynth
    catches it and degrades to silence)."""
    from .providers.piper_tts import PiperTTS
    cfg = dict(app.cfg)
    cfg["piper"] = {"model": model_path}
    return PiperTTS(cfg)


# The pairing WORKER + apply/remember/reconcile logic moved here with the rest of the wiring; only
# App._voice_pairing_allowed (the gate) stays on the app — it's read directly by a test and by
# build_voice_pairing. The worker runs in the background at startup; reconcile_persona_voice runs
# from App._after_settings_change on every live settings change (code moved; call sites and
# behaviour unchanged).
def pair_persona_voices(app: "App") -> None:
    """(moved from App._pair_persona_voices)

    Background worker: pair a default voice with each PRE-BUILT persona via one cheap-tier,
    one-time (cached) LLM call, then apply it to the current persona if it has no explicit
    voice. Fail-soft throughout — any failure just leaves the current default voice in place."""
    try:
        from . import elevenlabs as el
        from . import personality as persona_mod
        from . import voice_pairing as vp
        presets = [p for p in persona_mod.list_personas(app.cfg)
                   if p.get("source") == "preset"]
        if not presets:
            return
        voices = el.list_voices_detailed(app.cfg)
        if not voices:
            return
        cheap = Router.from_cfg(app.cfg).cheap_route(None).model
        gen = vp.make_pairing_generator(app.llm, model=cheap)
        result = vp.pair_voices(presets, voices, gen,
                                cache_path=vp.default_cache_path(app.cfg),
                                log=lambda m: app._log("voice", m))
        if result is None or not result.mapping:
            return
        app._voice_pairings = {k.strip().lower(): v for k, v in result.mapping.items()}
        app._voice_names = {v["voice_id"]: v.get("name", "") for v in voices}
        app._log("voice", f"persona voices paired ({'cache' if result.from_cache else 'fresh'}): "
                           f"{len(app._voice_pairings)}")
        # If a persona is already selected and has no explicit voice, dress it now.
        cur = str((app.cfg.get("personality", {}) or {}).get("persona") or "").strip()
        if cur:
            apply_persona_voice(app, cur)
    except Exception as e:  # noqa: BLE001 — pairing is best-effort; never crash/block the app
        app._log("voice", f"voice pairing skipped: {e}")


# ---- Auto crew->voice pairing (issue #124) ----------------------------------
def build_crew_voice_pairing(app: "App") -> None:
    """Kick the crew's OWN background pairing thread — a sibling of `build_voice_pairing`, started
    from the same startup hook and gated by the SAME `_voice_pairing_allowed` switch. Split out (
    rather than folded into `build_voice_pairing`) so a roster save can re-kick JUST the crew
    pairing (`kick_crew_voice_pairing`, called from `web.py::crew_save`) without also re-running
    the unrelated persona pairing."""
    if not app._voice_pairing_allowed():
        return
    threading.Thread(target=lambda: pair_crew_voices(app), name="crew-voice-pairing",
                     daemon=True).start()


def kick_crew_voice_pairing(app: "App") -> None:
    """Re-run the crew pairing in the BACKGROUND after the Commander saves the roster (issue #124):
    a save with no persona changes is a cache hit (the shared `pairing_key`), so this costs NO LLM
    call; only an actual persona edit/add/remove triggers one. Same gate + fail-soft as startup —
    never blocks the save response."""
    build_crew_voice_pairing(app)


def pair_crew_voices(app: "App") -> None:
    """Background worker (issue #124): pair a BEST-FIT voice with each crew member who has a
    written `persona` and is left on Auto (blank `voice_ref`), mirroring `pair_persona_voices` but
    keyed to the roster (`covas/crew.py`) instead of the shipped personas, and cached SEPARATELY
    (`crew.voice_pairings_file`) so a roster edit never busts the persona-pairing cache. Members
    with an explicit `voice_ref` are excluded from the input entirely (nothing to pair — their
    pinned voice already wins). Result lands on `app._crew_voice_pairings` (mirroring
    `_voice_pairings`) and is pushed straight to the audio layer so `speak_crew`'s NEXT line already
    honors it. Fail-soft throughout: any failure (LLM off, empty catalog, bad JSON, no persona'd
    members) just leaves crew on the deterministic per-name fallback — Auto never gets worse than
    before this issue."""
    try:
        from . import crew as crew_mod
        from . import elevenlabs as el
        from . import voice_pairing as vp
        # UNION across the default + every per-ship roster (issue #127 §6), deduped by name, so ONE
        # pairing cache serves every roster and a ship swap never triggers a re-pair.
        members = [m for m in crew_mod.all_members(app.cfg) if m.persona and not m.voice_ref]
        if not members:
            app._crew_voice_pairings = {}
            if app.audio is not None:
                app.audio.set_crew_pairings({})
            return
        voices = el.list_voices_detailed(app.cfg)
        if not voices:
            return
        cheap = Router.from_cfg(app.cfg).cheap_route(None).model
        gen = vp.make_pairing_generator(app.llm, model=cheap)
        personas = [{"name": m.name, "body": m.persona} for m in members]
        result = vp.pair_voices(personas, voices, gen,
                                cache_path=crew_mod.voice_pairings_file(app.cfg),
                                log=lambda m: app._log("voice", m))
        if result is None or not result.mapping:
            return
        app._crew_voice_pairings = {k.strip().lower(): v for k, v in result.mapping.items()}
        app._voice_names.update({v["voice_id"]: v.get("name", "") for v in voices})
        app._log("voice", f"crew voices paired ({'cache' if result.from_cache else 'fresh'}): "
                           f"{len(app._crew_voice_pairings)}")
        if app.audio is not None:
            app.audio.set_crew_pairings(dict(app._crew_voice_pairings))
    except Exception as e:  # noqa: BLE001 — pairing is best-effort; never crash/block the app
        app._log("voice", f"crew voice pairing skipped: {e}")


def persona_explicit_voices(app: "App") -> dict:
    """(moved from App._persona_explicit_voices)

    The per-persona EXPLICIT voice choices ([personality].persona_voices) the user has made —
    these ALWAYS win over an auto pairing and are never overwritten."""
    return (app.cfg.get("personality", {}) or {}).get("persona_voices", {}) or {}


def remember_persona_voice(app: "App", persona: str, voice_id: str, voice_name) -> None:  # noqa: ANN001
    """(moved from App._remember_persona_voice)

    Record that the user EXPLICITLY chose `voice_id` for `persona` (a manual voice change while
    that persona is active), persisted to overrides so it survives a restart and always wins."""
    persona = str(persona or "").strip()
    if not persona or not voice_id:
        return
    patch = {"personality": {"persona_voices": {persona: str(voice_id)}}}
    # save_overrides is namespaced on the app module (App imports it from .config); reach it there
    # so the persist stays on the same seam App.update_settings uses (and its test double applies).
    from . import app as _app_mod
    deep_merge(app.cfg, patch)
    deep_merge(app.overrides, patch)
    _app_mod.save_overrides(app.overrides)
    app._log("voice", f"remembered explicit voice for persona {persona!r}")


def apply_persona_voice(app: "App", persona: str) -> None:
    """(moved from App._apply_persona_voice)

    Apply the paired default voice for `persona` — UNLESS the user has set an explicit voice
    for it (which always wins). No-op when TTS isn't ElevenLabs, nothing is paired, or the voice
    already matches. Routed through update_settings (persist + live TTS reload); a re-entry guard
    stops the resulting voice change from being mis-recorded as an explicit user choice."""
    if app._applying_persona_voice:
        return
    if (app.cfg.get("tts", {}) or {}).get("provider") != "elevenlabs":
        return
    from . import voice_pairing as vp
    target = vp.voice_for_persona(persona_explicit_voices(app), app._voice_pairings, persona)
    if not target:
        return
    el = app.cfg.get("elevenlabs", {}) or {}
    if str(el.get("voice_id") or "") == target:
        return  # already on the right voice
    patch = {"elevenlabs": {"voice_id": target}}
    name = app._voice_names.get(target)
    if name:
        patch["elevenlabs"]["voice_name"] = name
    app._applying_persona_voice = True
    try:
        app.update_settings(patch)
        app._log("voice", f"persona {persona!r} -> paired voice {name or target}")
    finally:
        app._applying_persona_voice = False


def reconcile_persona_voice(app: "App", before: dict) -> None:
    """(moved from App._reconcile_persona_voice)

    On a settings change (issue #96): if the user changed the VOICE while staying on a persona,
    remember it as that persona's explicit choice; if the PERSONA changed, dress it in its paired
    (or explicit) voice. Skipped during our own apply (the re-entry guard). Fail-soft."""
    if app._applying_persona_voice:
        return
    try:
        pers = app.cfg.get("personality", {}) or {}
        if not pers.get("enabled"):
            return
        now_name = str(pers.get("persona") or "").strip()
        before_name = str((before.get("personality") or {}).get("persona") or "").strip()
        now_voice = str((app.cfg.get("elevenlabs", {}) or {}).get("voice_id") or "")
        before_voice = str((before.get("elevenlabs") or {}).get("voice_id") or "")
        persona_changed = now_name.lower() != before_name.lower()
        voice_changed = now_voice != before_voice
        if voice_changed and not persona_changed and now_name \
                and (app.cfg.get("tts", {}) or {}).get("provider") == "elevenlabs":
            remember_persona_voice(
                app, now_name, now_voice, (app.cfg.get("elevenlabs", {}) or {}).get("voice_name"))
        elif persona_changed and now_name:
            apply_persona_voice(app, now_name)
    except Exception as e:  # noqa: BLE001 — a reconcile glitch must never crash the loop
        app._log("voice", f"persona-voice reconcile failed: {e}")


# ---- Manifest + entrypoint --------------------------------------------------
@dataclass(frozen=True)
class Wiring:
    """One capability's wiring. `attr` = the single instance attr the builder binds; wire()
    pre-declares it None so it exists even when the gate is off. Use None when the row binds
    several attrs (their defaults stay in App.__init__) — or when the attr must stay ABSENT
    while gated off to match the pre-split surface (the Spansh search/planner rows: app.py
    never pre-declared those, and nothing reads them ungated). `gate` = config predicate
    (None = always on). `build` constructs + registers; gated builders keep their own
    fail-soft guard, always-on ones raise into startup exactly as their inline predecessors
    did."""
    attr: str | None
    gate: Callable[["App"], bool] | None
    build: Callable[["App"], None]


# Construction order is list order. It preserves the two real constraints: ED monitoring runs
# BEFORE its journal consumers (carriers/CG are built inside build_ed_monitoring; the None-defaults
# are set up-front so nothing is clobbered), and the audio layer is the LAST registration (it
# needs the mixer, providers and ED context; voice-pairing after it registers nothing — it only
# kicks off a background worker, matching the old __init__ tail). Adding a capability = one
# builder above + one entry here.
MANIFEST: tuple[Wiring, ...] = (
    Wiring(None,               None,                                                     build_help),
    Wiring(None,               lambda a: a.cfg.get("checklist", {}).get("file"),         build_checklist),
    Wiring("settings_cap",     None,                                                     build_settings),
    Wiring("clipboard_cap",    None,                                                     build_clipboard),
    Wiring("version_cap",      None,                                                     build_version),
    Wiring("ship_spec",        None,                                                     build_ship_spec),
    Wiring("game_data_status", None,                                                     build_game_data_status),
    Wiring(None,               lambda a: a.cfg.get("elite", {}).get("enabled"),          build_ed_monitoring),
    Wiring("proactive",        lambda a: a.cfg.get("proactive", {}).get("enabled"),      build_proactive),
    Wiring("route",            lambda a: a.cfg.get("route", {}).get("enabled"),          build_route),
    Wiring("keybinds",         lambda a: a.cfg.get("keybinds", {}).get("enabled"),       build_keybinds),
    Wiring("honk",             lambda a: a.cfg.get("honk", {}).get("enabled"),           build_honk),
    Wiring("reflex",           lambda a: a.cfg.get("reflex", {}).get("enabled"),         build_reflex),
    Wiring("comms",            lambda a: a.cfg.get("comms_send", {}).get("enabled"),     build_comms),
    Wiring("macros",           lambda a: a.cfg.get("macros", {}).get("enabled") and experimental(a.cfg, "macro"), build_macros),  # experimental (#123)
    Wiring("nav",              lambda a: a.cfg.get("nav", {}).get("enabled"),            build_nav),
    Wiring("ship_nav",         lambda a: a.cfg.get("nav", {}).get("enabled"),            build_ship_nav),
    Wiring(None,               lambda a: a.cfg.get("star_systems", {}).get("enabled"),   build_system_search),
    Wiring(None,               lambda a: a.cfg.get("search", {}).get("enabled"),         build_searches),
    Wiring(None,               lambda a: a.cfg.get("bodies", {}).get("enabled"),         build_bodies),
    Wiring(None,               lambda a: a.cfg.get("route_plan", {}).get("enabled") and experimental(a.cfg, "trade_route"), build_route_plan),  # experimental (#123)
    Wiring(None,               lambda a: a.cfg.get("neutron_plan", {}).get("enabled"),   build_neutron_plan),
    Wiring(None,               lambda a: a.cfg.get("riches_plan", {}).get("enabled"),    build_riches_plan),
    Wiring(None,               lambda a: a.cfg.get("mining_helper", {}).get("enabled"),  build_mining_helper),
    Wiring("memory",           lambda a: a.cfg.get("memory", {}).get("enabled"),         build_memory),
    Wiring("hud",              None,                                                     build_hud),          # always wired
    Wiring("audio",            lambda a: a.mixer is not None,                            build_audio_layer),  # last registration
    Wiring(None,               None,                                                     build_voice_pairing),
    Wiring(None,               None,                                                     build_crew_voice_pairing),  # #124, needs audio
)


def wire(app: "App") -> None:
    """Wire every capability onto `app`. Two-phase so ordering can't clobber a shared attr:
    first derive the None-defaults for rows that declare an attr (replacing the old scattered
    `self.X = None` pre-declarations; attr-less rows deliberately keep theirs absent when
    gated off), then build in list order. Gated builders fail soft and just stay off;
    always-on ones raise into startup, as before the split."""
    for w in MANIFEST:
        if w.attr:
            setattr(app, w.attr, None)
    for w in MANIFEST:
        if w.gate is None or w.gate(app):
            w.build(app)
