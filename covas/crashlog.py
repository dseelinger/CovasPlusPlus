"""Opt-in, off-by-default crash capture (issue #186).

"We can't fix what we never see" — but COVAS++ is privacy-first and phones nothing home. So this is
the privacy-preserving version of crash reporting: when the Commander **opts in** (`[crash_report]
enabled = true`, default **false**), an uncaught exception is written to a **redacted local file**
under the logs dir that the Commander can read and attach to a bug report. Nothing is transmitted;
the user stays in control of what they share.

The report is **redacted** before it ever hits disk: API keys, the Windows username / home path, and
`DPAPI:` blobs are scrubbed, so a pasted crash log can't leak secrets or PII. Redaction + formatting
are pure functions (offline-unit-tested); only `install()` touches process state / the filesystem, and
it is entirely fail-soft — crash *capture* must never itself crash the app.
"""
from __future__ import annotations

import os
import re
import sys
import traceback
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from .__version__ import __version__


def enabled(cfg: dict) -> bool:
    return bool((cfg.get("crash_report", {}) or {}).get("enabled", False))


# Redaction patterns — belt-and-suspenders. A crash traceback rarely contains a key, but a config
# repr or an env dump in a frame local might, so scrub aggressively before writing.
_USER = re.escape(os.environ.get("USERNAME", "") or "\0no-user\0")
_REDACTIONS = [
    (re.compile(r"DPAPI:[A-Za-z0-9+/=]+"), "DPAPI:<redacted>"),          # encrypted key blobs
    (re.compile(r"sk-[A-Za-z0-9_\-]{8,}"), "sk-<redacted>"),             # Anthropic/OpenAI-style keys
    (re.compile(r"xi-api-key['\"]?\s*[:=]\s*['\"]?[A-Za-z0-9_\-]+", re.I), "xi-api-key=<redacted>"),
    (re.compile(r"[A-Za-z]:\\Users\\[^\\/:*?\"<>|\r\n]+", re.I), r"C:\\Users\\<user>"),  # home path
    (re.compile(r"/home/[^/\r\n]+", re.I), "/home/<user>"),
]
if _USER and "\0" not in _USER:
    _REDACTIONS.append((re.compile(_USER, re.I), "<user>"))


def redact(text: str, cfg: dict | None = None) -> str:
    """Scrub secrets and PII (keys, DPAPI blobs, the username / home path) from `text`. Pure."""
    out = str(text)
    for pat, repl in _REDACTIONS:
        out = pat.sub(repl, out)
    return out


def format_report(exc_type, exc, tb, cfg: dict, *, version: str = __version__,
                  when: str | None = None) -> str:
    """Build a redacted, shareable crash report from an exception. Pure (caller passes `when`)."""
    llm = str((cfg.get("llm", {}) or {}).get("provider", "?"))
    tts = str((cfg.get("tts", {}) or {}).get("provider", "?"))
    header = [
        "COVAS++ crash report",
        f"version: {version}",
        f"time:    {when or ''}",
        f"platform: {sys.platform}  python: {sys.version.split()[0]}",
        f"providers: llm={llm} tts={tts}",
        "",
        "Traceback:",
    ]
    tbtext = "".join(traceback.format_exception(exc_type, exc, tb))
    return redact("\n".join(header) + "\n" + tbtext, cfg)


def write_report(cfg: dict, exc_type, exc, tb, *, now: datetime | None = None,
                 log: Callable[[str], None] | None = None) -> Path | None:
    """Write a redacted crash report to `<logs>/crash-<ts>.log`. Returns the path, or None if
    disabled / on any failure (fail-soft — capture must never crash the app)."""
    try:
        if not enabled(cfg):
            return None
        now = now or datetime.now()
        logs_dir = Path(str((cfg.get("logging", {}) or {}).get("dir", "logs")))
        logs_dir.mkdir(parents=True, exist_ok=True)
        path = logs_dir / f"crash-{now.strftime('%Y%m%d-%H%M%S')}.log"
        report = format_report(exc_type, exc, tb, cfg, when=now.isoformat(timespec="seconds"))
        path.write_text(report, encoding="utf-8", errors="replace")
        if log is not None:
            log(f"crash report written: {path}")
        return path
    except Exception:  # noqa: BLE001 — a failure to record a crash must never mask/replace it
        return None


def install(cfg: dict, *, log: Callable[[str], None] | None = None) -> bool:
    """Install a `sys.excepthook` that captures an uncaught exception to a redacted crash file —
    but only actually *writes* when crash reporting is opted in. The hook checks the LIVE config
    each time (`cfg` is the app's mutated-in-place dict), so toggling the setting takes effect
    without a restart. When disabled it's a transparent pass-through to the previous hook, so normal
    error output is unchanged. Fail-soft. Returns True (the hook is installed)."""
    previous = sys.excepthook

    def _hook(exc_type, exc, tb):
        write_report(cfg, exc_type, exc, tb, log=log)   # no-op unless enabled(cfg) at crash time
        try:
            previous(exc_type, exc, tb)
        except Exception:  # noqa: BLE001
            traceback.print_exception(exc_type, exc, tb)

    sys.excepthook = _hook
    if log is not None and enabled(cfg):
        log("crash reporting ON (opt-in) — uncaught errors saved to a redacted local file")
    return True
