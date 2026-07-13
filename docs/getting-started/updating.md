# Updating COVAS++

COVAS++ updates itself through **GitHub Releases** — there's no separate update server and
nothing for you to configure. On launch it quietly checks whether a newer release exists, and if
one does, it offers to install it. **Your settings and keys are always preserved.**

## How it works

1. **It checks on launch.** Each time COVAS++ starts, it compares its own version against the
   latest published [release](https://github.com/dseelinger/CovasPlusPlus/releases). This is a
   quick, read-only check that fails silently if you're offline — it never blocks startup.
2. **A banner appears.** If a newer version is out, an **"Update available → vX.Y"** banner
   shows across the top of the control panel. If you're already current, you'll never see it.
3. **You click to update.** When you choose to update, COVAS++ **downloads the new installer**
   for you, launches it, and **exits** so the installer can replace the running app. (A running
   program can't overwrite its own files, hence the hand-off.)
4. **The installer runs.** It installs the new version over the old one. The same
   [SmartScreen "unknown publisher"](install.md#1-download-the-installer) step applies — click
   **More info → Run anyway**.
5. **Relaunch.** Start COVAS++ again from its icon. You're on the new version.

!!! info "Updating is a click, never a voice command"
    You can ask COVAS++ *"what version are you?"* by voice, but **checking for and installing
    updates is a control-panel action only**. It triggers a network download and relaunch, which
    shouldn't be fireable by a stray voice command mid-flight — so it lives behind the banner
    button, not the microphone.

## Your settings always survive

An update replaces **only the app itself** (the read-only program files under
`%LOCALAPPDATA%\Programs\COVAS++`). It **never touches** your per-user data in
**`%APPDATA%\COVAS++`** — your keys, `overrides.json`, personality/campaign, checklist, and
logs. So:

- Every setting you changed — voice, mic, model, toggles — stays exactly as you left it.
- Defaults are applied **once**, at first run, and are **never re-applied** by an update. If you
  switched away from the default voice, an update won't switch it back.
- When a new version *adds* a setting, it fills in just that new setting's default without
  disturbing anything you'd already set (additive, not overwrite).

## Manual update

You can always skip the banner and update by hand: download the newest **`COVAS++ Setup.exe`**
from the [Releases page](https://github.com/dseelinger/CovasPlusPlus/releases) and run it. It
installs over your current version the same way, and your `%APPDATA%\COVAS++` data is left
untouched.

## Running from source?

If you [run from source](install.md#run-from-source-advanced), you update with `git` instead —
there's no in-app updater on that path:

```powershell
git pull
.venv\Scripts\pip install -r requirements.txt   # in case dependencies changed
```
