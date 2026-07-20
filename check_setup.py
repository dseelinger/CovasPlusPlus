"""
COVAS++  —  setup checker (CLI).

Confirms the foundation is solid: config loads, every Python package imports, your Anthropic key
(required) and ElevenLabs key (optional) are set and reachable, the bundled game data is fresh, and
your mic/speakers are visible. It changes nothing and sends no billable requests (the provider
calls are free lookups).

The actual checks live in `covas/health.py` (structured + importable), so the control-panel's
one-click "Test my setup" button runs the EXACT same checks and shows the same results without a
terminal (issue #181). This script just renders that report to the console and sets an exit code.
"""
from __future__ import annotations

import sys

from covas import health

_MARK = {health.OK: "  [ OK ] ", health.WARN: "  [warn] ", health.FAIL: "  [FAIL] "}


def main() -> None:
    print("COVAS++ setup check")
    report = health.run_health()  # loads config, runs every check (network probes included)
    for section in report.sections:
        print(f"\n=== {section.title} ===")
        for c in section.checks:
            print(_MARK.get(c.status, "  [ ?? ] ") + c.label)
            if c.detail:
                print("        " + c.detail)

    print("\n=== Result ===")
    if report.problems:
        print(_MARK[health.FAIL] + f"{len(report.problems)} issue(s): " + ", ".join(report.problems))
        print("\nFix the FAIL lines above, then run this again.")
        sys.exit(1)
    print(_MARK[health.OK] + "All systems go, Commander. Ready to fly.  o7")


if __name__ == "__main__":
    main()
