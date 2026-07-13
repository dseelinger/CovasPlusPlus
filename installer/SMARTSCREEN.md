# "Windows protected your PC" — installing COVAS++

COVAS++ is **not code-signed** (a code-signing certificate is an annual cost + business-identity
vetting we've chosen to skip — see `INSTALLER_DESIGN.md` decision #7). Because of that, when you
run **`COVAS++ Setup.exe`** the first time, Windows **SmartScreen** shows a blue warning:

> **Windows protected your PC**
> Microsoft Defender SmartScreen prevented an unrecognized app from starting.
> Running this app might put your PC at risk.

This is expected for any new app from a small publisher — it does **not** mean the app is unsafe.
To proceed:

1. Click **More info** (small link in the dialog).
2. A **Run anyway** button appears — click it.

The installer then runs with **no admin/UAC prompt** (COVAS++ installs per-user to
`%LOCALAPPDATA%\Programs\COVAS++`).

## Antivirus false positives

Frozen Python apps (PyInstaller) occasionally trip heuristic AV scanners. COVAS++ ships as a
one-folder build specifically to reduce this. If your AV quarantines it, restore it / add an
exclusion for `%LOCALAPPDATA%\Programs\COVAS++`. The source is public
(<https://github.com/dseelinger/CovasPlusPlus>) if you'd rather build it yourself.

## Why per-user (no admin)?

Installing to your user profile means no elevation prompt and keeps the app's writable state
(API keys, settings, logs) under `%APPDATA%\COVAS++`, separate from the read-only program files —
so updates never clobber your settings.
