"""
COVAS++  —  Phase 1 setup checker.

Run this to confirm the whole foundation is solid BEFORE we build the voice loop:
  - config file loads
  - the Anthropic key file exists (DPAPI-encrypted; env vars are no longer read — issue #22)
  - ElevenLabs key file + personality.txt exist and are readable
  - both API keys actually work (Anthropic model list + ElevenLab voice lookup)
  - all Python packages import
  - your microphone and speakers are visible to the audio engine

It changes nothing and sends no billable requests (both API calls are free lookups).
"""
from __future__ import annotations
import sys
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
    # Use the app's own loader so key-file paths resolve under data_dir exactly as the app sees
    # them (and so the DPAPI-aware key helpers read the same files).
    from covas.config import load_config as _load
    cfg = _load()
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


def check_files(cfg: dict) -> tuple[str | None, str | None]:
    section("Keys & files")
    from covas import firstrun
    # Anthropic key file (DPAPI-encrypted, file-only — env vars are no longer read, issue #22).
    anth_key = firstrun.anthropic_key(cfg)
    if anth_key:
        print(OK + f"Anthropic key file present (len={len(anth_key)})")
    else:
        print(BAD + "Anthropic key file missing or unreadable")
        print("        (Run the setup wizard, or paste your key in the control-panel Settings.)")
        problems.append("Anthropic key file")

    # ElevenLabs key file (optional — no key => text-only mode).
    el_key = firstrun.elevenlabs_key(cfg)
    if el_key:
        print(OK + f"ElevenLabs key file ({len(el_key)} chars)")
    else:
        print(WARN + "ElevenLabs key file missing (optional - the app runs text-only without it)")

    # personality.txt
    p_path = Path(cfg["personality"]["file"])
    if p_path.exists():
        n = len(p_path.read_text(encoding="utf-8").splitlines())
        print(OK + f"personality.txt ({n} lines)")
    else:
        print(BAD + f"personality.txt missing: {p_path}")
        problems.append("personality.txt")
    return anth_key, el_key


def check_anthropic(anth_key: str | None) -> None:
    section("Anthropic API (free model-list call)")
    if not anth_key:
        print(WARN + "skipped (no key)")
        return
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=anth_key)
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
    anth_key, el_key = check_files(cfg)
    check_anthropic(anth_key)
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
