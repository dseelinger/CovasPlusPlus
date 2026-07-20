"""Structured setup/health checks - the shared core behind the `check_setup.py` CLI and the
control-panel's one-click "Test my setup" (issue #181).

`check_setup.py` grew as a print-and-exit script; a non-technical Commander can't be asked to run it
in a terminal and read tracebacks. This module runs the SAME checks but returns a structured,
JSON-able `HealthReport` (sections of pass/warn/fail checks with human-readable messages), so:

  * `check_setup.py` renders it to the console (unchanged behaviour + exit code), and
  * the web control panel calls `run_health(cfg)` and shows a readable report the Commander can
    screenshot for support - no terminal, no stack traces.

Design for offline tests (DESIGN §9): the pure checks (config, imports, key/file presence, datasets,
audio-visibility) need no network; the provider-reachability probes are INJECTED, so the default
`pytest` run exercises the report/verdict logic and the friendly-error mapping with fakes. Every
check is fail-soft - a probe that raises becomes a readable fail/warn line, never an exception out
of `run_health`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

# Check statuses. Only FAIL blocks "all systems go"; WARN is informational (optional key, stale data).
OK = "ok"
WARN = "warn"
FAIL = "fail"


@dataclass
class Check:
    """One line in the report: a status, a short human label, and optional detail/hint."""
    status: str
    label: str
    detail: str = ""


@dataclass
class Section:
    title: str
    checks: list[Check] = field(default_factory=list)

    def add(self, status: str, label: str, detail: str = "") -> Check:
        c = Check(status, label, detail)
        self.checks.append(c)
        return c


@dataclass
class HealthReport:
    sections: list[Section] = field(default_factory=list)

    def section(self, title: str) -> Section:
        s = Section(title)
        self.sections.append(s)
        return s

    @property
    def problems(self) -> list[str]:
        """Labels of every FAIL check - the blocking issues."""
        return [c.label for s in self.sections for c in s.checks if c.status == FAIL]

    @property
    def warnings(self) -> list[str]:
        return [c.label for s in self.sections for c in s.checks if c.status == WARN]

    @property
    def ok(self) -> bool:
        """True when nothing FAILED (warnings are fine - the app still runs)."""
        return not self.problems

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "problems": self.problems,
            "warnings": self.warnings,
            "sections": [
                {"title": s.title,
                 "checks": [{"status": c.status, "label": c.label, "detail": c.detail}
                            for c in s.checks]}
                for s in self.sections
            ],
        }


# ---- human-readable error mapping (issue #181: messages, not tracebacks) -------------------

def friendly_provider_error(provider: str, exc: Exception) -> str:
    """Turn a provider exception into a Commander-facing sentence - never a stack trace.

    Classifies the usual failures: a bad/missing key (auth), an unreachable service (network), and
    everything else as a generic 'couldn't reach' with the short reason. Kept pure + string-only so
    it's unit-testable without a real provider."""
    msg = str(exc)
    low = msg.lower()
    status = getattr(exc, "status", None)
    if status in (401, 403) or "401" in low or "403" in low or "unauthor" in low or "invalid" in low \
            and "key" in low:
        return (f"COVAS couldn't sign in to {provider} - the API key looks wrong or expired. "
                f"Re-enter it on the Settings page (API keys).")
    if any(t in low for t in ("timed out", "timeout", "connection", "getaddrinfo", "network",
                              "temporarily", "unreachable", "dns", "ssl")):
        return (f"COVAS couldn't reach {provider} - looks like a network/connection problem. "
                f"Check your internet and try again.")
    if status == 429 or "429" in low or "rate limit" in low:
        return f"{provider} is rate-limiting requests right now - wait a moment and try again."
    # Fall back to a short, non-scary reason (first line only, no traceback).
    reason = msg.splitlines()[0].strip() if msg else "unknown error"
    return f"COVAS couldn't reach {provider}: {reason}"


# ---- individual checks (each appends to a Section) -----------------------------------------

_IMPORTS = [
    ("anthropic", "anthropic"),
    ("faster_whisper", "faster-whisper"),
    ("sounddevice", "sounddevice"),
    ("soundfile", "soundfile"),
    ("numpy", "numpy"),
    ("keyboard", "keyboard"),
    ("requests", "requests"),
    ("flask", "flask"),
]


