# Running COVAS++

There are two ways to run COVAS++: **headless** (just the voice loop) and **with the web control
panel** (the same voice loop plus a browser dashboard). Both use the same keys and settings.

## Headless — just the voice loop

```powershell
.\run_covas.bat
# or:  .venv\Scripts\python.exe run_covas.py
```

At launch it prints a banner summarizing your setup, for example:

```text
================ COVAS++ ================
  Router     : ON (default claude-haiku-4-5)
  Model      : claude-sonnet-5
  Voice      : Sarah
  Whisper    : small
  ED monitor : ON
  Proactive  : ON
  Keybinds   : OFF
  Auto-honk  : OFF
  Find module: ON
  Personality: ON
  Cache TTL  : 1h
  Dev mock   : OFF
  TALK        : hold  [[]
  CANCEL      : tap   [[] briefly
  QUIT        : Ctrl+Alt+Q (or close this window)
=========================================
Hold the PTT key and speak, Commander.
```

That block is your at-a-glance status: which model and voice are active, your speech model, and
which game-awareness features are **ON** or **OFF**. If something you expected to work is `OFF`,
that's where you'll see it — turn it on in [`config.toml`](../configuration.md) or the Settings
page and relaunch.

## With the web control panel

```powershell
.\run_covas_ui.bat
# or:  .venv\Scripts\python.exe run_covas_ui.py
```

This runs the same voice loop **and** starts a local dashboard. It prints:

```text
================ COVAS++ ================
  Control panel : http://127.0.0.1:8765
  Talk          : hold [[]
  Cancel        : tap  [[] briefly
  Quit          : Ctrl+Alt+Q or close this window
=========================================
```

Your browser opens **[http://127.0.0.1:8765](http://127.0.0.1:8765)** automatically a second
after launch. From there you get a live status light and log, a full **Settings** page, a
**Personality** editor, a **Checklist** editor, and a **CANCEL** button that always works. See
[The control panel](../control-panel.md) for the full tour.

The panel is local-only (`127.0.0.1`) — it isn't exposed to your network. You can change the host
and port under `[ui]` in [`config.toml`](../configuration.md) if you need to.

## The keys

| Action | Key |
|--------|-----|
| **Talk** | **Hold** <kbd>[</kbd> and speak; release when done |
| **Cancel / stop** | **Tap** <kbd>[</kbd> briefly (under ~400 ms) |
| **Quit** | <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>Q</kbd> (or close the window) |

<kbd>[</kbd> is the default push-to-talk key; change it under `[keys]` in
[`config.toml`](../configuration.md), or by voice. You can bind a **joystick or HOTAS button** to
that same key with a tool like JoyToKey so you never take your hands off the stick. There's no
separate cancel key by default — a quick tap of the talk key cancels — and the panel's **CANCEL**
button always works too.

## Turning features on

The core voice loop works immediately. Most **Elite Dangerous** features — and especially the ones
that press keys — are **off until you opt in**. You enable them one of three ways:

- **`config.toml`** — the fully-commented defaults file. Edit a section's `enabled = ` line.
- **The Settings page** — toggles that write to `overrides.json` (layered over `config.toml`).
- **By voice** — "turn game monitoring on," "turn the router on," etc.

!!! warning "Capability on/off applies on restart"
    Enabling or disabling a whole capability takes effect the **next time you launch**. A few
    settings (like the Whisper speech model) reload live, but toggling a feature on/off needs a
    restart. The one you'll almost always want first is `[elite].enabled = true` — it feeds live
    game state to nearly everything else.

## Quitting

Press <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>Q</kbd>, or just close the console window. Your settings
persist in `overrides.json`, so you'll come back to exactly the same configuration next time.

## Next

Learn how a turn actually flows — cues, cancel, and cost — in
**[The voice loop](voice-loop.md)**.
