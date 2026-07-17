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


def check_gemini(cfg: dict) -> None:
    """Fail-soft Gemini model-id guard (issue #91).

    Only runs when [llm].provider == "gemini". Fetches the live model list and WARNS (never a
    FAIL, never a crash) if the configured [gemini].model or any [gemini.tiers] id isn't in it —
    catching a stale/guessed id before the user hits a first-word 404 instead of after."""
    if (cfg.get("llm", {}) or {}).get("provider") != "gemini":
        return
    section("Gemini API (free model-list call)")
    try:
        from covas.firstrun import gemini_key
        from covas.providers.gemini_llm import list_gemini_models, _DEFAULT_BASE_URL
        key = gemini_key(cfg)
        if not key:
            print(WARN + "skipped (no Gemini key)")
            return
        g = cfg.get("gemini", {}) or {}
        base_url = str(g.get("base_url", "")).strip().rstrip("/") or _DEFAULT_BASE_URL
        live = list_gemini_models(base_url, key)
        print(OK + f"reachable — {len(live)} models")
        live_set = set(live)
        configured = {str(g.get("model", "")).strip()}
        configured |= {str(v).strip() for v in (g.get("tiers", {}) or {}).values()}
        # `-latest` ALIASES (issue #91) resolve server-side to a current GA model and do NOT appear
        # verbatim in the concrete-id list — so don't false-warn on them; only flag concrete ids that
        # are genuinely absent.
        missing = sorted(m for m in configured
                         if m and not m.endswith("-latest") and m not in live_set)
        if missing:
            print(WARN + "configured Gemini model id(s) not in the live list: "
                  + ", ".join(missing))
            print("        (Fix [gemini].model / [gemini.tiers] — a bad id 404s every turn. Tip: the "
                  "gemini-flash-lite-latest / gemini-flash-latest / gemini-pro-latest aliases are "
                  "deprecation-proof.)")
        else:
            print(OK + "configured [gemini].model / tiers are all valid (live ids or `-latest` aliases)")
    except Exception as e:  # noqa: BLE001 — this guard must never crash setup
        print(WARN + f"Gemini model-list check skipped: {e}")


def check_datasets() -> None:
    """Bundled-data freshness (issue #101). WARNS (never FAILS) when a generated dataset is older
    than ~6 months, so a release remembers to run `scripts/refresh_datasets.py` and keep up with
    FDev content. Purely informational — stale data still works, it just may miss newer hulls."""
    section("Game data freshness")
    try:
        from covas.nav.datasets import load_manifest, stale_datasets
        rows = load_manifest()
        if not rows:
            print(WARN + "no dataset manifest found (run scripts/refresh_datasets.py)")
            return
        max_age = 183  # ~6 months
        stale = stale_datasets(max_age)
        for d in rows:
            age = "unknown" if d.age_days is None else f"{d.age_days}d"
            mark = WARN if d in stale else OK
            print(mark + f"{d.label}: {d.row_count} rows, generated {d.generated_at} ({age} old)")
        if stale:
            print("        (Some datasets are >6 months old — run "
                  "`.venv\\Scripts\\python.exe scripts/refresh_datasets.py` to refresh from the "
                  "community sources, review the diff, and commit.)")
    except Exception as e:  # noqa: BLE001 — a freshness check must never fail setup
        print(WARN + f"dataset freshness check skipped: {e}")


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
    check_gemini(cfg)
    check_datasets()
    check_audio(cfg)

    section("Result")
    if problems:
        print(BAD + f"{len(problems)} issue(s): " + ", ".join(problems))
        print("\nFix the FAIL lines above, then run this again.")
        sys.exit(1)
    print(OK + "All systems go, Commander. Ready for Phase 2.  o7")


if __name__ == "__main__":
    main()