def check_imports(report: HealthReport) -> None:
    s = report.section("Python packages")
    for mod, label in _IMPORTS:
        try:
            __import__(mod)
            s.add(OK, label)
        except Exception as e:  # noqa: BLE001
            s.add(FAIL, f"{label} won't import", str(e).splitlines()[0] if str(e) else "")


def check_keys_and_files(report: HealthReport, cfg: dict) -> tuple[Optional[str], Optional[str]]:
    from . import firstrun
    s = report.section("Keys & files")

    anth_key = firstrun.anthropic_key(cfg)
    if anth_key:
        s.add(OK, "Anthropic key is set")
    else:
        s.add(FAIL, "No Anthropic key (required)",
              "Add your Anthropic key on the Settings page (API keys), or run the setup wizard.")

    el_key = firstrun.elevenlabs_key(cfg)
    if el_key:
        s.add(OK, "ElevenLabs key is set")
    else:
        s.add(WARN, "No ElevenLabs key (optional)",
              "The app still runs - it uses the free Edge voice, or falls back to on-screen text.")

    p_path = Path(str((cfg.get("personality", {}) or {}).get("file", "personality.txt")))
    if p_path.exists():
        s.add(OK, "personality.txt found")
    else:
        s.add(WARN, "personality.txt not found",
              "COVAS speaks with a neutral default until you add one (copy personality.example.txt).")
    return anth_key, el_key


def check_anthropic(report: HealthReport, anth_key: Optional[str],
                    probe: Optional[Callable[[str], int]] = None) -> None:
    """Free model-list reachability check. `probe(key)->count` is injected for tests; the default
    hits the live SDK. A missing key WARNs (the key check already FAILED it)."""
    s = report.section("Anthropic")
    if not anth_key:
        s.add(WARN, "Anthropic check skipped (no key)")
        return
    try:
        n = probe(anth_key) if probe is not None else _probe_anthropic(anth_key)
        s.add(OK, f"Anthropic reachable - {n} models available")
    except Exception as e:  # noqa: BLE001
        s.add(FAIL, "Anthropic not reachable", friendly_provider_error("Anthropic", e))


def check_elevenlabs(report: HealthReport, el_key: Optional[str],
                     probe: Optional[Callable[[str], int]] = None) -> None:
    s = report.section("ElevenLabs")
    if not el_key:
        s.add(WARN, "ElevenLabs check skipped (no key)")
        return
    try:
        n = probe(el_key) if probe is not None else _probe_elevenlabs(el_key)
        s.add(OK, f"ElevenLabs reachable - {n} voices in your account")
    except Exception as e:  # noqa: BLE001
        s.add(WARN, "ElevenLabs not reachable", friendly_provider_error("ElevenLabs", e))


def check_gemini(report: HealthReport, cfg: dict,
                 probe: Optional[Callable[[str, str], list]] = None) -> None:
    """Gemini model-id guard (issue #91) - only when [llm].provider == gemini. WARNS (never FAILS)
    if a configured model id isn't in the live list, catching a first-word 404 before it happens.
    `probe(base_url, key)->[model_ids]` injected for tests."""
    if (cfg.get("llm", {}) or {}).get("provider") != "gemini":
        return
    s = report.section("Gemini")
    try:
        from .firstrun import gemini_key
        key = gemini_key(cfg)
        if not key:
            s.add(WARN, "Gemini check skipped (no key)")
            return
        g = cfg.get("gemini", {}) or {}
        if probe is not None:
            live = probe(str(g.get("base_url", "")).strip(), key)
        else:
            from .providers.gemini_llm import list_gemini_models, _DEFAULT_BASE_URL
            base_url = str(g.get("base_url", "")).strip().rstrip("/") or _DEFAULT_BASE_URL
            live = list_gemini_models(base_url, key)
        s.add(OK, f"Gemini reachable - {len(live)} models")
        live_set = set(live)
        configured = {str(g.get("model", "")).strip()}
        configured |= {str(v).strip() for v in (g.get("tiers", {}) or {}).values()}
        # `-latest` aliases resolve server-side and don't appear verbatim - don't false-warn.
        missing = sorted(m for m in configured
                         if m and not m.endswith("-latest") and m not in live_set)
        if missing:
            s.add(WARN, "Configured Gemini model id(s) not in the live list: " + ", ".join(missing),
                  "A bad id 404s every turn - the -latest aliases are deprecation-proof.")
        else:
            s.add(OK, "Configured Gemini model / tiers are valid")
    except Exception as e:  # noqa: BLE001 - this guard must never fail the report
        s.add(WARN, "Gemini model check skipped", friendly_provider_error("Gemini", e))


