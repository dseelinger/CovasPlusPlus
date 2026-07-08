"""Pluggable providers for the three swappable pieces of the voice loop:
LLM (Anthropic / Ollama), TTS (ElevenLabs / Piper), and STT (Whisper).

The rest of the app talks to the Protocols in `base.py`; `factory.py` builds
the concrete implementation named in config. This is the seam that lets the
local (offline) stack and the cloud stack coexist and be routed between.
"""
