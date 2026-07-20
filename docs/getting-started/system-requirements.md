# System requirements & performance

COVAS++ is deliberately light on your PC: the language model runs in the **cloud** (so it never
competes with Elite Dangerous for your GPU), and the only local machine-learning — speech-to-text
(Whisper) and the optional Piper voice — runs on the **CPU**. Your graphics card stays entirely
Elite's.

## Minimum & recommended

| | Minimum | Recommended |
|---|---------|-------------|
| **OS** | Windows 10 (64-bit) | Windows 11 |
| **CPU** | Any modern multi-core (last ~6 years) | 6+ cores |
| **RAM** | 8 GB | 16 GB (Elite is the heavy one) |
| **GPU** | **None needed by COVAS++** | Whatever Elite wants — COVAS doesn't touch it |
| **Disk** | ~600 MB (app + one Whisper model) | — |
| **Internet** | Needed for the cloud LLM and the one-time speech-model download | — |
| **Elite Dangerous** | Optional — COVAS runs without it, but game-awareness features need it | — |

There's **no VRAM requirement**: COVAS++ has no local GPU model (issue #128 removed the local LLM on
purpose — a useful one would fight Elite for the GPU). If a page elsewhere mentions "low-VRAM," the
practical constraint on COVAS++ is **RAM and CPU**, not VRAM.

## Running well on a modest PC (graceful degradation)

If your machine is on the lighter side, or you want to leave every scrap of headroom for Elite:

- **Use a smaller Whisper model.** `small.en` (the default, ~2 GB working set) is a good balance;
  drop to **`base.en`** or **`tiny.en`** on an 8 GB machine — still accurate for command-style
  speech, and much lighter. Set it on the [Settings page](../control-panel.md) (Speech-to-text →
  Whisper model). Larger models (`medium`, `large-v3`) want 5–10 GB and are rarely worth it here.
- **Use a free, light voice.** **Edge** (the default) is cloud and needs no local compute; **Piper**
  is fully local/offline and CPU-cheap. Both leave your GPU alone.
- **Keep thinking off** (the default) for snappy, low-cost replies.
- **Text mode** ([accessibility](../using/accessibility.md)) uses no mic and no speech model at all.

!!! tip "Let COVAS check for you"
    Open **Settings → Test my setup** and click the button — it reports your RAM and **warns if your
    Whisper model is heavy for it**, pointing you at a lighter one. (See the
    [install guide](install.md#4-verify-everything).)

## Staying up to date

COVAS++ checks GitHub for a newer release on launch and shows an **"update available"** banner on
the control panel's main page — one click downloads and installs it, and your keys/settings under
`%APPDATA%\COVAS++` are never touched (see [Updating COVAS++](updating.md)). **Test my setup** also
flags when you're behind, so you're not unknowingly running a six-week-old build when you file a bug.

## Crash reports (opt-in, private)

COVAS++ **phones nothing home**. But "we can't fix what we never see," so there's a privacy-first
way to help:

- On the [Settings page](../control-panel.md) under **Diagnostics**, turn on **Save crash reports**
  (it's **off by default**).
- With it on, an unexpected crash is written to a **redacted local file** in your logs folder
  (`%APPDATA%\COVAS++\logs\crash-<time>.log`) — your API keys, username, and home path are
  **scrubbed** before anything is written.
- **Nothing is transmitted.** You read the file and decide whether to attach it to a
  [bug report](../support.md). Turning it off captures nothing extra.
