"""COVAS++ — fully-local proof of concept (Whisper + Qwen/Ollama + Piper).

Proves the offline stack end to end, independent of the main app and the cloud
services. Uses the same provider seam the real app will (covas/providers), so a
successful POC folds straight in.

Prereqs on THIS machine:
  1. Ollama running with your model:   ollama serve   +   ollama pull qwen3
  2. A Piper voice downloaded:         python -m piper.download_voices en_US-lessac-medium
     then set [piper].model in config.toml to the resulting .onnx path.
  3. faster-whisper installed (already in requirements.txt) for --from-wav / --mic.

Usage:
  python poc_local_loop.py                 # text REPL: type -> Qwen -> Piper speaks
  python poc_local_loop.py --no-tts        # text REPL, printed replies only
  python poc_local_loop.py --say "Hello"   # TTS smoke test (Piper only)
  python poc_local_loop.py --from-wav a.wav# STT smoke test: transcribe then reply
  python poc_local_loop.py --mic           # push-to-talk full local loop (needs mic)

Everything here runs offline and costs nothing to iterate on.
"""
from __future__ import annotations

import argparse
import sys
import threading

from covas.config import load_config, deep_merge
from covas.providers import factory


def _load_local_cfg() -> dict:
    """Config with the local providers forced on, whatever the file defaults are."""
    cfg = load_config()
    deep_merge(cfg, {"llm": {"provider": "ollama"}, "tts": {"provider": "piper"}})
    return cfg


def _stream_and_speak(llm, tts, history: list[dict], text: str, speak: bool) -> None:
    history.append({"role": "user", "content": text})
    cancel = threading.Event()

    def on_event(kind: str, data: str) -> None:
        if kind == "thinking":
            pass  # reasoning is hidden from speech; uncomment to debug:
            # print(f"\n  [thinking] {data}", end="", flush=True)

    reply = ""
    print("COVAS: ", end="", flush=True)
    for kind, chunk in llm.stream_reply(history, cancel, on_event):
        if kind == "text":
            reply += chunk
            sys.stdout.write(chunk)
            sys.stdout.flush()
    print()
    history.append({"role": "assistant", "content": reply})
    if speak and reply.strip():
        tts.speak(reply, cancel)


def main() -> int:
    ap = argparse.ArgumentParser(description="COVAS++ local (offline) POC")
    ap.add_argument("--say", metavar="TEXT", help="Speak TEXT via Piper and exit (TTS test)")
    ap.add_argument("--from-wav", metavar="FILE", help="Transcribe FILE via Whisper, then reply")
    ap.add_argument("--no-tts", action="store_true", help="Print replies, don't speak them")
    ap.add_argument("--mic", action="store_true", help="Push-to-talk full local loop")
    args = ap.parse_args()

    cfg = _load_local_cfg()
    speak = not args.no_tts

    # --- TTS-only smoke test -------------------------------------------------
    if args.say is not None:
        tts = factory.make_tts(cfg)
        print(f"[piper] speaking: {args.say!r}")
        tts.speak(args.say, threading.Event())
        return 0

    # --- Build the local stack ----------------------------------------------
    llm = factory.make_llm(cfg)
    ok, msg = llm.ping()  # OllamaLLM-specific friendly connectivity check
    print(f"[ollama] {msg}")
    if not ok:
        return 2
    tts = factory.make_tts(cfg) if speak else None

    history: list[dict] = []

    # --- STT smoke test ------------------------------------------------------
    if args.from_wav:
        import soundfile as sf
        stt = factory.make_stt(cfg)
        audio, _sr = sf.read(args.from_wav, dtype="float32", always_2d=False)
        text = stt.transcribe(audio)
        print(f"[whisper] heard: {text!r}")
        if text:
            _stream_and_speak(llm, tts, history, text, speak)
        return 0

    # --- Mic push-to-talk loop (Windows/hardware) ----------------------------
    if args.mic:
        return _mic_loop(cfg, llm, tts, history, speak)

    # --- Default: text REPL --------------------------------------------------
    print("Local COVAS++ POC. Type a message (Ctrl+C or 'quit' to exit).")
    try:
        while True:
            text = input("\nCommander: ").strip()
            if text.lower() in {"quit", "exit"}:
                break
            if text:
                _stream_and_speak(llm, tts, history, text, speak)
    except (KeyboardInterrupt, EOFError):
        print()
    return 0


def _mic_loop(cfg, llm, tts, history, speak) -> int:
    """Minimal push-to-talk: hold the configured key, speak, release to process."""
    import time
    import keyboard
    from covas.audio import Recorder
    from covas.providers.whisper_stt import WhisperSTT

    rec = Recorder(cfg)
    stt = WhisperSTT(cfg)
    key = str(cfg["keys"]["push_to_talk"])
    print(f"Hold [{key}] to talk; release to send. Ctrl+C to quit.")
    held = False
    try:
        while True:
            if keyboard.is_pressed(key):
                if not held:
                    held = True
                    rec.start()
            elif held:
                held = False
                audio = rec.stop()
                text = stt.transcribe(audio)
                if text:
                    print(f"\nCommander: {text}")
                    _stream_and_speak(llm, tts, history, text, speak)
            time.sleep(0.02)
    except KeyboardInterrupt:
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
