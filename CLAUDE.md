# CLAUDE.md — context for Claude Code

Read this first. It's the working agreement for this repo.

## What this is
COVAS++ — a local Windows **voice AI companion for Elite Dangerous**. Push-to-talk →
local STT (faster-whisper) → LLM → TTS. It converses, tracks a markdown checklist, and
can web-search. It does **not** fly the ship. The app is well past MVP — 110+ issues shipped:
a multi-provider LLM/TTS seam (Anthropic/OpenAI/Gemini · ElevenLabs/Edge/Azure/OpenAI/Cartesia/
Piper), 40+ self-registering capabilities, an ambient-audio layer, ED journal monitoring,
route/activity planners, guarded keybind automation, and a packaged Windows installer. Treat it
as a mature, actively-extended codebase.
Full design and rationale: **`DESIGN_AND_ROADMAP.md`**. `CLAUDE_CODE_PROMPTS.md` holds the
original sequenced build prompts (Prompts 1–7, Search 1–6, N1–N11, C1–C11, I1–I9) — all built
and merged. The live worklist is the **GitHub issue tracker**; start a fresh session from an
open issue there, not from the prompt pack.

## Run / verify
```powershell
.venv\Scripts\python.exe check_setup.py     # environment health
.venv\Scripts\python.exe run_covas.py        # headless voice loop
.venv\Scripts\python.exe run_covas_ui.py     # + localhost control panel
python poc_local_loop.py                      # offline POC (Ollama + Piper + Whisper)
python -m compileall covas                    # fast sanity check after edits (recursive)
pytest                                        # UNIT tests only — offline, free, run often
pytest -m "integration and local"            # free integration (Ollama/Piper/Whisper/audio)
pytest -m "integration and paid"             # deliberate, COSTS money (Anthropic/ElevenLabs)
```
Ship-critical paths (audio devices, Ollama server, ElevenLabs) need Doug's machine —
you generally **cannot** run the full loop in CI/sandbox. Byte-compile, add unit tests
for pure logic (parsing, routing, checklist ops), and state clearly what needs manual
on-hardware testing.