def check_datasets(report: HealthReport) -> None:
    s = report.section("Game data freshness")
    try:
        from .nav.datasets import load_manifest, stale_datasets
        rows = load_manifest()
        if not rows:
            s.add(WARN, "No dataset manifest found")
            return
        stale = set(stale_datasets(183))  # ~6 months
        for d in rows:
            age = "age unknown" if d.age_days is None else f"{d.age_days}d old"
            status = WARN if d in stale else OK
            s.add(status, f"{d.label}: {d.row_count} rows, {age}")
        if stale:
            s.add(WARN, "Some game data is over 6 months old",
                  "Still works, but may miss the newest hulls - a refresh is due.")
    except Exception as e:  # noqa: BLE001 - freshness must never fail the report
        s.add(WARN, "Data freshness check skipped", str(e).splitlines()[0] if str(e) else "")


def check_audio(report: HealthReport, probe: Optional[Callable[[], dict]] = None) -> None:
    """Microphone/speaker visibility. `probe()` returns {inputs, outputs, default_in, default_out};
    injected for tests, defaults to sounddevice."""
    s = report.section("Audio devices")
    try:
        info = probe() if probe is not None else _probe_audio()
        ins, outs = info.get("inputs", 0), info.get("outputs", 0)
        if ins:
            s.add(OK, f"{ins} microphone(s) - default: {info.get('default_in', '?')}")
        else:
            s.add(FAIL, "No microphone found",
                  "COVAS can't hear you. Plug in / enable a mic, then pick it on the Settings page.")
        if outs:
            s.add(OK, f"{outs} speaker(s) - default: {info.get('default_out', '?')}")
        else:
            s.add(WARN, "No speaker found", "COVAS will fall back to on-screen text.")
    except Exception as e:  # noqa: BLE001
        s.add(FAIL, "Audio devices not visible", str(e).splitlines()[0] if str(e) else "")


# ---- real probes (network / hardware; not hit by the offline test run) ---------------------

def _probe_anthropic(key: str) -> int:
    import anthropic
    client = anthropic.Anthropic(api_key=key)
    return len(client.models.list(limit=20).data)


def _probe_elevenlabs(key: str) -> int:
    import requests
    r = requests.get("https://api.elevenlabs.io/v1/voices",
                     headers={"xi-api-key": key}, timeout=15)
    r.raise_for_status()
    return len(r.json().get("voices", []))


def _probe_audio() -> dict:
    import sounddevice as sd
    devs = sd.query_devices()
    return {
        "inputs": sum(1 for d in devs if d["max_input_channels"] > 0),
        "outputs": sum(1 for d in devs if d["max_output_channels"] > 0),
        "default_in": sd.query_devices(kind="input")["name"],
        "default_out": sd.query_devices(kind="output")["name"],
    }


# ---- orchestration ------------------------------------------------------------------------

def run_health(cfg: Optional[dict] = None, *, network: bool = True) -> HealthReport:
    """Run every check and return a structured report. `cfg` defaults to the app's loaded config
    (so the web route passes its live cfg and the CLI lets it load). `network=False` skips the
    provider-reachability probes (used by the offline test run / a quick local-only check)."""
    report = HealthReport()

    cfgs = report.section("Config")
    if cfg is None:
        try:
            from .config import load_config
            cfg = load_config()
            cfgs.add(OK, "config.toml loaded")
        except Exception as e:  # noqa: BLE001
            cfgs.add(FAIL, "config.toml could not be loaded", str(e).splitlines()[0] if str(e) else "")
            return report
    else:
        cfgs.add(OK, "config loaded")

    check_imports(report)
    anth_key, el_key = check_keys_and_files(report, cfg)
    if network:
        check_anthropic(report, anth_key)
        check_elevenlabs(report, el_key)
        check_gemini(report, cfg)
    check_datasets(report)
    check_audio(report)
    return report
