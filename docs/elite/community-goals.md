# Community goals

> *"I list the active community goals, tell you what system a goal is in, and how you're standing
> in one."*

Ask about **Community Goals** (CGs) by voice. COVAS++ is **journal-primary**: the CGs you've
visited and your own standing come straight from your game journal, so they work offline with no
setup. Add a free Inara API key and it can also surface active CGs you *haven't* visited yet.

**Example:** *"what community goals are active"*

## What you can say

| You say… | It does… |
|----------|----------|
| *"List the community goals."* | Names the active CGs (title, system, end time) — and, with an Inara key, flags ones you haven't visited ("…and there's one in *system* you haven't visited yet") |
| *"What system is the *&lt;CG title&gt;* community goal in?"* | Resolves the goal by (fuzzy) title, speaks its system, and copies it to your clipboard |
| *"What's my standing in the *&lt;CG title&gt;* goal?"* | Reports "Top 10 Commanders" or your percentile band (e.g. "top 25%") |

Your standing is *as of your last visit to that CG's board* — COVAS++ notes that, and if a CG isn't
in your journal at all it tells you it doesn't have your standing yet ("visit the board").

The system lookup follows the [already-there rule](location-carriers.md#the-youre-already-there-rule):
if the CG is in your current system, it says so and skips the clipboard copy.

## The optional Inara feed

- **No key** → journal-only. You get the CGs you've visited and your standing; COVAS++ just notes
  it can't see unvisited ones right now.
- **With a key** → the complete active list, so CGs you haven't been to still surface (which is the
  whole point of adding the feed).

To add one: create a free generic API key at **inara.cz → Settings → 3rd party APIs**, then paste it
into the **API keys** card on the control panel's Settings page. It's stored the same way as every
other key — encrypted at rest with Windows DPAPI in `InaraAPIKey.txt` (git-ignored), never in plain
text. Changing it takes effect on restart.

!!! note "Upgrading from an older version"
    If you previously set `[cg].inara_api_key` in `config.toml` or `overrides.json`, COVAS++ moves it
    into the encrypted `InaraAPIKey.txt` automatically on the next run and blanks the old plaintext
    value — no action needed.

## Settings

| Setting | What it does |
|---------|--------------|
| `cg.source` | `inara` (external feed) or `none` (journal-only) |
| Inara API key | Entered on the Settings **API keys** card; stored encrypted, blank = journal-only |

See the [Configuration reference](../configuration.md#community-goals-cg).
