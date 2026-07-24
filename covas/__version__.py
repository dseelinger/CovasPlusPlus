"""Single source of truth for the app version (INSTALLER_DESIGN.md — "Version string").

Two consumers: the runtime update-check (`covas/updates.py`) compares this against the
latest GitHub Release tag, and the eventual PyInstaller/Inno build stamps the installer
from it. Bump this — nothing else — to cut a release.
"""
__version__ = "0.27.2"
