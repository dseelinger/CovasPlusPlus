# CLAUDE.md — context for Claude Code

Read this first. It's the working agreement for this repo.

## What this is
COVAS++ — a local Windows **voice AI companion for Elite Dangerous**. Push-to-talk →
local STT (faster-whisper) → LLM → TTS. It converses, tracks a markdown checklist, and
can web-search. It does **not** fly the ship. Treat the current app as a light MVP.
Full design and rationale: **`DESIGN_AND_ROADMAP.md`**. Sequenced build prompts:
**`CLAUDE_CODE_PROMPTS.md`**.

## Run / verify
```bash
.venv\Scripts\python.exe check_setup.py     # environment health
.venv\Scripts\python.exe run_covas.py        # headless voice loop
.venv\Scripts\python.exe run_covas_ui.py     # + localhost control panel
python poc_local_loop.py                      # offline POC (Ollama + Piper + Whisper)
python -m py_compile covas\**\*.py            # fast sanity check after edits
```
Ship-critical paths (audio devices, Ollama server, ElevenLabs) need Doug's machine —
you generally **cannot** run the full loop in CI/sandbox. Byte-compile, add unit tests
for pure logic (parsing, routing, checklist ops), and state clearly what needs manual
on-hardware testing.

## Architecture (where things live)
- `covas/app.py` — orchestration: PTT handling, threading, cancellation, worker loop.
- `covas/providers/` — the swappable seam. `base.py` = Protocols (LLM/TTS/STT);
  `factory.py` builds the one named in config; cloud = `anthropic_llm`/`elevenlabs_tts`,
  local = `ollama_llm`/`piper_tts`; `whisper_stt` wraps STT.
- `covas/llm.py` — Anthropic streaming (prompt caching + tools live here).
- `covas/checklist.py` — the checklist model; tools exposed to the LLM.
- `covas/events.py` — `EventBus` (thread-safe pub/sub). This is the spine; new inputs
  (ED journal, timers) publish here, UI/capabilities subscribe.
- `covas/config.py` — `config.toml` + `overrides.json`, relative paths resolved to abs.
- `covas/web.py` + `templates/` — Flask control panel.
- `poc_local_loop.py` — standalone offline proof of concept.

## Conventions
- **Python 3.11+, standard library first.** Current deps in `requirements.txt`; add a
  new one only when it clearly earns its place, and note it in the PR.
- **Provider interfaces stay tiny** (1–2 methods). Normalize every LLM provider to the
  shared event contract in `providers/base.py` so `app.py` consumes them identically.
- **Capabilities over loop edits.** New features (ED context, keybinds) should be
  self-contained modules that register tools/handlers, not new branches inside `app.py`.
- **Fail soft.** The voice loop must survive any provider/tool error and return to Idle;
  a dead TTS degrades to text, it doesn't crash the session. Keep the broad `except`
  guards that exist for this reason.
- **Style:** type hints, module docstrings, comments explain *why* not *what*, match the
  existing terse-but-commented voice. Keep diffs small and reviewable.

## Guardrails (this is a PUBLIC repo)
- **Never commit secrets or personal data.** `ElevenLabsAPIKey.txt`, `personality.txt`,
  `overrides.json`, `logs/`, `ultimate_checklist.md`, `sounds/`, `voicelines/`, and
  `*.onnx` are git-ignored — keep it that way. Don't hardcode API keys, absolute
  `C:\Users\...` paths, the local username, or the Commander's identity anywhere tracked.
- **Config paths are relative** to the project root and resolved in `config.py`. New path
  settings follow the same pattern; add them to `_PATH_FIELDS`.
- Don't add ship-control/keybind automation without the safety layer described in the
  design doc (allowlist, confirmation, hard abort, no-op during combat).

## Cost awareness (cloud path)
Prompt caching on system+tools is already on in `llm.py`; default model is Sonnet.
When touching the cloud path, preserve caching, avoid re-sending large static context,
and prefer routing routine turns to the local provider (see the router prompt).

## Workflow
One capability/change per branch; small commits; each roadmap step is independently
shippable. Update `DESIGN_AND_ROADMAP.md` if a decision changes the architecture.
