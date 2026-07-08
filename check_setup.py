"""
COVAS++  —  Phase 1 setup checker.

Run this to confirm the whole foundation is solid BEFORE we build the voice loop:
  - config file loads
  - ANTHROPIC_API_KEY env var is present
  - ElevenLabs key file + personality.txt exist and are readable
  - both API keys actually work (Anthropic model list + ElevenLab voice lookup)
  - all Python packages import
  - your microphone and speakers are visible to the audio engine

It changes nothing and sends no billable requests (both API calls are free lookups).
"""
from __future__ import annotations
import os
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OK = "  [ OK ] "
BAD = "  [FAIL] "
WARN = "  [warn] "
problems: list[str] = []


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def load_config() -> dict:
    section("Config")
    cfg_path = ROOT / "config.toml"
    if not cfg_path.exists():
        print(BAD + f"config.toml not found at {cfg_path}")
        problems.append("config.toml missing")
        sys.exit(1)
    with open(cfg_path, "rb") as f:
        cfg = tomllib.load(f)
    print(OK + f"loaded {cfg_path}")
    return cfg


def check_imports() -> None:
    section("Python packages")
    for mod, label in [
        ("anthropic", "anthropic"),
        ("faster_whisper", "faster-whisper"),
        ("sounddevice", "sounddevice"),
        ("soundfile", "soundfile"),
        ("numpy", "numpy"),
        ("keyboard", "keyboard"),
        ("requests", "requests"),
        ("flask", "flask"),
    ]:
        try:
            __import__(mod)
            print(OK + label)
        except Exception as e:  # noqa: BLE001
            print(BAD + f"{label}: {e}")
            problems.append(f"import {label}")


def check_files(cfg: dict) -> str | None:
    section("Keys & files")
    # Anthropic env var
    ak = os.environ.get("ANTHROPIC_API_KEY", "")
    if ak.startswith("sk-ant-"):
        print(OK + f"ANTHROPIC_API_KEY present (len={len(ak)})")
    else:
        print(BAD + "ANTHROPIC_API_KEY not visible to this process")
        print("        (If you JUST set it, close this terminal and open a new one.)")
        problems.append("ANTHROPIC_API_KEY")

    # ElevenLabs key file
    el_path = Path(cfg["elevenlabs"]["api_key_file"])
    el_key = None
    if el_path.exists():
        el_key = el_path.read_text(encoding="utf-8").strip()
        print(OK + f"ElevenLabs key file ({len(el_key)} chars)")
    else:
        print(BAD + f"ElevenLabs key file missing: {el_path}")
        problems.append("ElevenLabs key file")

    # personality.txt
    p_path = Path(cfg["personality"]["file"])
    if p_path.exists():
        n = len(p_path.read_text(encoding="utf-8").splitlines())
        print(OK + f"personality.txt ({n} lines)")
    else:
        print(BAD + f"personality.txt missing: {p_path}")
        problems.append("personality.txt")
    return el_key


def check_anthropic() -> None:
    section("Anthropic API (free model-list call)")
    try:
        import anthropic
        client = anthropic.Anthropic()
        models = client.models.list(limit=20)
        ids = [m.id for m in models.data]
        print(OK + f"reachable — {len(ids)} models")
    except Exception as e:  # noqa: BLE001
        print(BAD + f"{e}")
        problems.append("Anthropic API call")


def check_elevenlabs(el_key: str | None) -> None:
    section("ElevenLabs API (free voice lookup)")
    if not el_key:
        print(WARN + "skipped (no key)")
        return
    try:
        import requests
        r = requests.get(
            "https://api.elevenlabs.io/v1/voices",
            headers={"xi-api-key": el_key},
            timeout=15,
        )
        r.raise_for_status()
        voices = r.json().get("voices", [])
        print(OK + f"reachable — {len(voices)} voices in your account")
    except Exception as e:  # noqa: BLE001
        print(BAD + f"{e}")
        problems.append("ElevenLabs API call")


def check_audio(cfg: dict) -> None:
    section("Audio devices")
    try:
        import sounddevice as sd
        inputs = [d["name"] for d in sd.query_devices() if d["max_input_channels"] > 0]
        outputs = [d["name"] for d in sd.query_devices() if d["max_output_channels"] > 0]
        di = sd.query_devices(kind="input")
        do = sd.query_devices(kind="output")
        print(OK + f"{len(inputs)} input device(s). Default mic: {di['name']}")
        print(OK + f"{len(outputs)} output device(s). Default out: {do['name']}")
        print("        (If the default mic is wrong, set audio.input_device in config.toml.)")
    except Exception as e:  # noqa: BLE001
        print(BAD + f"{e}")
        problems.append("audio devices")


def main() -> None:
    print("COVAS++ setup check")
    cfg = load_config()
    check_imports()
    el_key = check_files(cfg)
    check_anthropic()
    check_elevenlabs(el_key)
    check_audio(cfg)

    section("Result")
    if problems:
        print(BAD + f"{len(problems)} issue(s): " + ", ".join(problems))
        print("\nFix the FAIL lines above, then run this again.")
        sys.exit(1)
    print(OK + "All systems go, Commander. Ready for Phase 2.  o7")


if __name__ == "__main__":
    main()
