# Running COVAS++

Launch COVAS++ from its **desktop icon** or the **Start menu**. It opens as a **native
window** — the control panel, in its own app window, no browser tab and no URL bar. The voice
loop starts with it. **Closing the window quits the app** — there's no background process and no
system tray.

## The window

The window is the [control panel](../control-panel.md): a live status light and log, a full
**Settings** page, and **Personality**, **Checklist**, and **CANCEL** controls. If an update is
available, an **update banner** appears across the top — see [Updating COVAS++](updating.md).

Everything runs locally on your machine. The panel is served on `127.0.0.1` under the hood and
isn't exposed to your network.

## The keys

Push-to-talk works whether or not the window has focus, so you can drive COVAS++ with Elite
Dangerous in the foreground:

| Action | Key |
|--------|-----|
| **Talk** | **Hold** <kbd>[</kbd> and speak; release when done |
| **Cancel / stop** | **Tap** <kbd>[</kbd> briefly (under ~400 ms) |
| **Quit** | Close the window (or <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>Q</kbd>) |

<kbd>[</kbd> is the default push-to-talk key; change it under `[keys]` in
[`config.toml`](../configuration.md), or by voice. You can bind a **joystick or HOTAS button** to
that same key with a tool like JoyToKey so you never take your hands off the stick. There's no
separate cancel key by default — a quick tap of the talk key cancels — and the panel's **CANCEL**
button always works too.

!!! tip "Ask it anything — including its version"
    The core voice loop works immediately. Try *"what can you do?"* for the guided tour, or
    *"what version are you?"* to hear the running version. (To **check for updates**, use the
    panel's update banner — that's a click, not a voice command.)

## Turning features on

The core voice loop works immediately. Most **Elite Dangerous** features — and especially the
ones that press keys — are **off until you opt in**. You enable them one of three ways:

- **The Settings page** — toggles that write to `overrides.json` (layered over `config.toml`).
- **By voice** — "turn game monitoring on," "turn the router on," etc.
- **`config.toml`** — the fully-commented defaults file in `%APPDATA%\COVAS++`.

!!! warning "Capability on/off applies on restart"
    Enabling or disabling a whole capability takes effect the **next time you launch**. A few
    settings (like the Whisper speech model) reload live, but toggling a feature on/off needs a
    restart. The one you'll almost always want first is `[elite].enabled = true` — it feeds live
    game state to nearly everything else.

## Where your settings live

Your keys, `overrides.json`, personality/campaign, checklist, and logs all live in
**`%APPDATA%\COVAS++`**. They persist across restarts *and* across updates, so you come back to
exactly the same configuration every time. (Paste `%APPDATA%\COVAS++` into File Explorer's
address bar to open the folder.)

## Quitting

Just **close the window** (or press <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>Q</kbd>). There's nothing
left running in the background.

## Running from source

If you [installed from source](install.md#run-from-source-advanced), you launch from PowerShell
instead — two entry points, same voice loop and settings:

```powershell
.\run_covas_app.bat     # native window (the packaged experience)
.\run_covas.bat         # headless — just the voice loop, no panel
.\run_covas_ui.bat      # voice loop + the panel in your default browser
```

The headless and browser paths print a status banner to the console (model, voice, Whisper
size, and which capabilities are ON/OFF) and are handy for development; the native-window path
(`run_covas_app`) is what the installer ships.

## Next

Learn how a turn actually flows — cues, cancel, and cost — in
**[The voice loop](voice-loop.md)**.
