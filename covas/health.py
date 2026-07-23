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

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

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
    ("pywhispercpp", "pywhispercpp"),
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


def check_keys_and_files(report: HealthReport, cfg: dict) -> tuple[str | None, str | None]:
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


def check_anthropic(report: HealthReport, anth_key: str | None,
                    probe: Callable[[str], int] | None = None) -> None:
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


def check_elevenlabs(report: HealthReport, el_key: str | None,
                     probe: Callable[[str], int] | None = None) -> None:
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
                 probe: Callable[[str, str], list] | None = None) -> None:
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
            from .providers.gemini_llm import _DEFAULT_BASE_URL, list_gemini_models
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


def check_updates(report: HealthReport, current: str | None = None,
                  probe: Callable[[str], dict] | None = None) -> None:
    """Update-available check (issue #186): nudge a stale build before the user files a bug against
    one. Reuses the fail-soft `updates.check_for_update` (GitHub Releases). `probe(current)->info`
    injected for tests; fail-soft (a network hiccup WARNs, never FAILs)."""
    s = report.section("Updates")
    try:
        from .__version__ import __version__
        cur = current or __version__
        info = probe(cur) if probe is not None else _probe_updates(cur)
        if info.get("available"):
            s.add(WARN, f"An update is available: {info.get('latest')} (you have {cur})",
                  "Update from the banner on the control panel's main page.")
        else:
            s.add(OK, f"Up to date (version {cur})")
    except Exception as e:  # noqa: BLE001 — an update check must never fail the report
        s.add(WARN, "Update check skipped", str(e).splitlines()[0] if str(e) else "")


# Rough RAM footprint of each Whisper size — used only to WARN a low-RAM machine toward a lighter
# model (graceful degradation, issue #186). Approximate working-set, not exact.
_WHISPER_RAM_GB = {
    "tiny": 1, "tiny.en": 1, "base": 1, "base.en": 1, "small": 2, "small.en": 2,
    "medium": 5, "medium.en": 5, "large-v3": 10,
}


def check_system(report: HealthReport, cfg: dict,
                 probe: Callable[[], float | None] | None = None) -> None:
    """Minimum-requirements / graceful-degradation check (issue #186). Reports total RAM and WARNs
    if the configured Whisper model is heavy for it, pointing at a lighter model. `probe()->RAM GB`
    (or None if unknown) is injected for tests. Speech is CPU-only, so RAM — not VRAM — is the real
    constraint."""
    s = report.section("System")
    try:
        ram = probe() if probe is not None else _probe_ram_gb()
        model = str((cfg.get("whisper", {}) or {}).get("model", "small.en"))
        need = _WHISPER_RAM_GB.get(model, 2)
        if ram is None:
            s.add(OK, f"Whisper model: {model} (couldn't read total RAM)")
            return
        s.add(OK, f"{ram:.0f} GB RAM, Whisper model {model}")
        if ram < 8:
            s.add(WARN, "Less than 8 GB RAM — the recommended minimum",
                  "COVAS runs, but close other apps; a smaller Whisper model helps.")
        if ram < need + 4:  # leave headroom for Elite + the OS
            s.add(WARN, f"Whisper '{model}' may be heavy for {ram:.0f} GB RAM alongside Elite",
                  "Set a smaller model (small.en / base.en / tiny.en) on the Settings page.")
    except Exception as e:  # noqa: BLE001
        s.add(WARN, "System check skipped", str(e).splitlines()[0] if str(e) else "")


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


def check_audio(report: HealthReport, probe: Callable[[], dict] | None = None) -> None:
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


def _probe_updates(current: str) -> dict:
    from .updates import check_for_update
    return check_for_update(current)


def _probe_ram_gb() -> float | None:
    """Total physical RAM in GB, Windows-first (GlobalMemoryStatusEx) with an os.sysconf fallback;
    None if it can't be determined. No new dependency."""
    try:
        import ctypes

        class _MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        stat = _MEMORYSTATUSEX(); stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))  # type: ignore[attr-defined]
        return stat.ullTotalPhys / (1024 ** 3)
    except Exception:  # noqa: BLE001 — not Windows, or the call failed
        try:
            import os  # local, like the ctypes import above — only the POSIX fallback needs it
            return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / (1024 ** 3)
        except Exception:  # noqa: BLE001
            return None


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

def run_health(cfg: dict | None = None, *, network: bool = True) -> HealthReport:
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
    check_system(report, cfg)
    check_audio(report)
    if network:
        check_updates(report)
    return report
