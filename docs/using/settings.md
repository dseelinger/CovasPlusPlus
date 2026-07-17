# Settings by voice

> *"I change my settings by voice — the whisper model, thinking depth, personality, the voice,
> web search, and more."*

You can change most of COVAS++'s settings just by talking to it. It's backed by the **same
settings schema** the [web Settings page](../control-panel.md) uses, so the two can't disagree —
change a setting either way and it lands in the same place (`overrides.json`, layered over
`config.toml`).

**Example:** *"turn personality off"*

## What you can say

| You say… | Result |
|----------|--------|
| *"Set the Whisper model to small."* | Confirms: "Whisper model set to small" |
| *"Turn personality off."* | Personality disabled (say the same to turn it back on) |
| *"Use the George voice."* | Switches the ElevenLabs voice |
| *"Set thinking to high."* | Raises the thinking depth |
| *"Set the voice speed to 1.5."* | Sets the normalized voice speed (0.5–2.0×, 1.0 = normal) for the active TTS provider — each provider clamps it to its own range |
| *"Set the chatter min gap to 30 seconds."* | Makes space chatter more frequent in busy systems |
| *"Turn web search off."* | Disables automatic web search |
| *"What's my Whisper model set to?"* | Reads back the current value |

Ask **"what can I change?"** and it runs through the options.

## It validates — it never guesses

If you give a value that isn't allowed, COVAS++ **refuses and lists the valid options** rather
than silently picking something:

> *"Set the Whisper model to gigantic."*
> → *"That's not one of the options — try tiny, base, small, medium, or large-v3."*

And if you name something that isn't a setting at all (*"set the warp factor to 9"*), it routes to
[help](help.md) instead of inventing a setting.

!!! note "API keys aren't voice settings"
    Provider API keys (Anthropic, ElevenLabs, OpenAI, Gemini, Azure, Cartesia, Inara) are **not**
    changed by voice and don't live in `overrides.json`. Set or rotate them on the write-only,
    masked **API keys** card at the top of the [Settings page](../control-panel.md#api-keys), where
    they're stored encrypted (Windows DPAPI). A key change takes effect on restart.

## What applies immediately vs. on restart

- **Live** — a handful of settings take effect right away (the Whisper speech model reloads live,
  for instance).
- **On restart** — turning a whole **capability** on or off (e.g. enabling game monitoring or the
  keybinds) applies the next time you launch.

Your changes are written to `overrides.json`, so they persist across restarts and `config.toml`
stays pristine. Delete a key from the overrides to reset it, or use the per-setting **Reset**
button on the Settings page.

!!! info "Where your settings live"
    In the installed app, your `config.toml`, `overrides.json`, keys, personality/campaign, and
    checklist all live in **`%APPDATA%\COVAS++`** (a source run keeps them in the project root).
    That folder is outside the program files, so **[updating COVAS++](../getting-started/updating.md)
    never touches your settings** — every value you changed survives an upgrade. Paste
    `%APPDATA%\COVAS++` into File Explorer's address bar to open it.

## See also

- [The control panel → Settings page](../control-panel.md) — the same settings with sliders,
  dropdowns, inline help, and a search box.
- [Configuration reference](../configuration.md) — every setting, grouped by section.
