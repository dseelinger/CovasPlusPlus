"""Tier-2 self-update check (INSTALLER_DESIGN.md decisions #5 + #6).

On launch the UI asks GitHub for the repo's latest Release, semver-compares its tag to the
baked-in `__version__`, and — if newer — shows an "update available" banner. GitHub Releases
IS the update server; there's no infrastructure to run. The *action* behind the banner
(`download_and_launch_installer`) streams the new installer to a temp file, launches it, and
the app exits so the installer can replace files a running .exe holds open.

Two hard rules live here:
  * **Fail-soft.** A background update check must never break startup or the UI. Every network
    or parse error collapses to "no update available" — never an exception out of
    `check_for_update`.
  * **Never touch user state.** This module only fetches metadata and downloads an installer to
    temp; it writes nothing under the user-data dir. Preserving %APPDATA%\\COVAS++ is the
    installer's job (decision #6), and it's kept honest by not being given a reason to write there.

The semver compare + release-JSON parse are pure and unit-tested offline with fake payloads;
the network fetch and the download/launch are only exercised on real hardware.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import urllib.parse

import requests

from .__version__ import __version__

# Hosts a release installer may be fetched from. GitHub release-asset URLs live on github.com and
# redirect to *.githubusercontent.com; nothing else is a legitimate update source. This fences the
# download-and-execute SINK itself (defense in depth) so even a bug upstream can't aim it elsewhere.
_TRUSTED_ASSET_HOSTS = ("github.com", "githubusercontent.com")


def _is_trusted_asset_url(url: str) -> bool:
    """True only for an https URL whose host is github.com/githubusercontent.com (or a subdomain).
    Anything else — other scheme, other host, unparseable — is rejected."""
    try:
        parts = urllib.parse.urlsplit(url or "")
    except Exception:  # noqa: BLE001 — a malformed URL is simply untrusted
        return False
    if parts.scheme != "https":
        return False
    host = (parts.hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in _TRUSTED_ASSET_HOSTS)

# GitHub's /releases/latest already excludes drafts and prereleases, so this one call is the
# whole "which version is current" story. We still re-check the flags defensively in
# parse_release in case the endpoint (or a hand-fed payload) ever surprises us.
GITHUB_LATEST = "https://api.github.com/repos/dseelinger/CovasPlusPlus/releases/latest"

_NUM = re.compile(r"\d+")


def _version_key(s: str) -> tuple[tuple[int, int, int], int]:
    """Turn a version string into a sortable key. Tolerates a leading 'v', missing components
    (padded with zeros), and a trailing prerelease/build tag. A *release* sorts ABOVE its own
    prereleases (1.2.3 > 1.2.3-rc1) via the trailing flag; prerelease identifiers aren't
    ranked against each other because we ignore prereleases entirely (they never reach here
    from /releases/latest). Non-numeric junk in a component reads as 0 rather than raising."""
    s = s.strip()
    if s[:1].lower() == "v":
        s = s[1:]
    core = re.split(r"[-+]", s, maxsplit=1)[0]        # drop prerelease/build metadata
    nums: list[int] = []
    for part in core.split("."):
        m = _NUM.match(part.strip())
        nums.append(int(m.group()) if m else 0)
    while len(nums) < 3:
        nums.append(0)
    is_release = 0 if re.search(r"-", s) else 1        # release outranks its prereleases
    return (tuple(nums[:3]), is_release)  # type: ignore[return-value]


def is_newer(latest: str, current: str) -> bool:
    """True iff `latest` is a strictly newer version than `current` (semver order). Any
    unparseable input fails soft to False — we never prompt an update we can't justify."""
    try:
        return _version_key(latest) > _version_key(current)
    except Exception:  # noqa: BLE001 — a malformed tag must not raise into the UI
        return False


def _installer_asset(assets: list) -> str | None:
    """The Windows-installer download URL from a release's assets, if one is attached. Picks
    the first '.exe' — the Inno build (I6) produces a single `COVAS++ Setup.exe`."""
    for a in assets or []:
        name = str((a or {}).get("name") or "").lower()
        if name.endswith(".exe"):
            return (a or {}).get("browser_download_url")
    return None


def parse_release(payload) -> dict | None:
    """Extract the usable bits from a GitHub /releases/latest JSON object, or None if the
    payload is a draft/prerelease or carries no tag. Pure — unit-tested with fake payloads."""
    if not isinstance(payload, dict):
        return None
    if payload.get("draft") or payload.get("prerelease"):
        return None
    tag = payload.get("tag_name") or payload.get("name")
    if not tag:
        return None
    return {
        "tag": str(tag),
        "url": payload.get("html_url", "") or "",
        "asset_url": _installer_asset(payload.get("assets", [])),
    }


def check_for_update(current: str = __version__, url: str = GITHUB_LATEST,
                     timeout: float = 6) -> dict:
    """Ask GitHub for the latest release and decide whether it's newer than `current`.

    Always returns a dict, never raises (fail-soft): on any offline/HTTP/JSON error the
    result is simply available=False. Shape:
        {"available": bool, "current": str, "latest": str|None,
         "url": str, "asset_url": str|None}
    """
    info: dict = {"available": False, "current": current, "latest": None,
                  "url": "", "asset_url": None}
    try:
        r = requests.get(url, headers={"Accept": "application/vnd.github+json"},
                         timeout=timeout)
        r.raise_for_status()
        rel = parse_release(r.json())
    except Exception:  # noqa: BLE001 — offline / rate-limited / malformed: no update, no crash
        return info
    if rel is None:
        return info
    info["latest"] = rel["tag"]
    info["url"] = rel["url"]
    info["asset_url"] = rel["asset_url"]
    # "Available" means one-click INSTALLABLE, not merely "a newer tag exists". `gh release create`
    # publishes the release instantly, but CI attaches `COVAS++ Setup.exe` ~3 min later; advertising
    # an update in that window sent the user to a GitHub page instead of downloading (no asset to
    # apply). Gate on the installer asset so the banner (and the health "Updates" line, which points
    # at that banner) only surface an update you can actually install from the app. Self-heals the
    # moment the asset lands. COVAS ships an installer with every release, so a release with no asset
    # is by definition "not ready yet", never a deliberate source-only drop.
    info["available"] = is_newer(rel["tag"], current) and bool(rel["asset_url"])
    return info


def download_and_launch_installer(asset_url: str, timeout: float = 300) -> str:
    """Tier-2 apply: stream the installer to a temp file and launch it detached, then return
    the temp path. The caller is responsible for exiting the app afterwards — a running .exe
    can't overwrite itself, so the installer only proceeds once we're gone.

    Unlike the background check this RAISES on failure: it's a user-initiated action, so the
    UI should surface a download/launch error rather than swallow it. Writes only to the OS
    temp dir — never the user-data dir (decision #6: updates must not clobber user state).

    RAISES on an untrusted `asset_url`: this streams to a temp .exe and launches it, so the URL must
    come from GitHub (github.com / githubusercontent.com). Callers derive it from `check_for_update`
    server-side; the check here guards the sink regardless of caller."""
    if not _is_trusted_asset_url(asset_url):
        raise ValueError(f"refusing to fetch an installer from an untrusted URL: {asset_url!r}")
    fd, path = tempfile.mkstemp(prefix="COVAS++-setup-", suffix=".exe")
    os.close(fd)
    with requests.get(asset_url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                if chunk:
                    f.write(chunk)
    # Detached so the installer outlives our imminent exit (Windows: no controlling terminal
    # to tie it to ours). close_fds keeps it from inheriting our open handles.
    subprocess.Popen([path], close_fds=True)
    return path