## Architecture (where things live)
- `covas/app.py` — orchestration: PTT handling, threading, cancellation, worker loop.
- `covas/providers/` — the swappable seam. `base.py` = Protocols (LLM/TTS/STT);
  `factory.py` builds the one named in config. The in-game LLM path is **provider-agnostic**
  (issue #11): any CLOUD LLM is fine there — Anthropic (`anthropic_llm`) today, OpenAI/Gemini
  next — and the router picks a canonical tier (cheap/standard/premium) that each provider's
  `[<provider>].tiers` map turns into a model id. Only LOCAL models (`ollama_llm`) stay OFF the
  in-game path — a useful local model competes with ED for the GPU (not an API limitation), so
  it's for offline/out-of-game use. TTS = `edge_tts` (default) / `azure_tts` / `openai_tts` /
  `cartesia_tts` (persona) / `elevenlabs_tts` / local `piper_tts`; `whisper_stt` wraps STT.
- `covas/llm.py` — Anthropic streaming (prompt caching + tools live here).
- `covas/checklist.py` — the checklist model; tools exposed to the LLM.
- `covas/events.py` — `EventBus` (thread-safe pub/sub). This is the spine; new inputs
  (ED journal, timers) publish here, UI/capabilities subscribe.
- `covas/config.py` — `config.toml` + `overrides.json`, relative paths resolved to abs.
- `covas/web.py` + `templates/` — Flask control panel.
- `covas/capabilities/` — the primary extension surface (43+ self-registering modules);
  see "Capabilities over loop edits" below.
- `covas/mixer/` — audio mixing/ducking for voice, ambient SFX/music, and chatter.
- `covas/ed/` — Elite Dangerous journal/status file monitoring and game-state context.
- `covas/search/` — web-search tool integration and result shaping.
- `covas/keybinds/` + `covas/macros/` — guarded ship/SRV/on-foot keybind automation and
  voice-authored named macros.
- `covas/comms/` + `covas/nav/` — in-game chat (local/wing) and route/activity planning.
- `covas/memory/` + `covas/cg/` — durable-fact recall and Community Goal tracking.
- `poc_local_loop.py` — standalone offline proof of concept.

## Conventions
- **Python 3.11+, standard library first.** Current deps in `requirements.txt`; add a
  new one only when it clearly earns its place, and note it in the PR.
- **Provider interfaces stay tiny** (1–2 methods). Normalize every LLM provider to the
  shared event contract in `providers/base.py` so `app.py` consumes them identically.
- **Capabilities over loop edits.** New features (ED context, keybinds) should be
  self-contained modules that register tools/handlers, not new branches inside `app.py`.
- **Tests: unit by default, integration opt-in.** Bare `pytest` must stay offline and
  free — no network, API, ElevenLabs, Ollama, or audio. Achieve that by injecting
  dependencies (components take provider instances; the factory builds real ones only at
  the app entry, tests pass fakes from `tests/fakes.py`). Anything hitting a real service
  is `@pytest.mark.integration` plus `local` (free) or `paid` (costs money), and is
  excluded from the default run. See `DESIGN_AND_ROADMAP.md` §9.
- **Fail soft.** The voice loop must survive any provider/tool error and return to Idle;
  a dead TTS degrades to text, it doesn't crash the session. Keep the broad `except`
  guards that exist for this reason.
- **Docs + tests + help stay in sync (definition of done).** A feature isn't done until it's
  reflected in all of: the documentation site (`docs/`), a manual check in `MANUAL_TESTS.md`,
  the capability's in-app help metadata, and `DESIGN_AND_ROADMAP.md` if the architecture
  changed. Update them in the same change — don't let the four drift apart.
- **Style:** type hints, module docstrings, comments explain *why* not *what*, match the
  existing terse-but-commented voice. Keep diffs small and reviewable.

## Guardrails (this is a PUBLIC repo)
- **Never commit secrets or personal data.** All provider key files (`*APIKey.txt` —
  Anthropic/Azure/OpenAI/Cartesia/Gemini/Inara/ElevenLabs — and `*.key`) plus per-user data
  (`personality.txt`, `overrides.json`, `logs/`, `ultimate_checklist.md`, `sounds/`,
  `voicelines/`, `*.onnx`, `campaign.txt`, `crew.json`, `/memory/`, `custom_macros.jsonl`,
  `music/`, `/audio/`, `/content/`, `personalities/custom/`, `personalities/voice_pairings.json`)
  are git-ignored — keep it that way; check `.gitignore` for the current full list rather than
  assuming this one. Don't hardcode API keys, absolute `C:\Users\...` paths, the local username,
  or the Commander's identity anywhere tracked.
- **Config paths are relative** to the project root and resolved in `config.py`. New path
  settings follow the same pattern; add them to `_PATH_FIELDS`.
- Don't add ship-control/keybind automation without the safety layer described in the
  design doc (allowlist, confirmation, hard abort, no-op during combat).

## Cost awareness
Cost is handled by **cloud tiering**, not local LLMs (a useful local model fights ED for
the GPU). Prompt caching on system+tools is on in `llm.py`; defaults are Sonnet, thinking
off, `max_tokens=1024`, `web_search.max_uses=3`. The router (see the prompt pack) makes
Haiku the default tier and escalates to Sonnet/Opus only when a turn earns it. When
touching the LLM path: preserve caching, don't re-send large static context, keep replies
short (spoken aloud), and never re-introduce an always-on thinking default. Local Piper TTS
is the one cost cut that runs fine next to the game.

## Workflow
One capability/change per branch; small commits; each roadmap step is independently
shippable. Update `DESIGN_AND_ROADMAP.md` if a decision changes the architecture.
