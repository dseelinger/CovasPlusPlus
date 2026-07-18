"""Pluggable providers for the three swappable pieces of the voice loop:
LLM (Anthropic / OpenAI-compatible / Gemini), TTS (ElevenLabs / Edge / Azure /
OpenAI / Cartesia / Piper), and STT (Whisper — CPU only).

The rest of the app talks to the Protocols in `base.py`; `factory.py` builds
the concrete implementation named in config. This is the seam that lets the
cloud LLM providers be swapped and routed between; local Piper TTS + CPU Whisper
STT run fine alongside the game (no GPU contention with Elite Dangerous).
"""
