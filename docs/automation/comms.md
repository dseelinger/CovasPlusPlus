# Send in-game messages by voice

> *"Tell local o7." — "Ready to send to local/system chat: 'o7.' Confirm to send?" — "Send it." —
> message away.*

COVAS++ can compose and send **Elite Dangerous chat** for you — to **local/system**, your **wing**,
your **squadron**, or as a **direct message** — from what you say out loud, without opening the
comms panel and typing. You speak the intent; COVAS composes the words, **reads them back**, and
sends only after you confirm.

**Examples:**

- *"Tell local o7."*
- *"Message my wing: forming up at the nav beacon."*
- *"Send a direct message: thanks for the assist, cmdr."*

!!! danger "Off by default — and outward-facing"
    This types a message **other Commanders will see**. It's off until you set
    `[comms_send].enabled = true`. There is **no way to skip the confirmation** — COVAS always reads
    the message back and sends only on a **separate** command.

## How it stays safe — read-back before send

Sending is **outward-facing**: the words go to real people, so a garbled transcription reaching
strangers is the risk to design against. The single, unconditional safeguard is a
**read-back-before-send gate**:

1. **Compose never sends.** When you ask, COVAS composes the message and reads it back — *"Ready to
   send to wing chat: 'forming up at the nav beacon.' Confirm to send?"* — but injects **nothing**
   yet.
2. **Confirm on a separate turn.** You must say **"confirm"** / **"send it"** on a *new* command.
   The assistant physically cannot compose-and-send in one breath (the confirmation is turn-gated,
   exactly like [keybind automation](keybinds.md)), so you always hear the exact words first. At
   send time COVAS **re-states the actual composed message and channel** — a *"Confirming — sending
   to wing chat: 'forming up…'"* line taken straight from what's armed, not from how the read-back
   was phrased — so a swapped message would be audible before it leaves.
3. **Cancel or reword.** Say **"cancel"** (or "no", "belay") to discard it. An un-confirmed message
   also **expires** after a timeout (default 60 s), so a stale "confirm" can't fire it later.

Unlike benign keybinds, there is **no** immediate-send path here — confirmation is mandatory.

## How the text gets in — clipboard paste

A chat message is arbitrary text, not a single keystroke. Rather than replaying each character
(fragile across keyboard layouts and dead keys), COVAS puts the finished message on the **Windows
clipboard** and **pastes** it into the chat box with **Ctrl+V**, then presses **Enter**. The
scripted sequence is:

**open the comms box (keybind) → select the channel (keybind, if configured) → paste the text →
Enter.**

The composed text is normalised to a single line first (newlines collapsed, length capped), so it
can't commit early or run away.

Because the paste goes into **whatever window has focus**, COVAS brings the Elite Dangerous
window to the front first, so a message can't misfire into a browser or the control panel. This
is a no-op when ED is already focused, and it's governed by the shared
[`[keybinds].focus_before_inject`](keybinds.md#focus-the-game-window-105) setting (on by default);
turn it off to paste into whatever window currently has focus.

## Set up your binds

COVAS presses **keyboard** keys, so the controls it needs must be bound to a **key** in Elite
Dangerous (a HOTAS/mouse-only bind can't be pressed):

- **Open-comms box** — `[comms_send].open_bind`, default `QuickCommsPanel`. Bind *"Quick Comms
  Panel"* to a key in ED (Controls). This is the one control the feature always needs.
- **Per-channel select** (optional) — Elite has no universal per-channel *send* key, so the
  channel-select binds are yours to set. Leave a channel **blank** to send on your
  **currently-selected** comms channel — perfectly fine for the common local-chat case. If you've
  bound keys that switch channels, put their ED action tokens in `channel_local` / `channel_wing` /
  `channel_squadron` / `channel_direct` and COVAS will switch for you. An unbound-but-configured key
  fails soft with a spoken *"bind it in-game."* These four binds (and `settle_seconds`) are on the
  **Settings page** under **Comms** and are voice-settable (*"set the squadron chat bind to …"*), so
  you no longer have to hand-edit `config.toml` to point *"send to my squadron"* at the right channel.

## Settings

| Setting | What it does |
|---------|--------------|
| `comms_send.enabled` | Master switch (**off** by default) |
| `comms_send.open_bind` | ED action token that opens the chat text box (default `QuickCommsPanel`) |
| `comms_send.confirm_window` | Seconds a composed message stays confirmable (default 60) |
| `comms_send.settle_seconds` | Pause after focus/channel/paste so the field keeps up (default 0.15) |
| `comms_send.channel_local` / `_wing` / `_squadron` / `_direct` | Per-channel select binds; blank = current channel |

No game-state monitoring is required — the read-back is the safety, not a combat guard. The send
shares the same key executor as [keybinds](keybinds.md) and [auto-honk](auto-honk.md), so a hard
**"abort"** releases any key it pressed. See the [Configuration reference](../configuration.md).

## Beats the competition

EDCoPilot and COVAS:NEXT narrate *incoming* comms; COVAS++ composes and sends *outgoing* chat
hands-free — behind a mandatory read-back so nothing garbled ever reaches another Commander.
