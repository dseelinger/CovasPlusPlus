# COVAS++

A local desktop **voice AI companion** for Elite Dangerous. Talk to it by voice while
you fly; it converses, answers questions, tracks a checklist, and can look things up on
the web — with a personality and TNG-style computer sound cues. It does **not** control
your ship (EDCopilot handles that).

> Unofficial, fan-made. Elite Dangerous is a trademark of Frontier Developments plc.
> Not affiliated with or endorsed by Frontier.

## How it works (the voice loop)
1. **Hold** the push-to-talk key and speak.
2. A *listening* chirp plays; your mic is captured while held.
3. **Release** → a *processing* chirp; speech is transcribed locally (faster-whisper).
4. The transcript goes to the LLM (streaming) with your personality + rolling history.
5. A *done* chirp plays; the reply is spoken aloud (ElevenLabs, or local Piper).
6. **Cancel** anything in progress with a brief tap of the PTT key.

## Cloud or local
The three swappable pieces live behind a provider seam (`covas/providers/`):

| Piece | Cloud | Local (offline, free) |
|-------|-------|-----------------------|
| LLM   | Anthropic Claude | Ollama (e.g. Qwen) |
| TTS   | ElevenLabs | Piper |
| STT   | faster-whisper (already local) | — |

Select providers in `config.toml` under `[llm]` and `[tts]`.

## Quick start (from a fresh clone)
This is a public repo; **secrets and personal data are git-ignored**, so set them up locally:

```bash
# 1. Python env
python -m venv .venv && .venv\Scripts\pip install -r requirements.txt

# 2. Secrets & personal files (copies of the .example templates)
copy ElevenLabsAPIKey.txt.example ElevenLabsAPIKey.txt   # paste your key (cloud TTS only)
copy personality.example.txt personality.txt             # make the character yours
#   ANTHROPIC_API_KEY: set as a Windows *User* environment variable (cloud LLM only)

# 3. (Optional) sound cues: drop your own .wav files in sounds/ — see config.toml

# 4. Verify
check_setup.bat        # or: .venv\Scripts\python.exe check_setup.py
```

## Local offline mode (proof of concept)
Runs the whole loop with **no cloud and no cost** — Whisper + Qwen (Ollama) + Piper.

```bash
# prereqs on this machine
ollama serve && ollama pull qwen3
python -m piper.download_voices en_US-lessac-medium   # then set [piper].model in config.toml

# try it
python poc_local_loop.py                 # text REPL: type -> Qwen -> Piper speaks
python poc_local_loop.py --say "Systems nominal, Commander."   # TTS smoke test
python poc_local_loop.py --from-wav clip.wav                    # STT smoke test
python poc_local_loop.py --mic           # push-to-talk full local loop
```

To make the **main** app local, set `[llm].provider = "ollama"` and `[tts].provider = "piper"`.

## Files
| File | What it is |
|------|-----------|
| `config.toml` | All defaults, commented. Portable (relative paths). |
| `overrides.json` | *(git-ignored)* live UI changes, layered over config.toml. |
| `personality.txt` | *(git-ignored)* your system prompt / character. |
| `ElevenLabsAPIKey.txt` | *(git-ignored)* your ElevenLabs key. |
| `covas/providers/` | Cloud + local LLM/TTS/STT behind a common interface. |
| `poc_local_loop.py` | Standalone offline (local) proof of concept. |
| `DESIGN_AND_ROADMAP.md` | Architecture, cost strategy, and phased plan. |
| `CLAUDE.md` | Repo context/conventions for Claude Code. |

## Keys
- **ANTHROPIC_API_KEY** — a Windows *User* environment variable. Never stored in a file.
- **ElevenLabs key** — read from `ElevenLabsAPIKey.txt` (git-ignored).

## License
MIT (see `LICENSE`) — covers the source only. Supply your own rights for any sound
cues or voice models you add locally.
