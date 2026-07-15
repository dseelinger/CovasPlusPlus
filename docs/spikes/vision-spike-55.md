# Spike #55 — Vision / screenshot awareness feasibility (vision-LLM)

**Status: research spike — go/no-go recommendation, not a shipped feature.**
**Recommendation: GO (narrow) — build a spike prototype of ON-DEMAND, router-tiered "look at my screen" only. NO-GO on any always-on / polling / closed-loop-control use of vision.**

---

## 1. Question

Can COVAS++ usefully "see" Elite Dangerous via screenshots + a vision-LLM, and is it worth the cost/complexity? Specifically: does it unlock things the ED journal / `Status.json` cannot expose (e.g. distance-to-target — the #50 tier-2 ceiling), and can it be done inside our existing cost stance (cloud tiering, prompt caching, no always-on burn)?

## 2. Exec summary

Yes — narrowly, and only on-demand. A vision-LLM can read values the journal never writes (distance-to-target, sub-target, throttle %, exact heading, GUI panel contents) and confirm what's on screen, at roughly **half a cent to a cent and a half per call** on our existing cheap/standard tiers — pennies per session when fired a handful of times on request. That is a genuine, concrete capability axis EDCoPilot and COVAS:NEXT **structurally cannot match**, because they read only telemetry ED writes to disk. The catch is the whole value collapses if vision is used continuously: a 1 fps polling loop would cost **~$5–$50/hour** and add 2–4 s of latency per frame, which is both unaffordable and useless for real-time control. So this fits our architecture *only* as an on-demand, wake-word/tool-gated, router-tiered lookup that mirrors the existing inline ED-context injection — one image, after the cached prefix, on the turn that asked for it. Go build that as an opt-in prototype; do not build a vision loop, an always-on HUD describer, or vision-driven aiming.

## 3. Screenshot capture on Windows (frozen-app + VR friendly)

The image is the easy part. Three library options, in ascending order of robustness and complexity:

| Option | How | PyInstaller-friendly | Fullscreen ED | VR mirror | Verdict |
|---|---|---|---|---|---|
| **`PIL.ImageGrab.grab()`** | GDI `BitBlt` via Pillow (already a dep) | Yes — zero new deps | Black frame under true fullscreen-exclusive DirectX | Grabs primary monitor only | Fine for a 30-line PoC; not the shipping path |
| **`mss`** | GDI `BitBlt` via ctypes, fast (~5–15 ms/grab) | Yes — pure-Python + ctypes, no native binary to bundle | Same fullscreen-exclusive caveat as ImageGrab | Monitor-region capture (grabs the flat mirror) | **Recommended PoC path.** Small, fast, freeze-clean |
| **Windows Graphics Capture (WGC)** | WinRT `Windows.Graphics.Capture` via `winsdk`/`pywinrt` | Heavier — WinRT bindings enlarge the bundle and need `collect_all` in `covas.spec` | Handles fullscreen + occlusion + DPI correctly (this is what OBS uses) | Can capture a **specific window** (the SteamVR/ED mirror window) directly | **Recommended shipping path** if the prototype earns a ship |

Key Windows facts that drive the choice:

- **Fullscreen-exclusive defeats GDI.** `mss`/`ImageGrab` return black frames when ED runs true fullscreen-exclusive DirectX. Mitigations: (a) ask users to run **borderless/windowed** (GDI capture then works, and most streamers already do this), or (b) use **WGC** (or DXGI Desktop Duplication), which capture the composited/GPU surface and are immune. Recommend documenting borderless for the PoC, WGC for ship.
- **VR.** In VR, ED renders into the HMD; the monitor shows a **mirror window** (a normal, capturable desktop window — often letterboxed / single-eye / barrel-distorted). Capturing the primary monitor with `mss` grabs that mirror as-is; WGC can target the mirror window by handle for a cleaner crop. Either way we capture *what the desktop shows*, which is the pragmatic definition of "what a headset user sees." We cannot (and should not try to) hook the compositor.
- **Frozen PyInstaller app.** `mss` and Pillow are pure-Python/ctypes and freeze cleanly with no extra `covas.spec` work. WGC/`winsdk` pulls WinRT and would need a `collect_all` entry plus a `--selftest` import guard (same pattern already used for `edge_tts`/`onnxruntime` — see DESIGN §3.6). This is a real but bounded packaging cost, and a reason to prototype on `mss` first.

**Capture is not the bottleneck.** A grab is 5–50 ms; downscale + JPEG/PNG encode is a few ms more. The cost and latency live in the LLM call, below.

## 4. Vision-LLM options on our provider seam

All three cloud LLM providers already on our seam accept image inputs, so vision requires **no new provider** — only that each provider's message translator learns to pass an image content block. The image rides in the user message as a content block alongside text:

```
{"role": "user", "content": [
    {"type": "text",  "text": "How far am I from the station?"},
    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "<b64>"}},
]}
```

Anthropic, OpenAI (`image_url`), and Gemini (`inline_data`) each have their own block shape; the normalization is per-provider translation, exactly like tool-call translation already done in `openai_llm.py` / `gemini_llm.py`.

### 4.1 Per-image token math (assumptions stated — verify before shipping)

| Provider | Image tokenization | Tokens for a downscaled ~1568px frame | Notes |
|---|---|---|---|
| **Anthropic (Claude)** | `tokens ≈ (w × h) / 750` (documented) | **~1,600** (Anthropic auto-caps older models at 1568px long edge) | High-res on Opus 4.8 / Sonnet 5 accepts up to **2576px** long edge → up to **~4,784 tokens/image**. *Downscale to ~1568px to control cost — we don't need HUD-pixel fidelity for most reads.* |
| **OpenAI (GPT-4o family)** | Tiled: ~85 base + ~170 per 512px tile ("high detail") | **~1,100** for a 1080p frame | **⚠ needs confirmation.** GPT-4o-mini applies a large image-token multiplier (~5–7×), so "mini" is **not** automatically cheaper for vision. |
| **Gemini (2.5 Flash/Pro)** | ~258 tokens for images ≤384px; tiled at 258/768px-tile above | **~258–800** | **⚠ needs confirmation.** Gemini's flat/low per-image token count + Flash's cheap input rate makes it the **cheapest vision path by a wide margin.** |

These are estimates from provider tokenization rules; the exact numbers should be confirmed against a live `count_tokens` (Anthropic) / usage readback (OpenAI/Gemini) before any cost promise is made in the UI. The repo's `[pricing]` table (`config.toml`) is the source of truth for $/Mtok and already costs any of these via `llm.estimate_cost`.

### 4.2 Rough $ per vision call

Assumptions: ~1,600 image tokens (downscaled), ~500 uncached prompt/context tokens, ~150 output tokens (short spoken reply), system+tools served from the existing prompt cache (~free). Rates from `config.toml` `[pricing]`.

| Tier → model | Input $/Mtok | Output $/Mtok | **≈ $ / vision call** | Note |
|---|---|---|---|---|
| **cheap → Haiku 4.5** | 1.00 | 5.00 | **~$0.003** (0.3¢) | Haiku *does* support vision; adequate for reading HUD text — the recommended default |
| **standard → Sonnet 5** | 3.00 (2.00 intro) | 15.00 (10.00 intro) | **~$0.009** (~$0.006 intro) | Escalation tier for ambiguous scenes |
| **premium → Opus 4.8** | 5.00 | 25.00 | **~$0.014** (1.4¢) | Rarely worth it for a screen read |
| **Gemini 2.5 Flash** | 0.30 | 2.50 | **~$0.0005** (<0.1¢) | Cheapest by far — ~258-token image + cheap rate |
| **OpenAI GPT-4o** | 2.50 | 10.00 | **~$0.005** (0.5¢) | GPT-4o-mini not necessarily cheaper (image multiplier) |

**On-demand, a session that "looks" 5–10 times costs 1.5–15¢. That is negligible.**

**The polling counter-example (why the cost stance is non-negotiable):** at 1 fps a vision loop is `~$0.003 × 3600 ≈ $10.80/hour` on cheap Haiku, `~$30/hr` on Sonnet, more on Opus — for a companion that talks a few times an hour. Even 0.2 fps is dollars/hour of pure background burn. **Vision must never be a polling loop.**

### 4.3 Latency

Grab ~5–50 ms · downscale/encode ~few ms · base64 + upload of a ~1,600-token image ~200–500 ms · vision-LLM time-to-first-token ~1–3 s. **Total ≈ 2–4 s per look.** Acceptable for a spoken "what's my distance?" Q&A; **far too slow for any closed-loop control** (aiming, "boost until within 7.5 km"). This latency floor is a second, independent reason vision cannot drive real-time macros.

### 4.4 Router / tiering fit

The router already picks a canonical tier per turn and maps it to the active provider's model (`router.py`, DESIGN §4). A vision turn is tiered **exactly like a text turn** — no new mechanism:

- **Default a vision look to the `cheap` tier** (Haiku 4.5 / Gemini Flash — both read HUD text fine), escalate to `standard` only for ambiguous scenes.
- The image is **uncached and on-demand** — it goes on that turn's user message *after* the cached system+tools prefix, so it never busts the prompt cache (identical to the inline ED-`context_block()` pattern in DESIGN §5).
- No always-on state; nothing added to conversation history (a stale screenshot must not linger — mirror how `context_block()` is stripped from stored history).

## 5. What it unlocks (and which are worth it)

| Unlock | Value | Worth pursuing? |
|---|---|---|
| **Reading on-screen values the journal does NOT expose** — distance-to-target/station, sub-target module, throttle %, exact speed/heading, GUI panel text | **Highest.** This is the differentiator and the **#50 tier-2 enabler** — the journal/`Status.json` do not stream live distance-to-target, so a vision *snapshot read* ("are we within 7.5 km?") is the only on-demand way to answer it today | **YES** — the headline use case |
| **Target ID / visual confirmation** — "what am I looking at?", confirm the right ship/station before acting | Medium. Nice grounding for callouts and a safety check before a keybind action | **Maybe** — cheap add-on once the read path exists |
| **Richer generative HUD content (#40)** | Low. Epic #40's HUD is deliberately **companion-state-centric** (voice-loop state + checklist + route) — it shows *what only COVAS++ knows*, which does **not** require vision. Vision could add scene flavor but that's not #40's thesis | **NO** (for now) — don't couple #55 to #40 |

## 6. Approach — how it plugs in

Mirror the existing **inline ED-context injection**, not a new subsystem:

1. **`VisionCapability` (opt-in, default off), gated two ways:**
   - a **wake phrase / detector** ("look at my screen", "what am I targeting", "read the distance") — a `VisionDetector` mirroring `ContextDetector` (DESIGN §5), **or**
   - an **LLM tool** `look_at_screen(question)` the model calls when a turn genuinely needs pixels.
2. On trigger, the app **captures one frame** (`mss` PoC / WGC ship), **downscales to ~1568px long edge**, encodes JPEG, and **attaches it as an image content block on that turn's user message** — after the cached prefix, never stored in history.
3. The **router tiers the turn** (default `cheap`); `stream_reply(messages, …)` carries the image because `messages` is already `list[dict]` — the only code change is teaching each provider's translator to emit its native image block. The seam signature is unchanged.
4. **Fail soft** (DESIGN principle): a black frame / capture error / vision failure degrades to a spoken "I couldn't read the screen," never crashes the loop.

This keeps vision a **capability plugin** (DESIGN §3.3), not a loop edit, and reuses the router, cache discipline, and fail-soft guarantees already in place.

## 7. Ties to other issues

- **#50 (voice/UI-authored macros — aim/distance):** #55 is the **enabler for tier-2's telemetry ceiling.** ED does not stream live distance-to-target, so a macro condition like *"within 7.5 km of the station"* is unavailable from the journal. An on-demand vision **read** can answer that as a *check* ("look now — are we close enough?"), but **not as a continuous trigger** (that's polling — banned). Tier-3 analog aiming ("boost toward the station") stays out of scope: the 2–4 s vision latency makes closed-loop aiming infeasible and unsafe.
- **#40 (VR overlay + simplified HUD):** #55 is **not** required for #40 and should not block or bloat it. #40's HUD renders companion state / checklist / route — data COVAS++ already owns. Note the relationship, keep them decoupled.

## 8. Beats-competitors thesis

EDCoPilot and COVAS:NEXT are **blind to anything ED doesn't write to disk** — they consume the journal/`Status.json`/EDDN only. On-demand vision lets COVAS++ answer questions grounded in **what is literally on the screen** — distance-to-target, the sub-module currently targeted, a GUI panel's contents, exact throttle/heading — values that **no journal-based companion can access**. That is a concrete capability axis competitors *structurally* cannot reach, and COVAS++ gets it while staying **cost-disciplined**: on-demand only, router-tiered to the cheapest capable model, pennies per session — not an always-on vision loop that would cost dollars per hour and add seconds of latency. "Sees what the journal can't, without a vision loop" is the differentiator.

## 9. Go / No-go

- **GO** — build an **opt-in, on-demand, router-tiered spike prototype** of the "read a value off the HUD on request" path (§6), targeting the #50 tier-2 read. Default tier `cheap`; default capture `mss` (borderless) with WGC noted as the fullscreen/VR-robust upgrade. Validate on Doug's hardware: does Haiku actually read distance-to-station / sub-target reliably off a real 1080p ED frame?
- **NO-GO** — no always-on vision, no polling loop, no vision-driven real-time aiming or continuous macro triggers, and no coupling to #40's HUD. These fail on cost (dollars/hour), latency (2–4 s), and the cost stance in CLAUDE.md.

## 10. What could not be verified in this spike

- **Exact per-image token counts for OpenAI and Gemini** — estimated from tokenization rules; confirm against live usage readback before any UI cost promise (§4.1). Anthropic's `(w×h)/750` is documented; the OpenAI tile math and Gemini flat/tiled counts are the softer numbers.
- **Whether a *cheap*-tier model actually reads ED HUD values reliably** — Haiku/Flash reading small, stylized, orange-on-black HUD text (distance, sub-target) at 1080p is unproven; needs an on-hardware A/B against Sonnet. This is the single most important thing the prototype must answer.
- **Fullscreen-exclusive capture behavior on Doug's exact setup** — the black-frame caveat is documented Windows behavior but not verified against this ED install / GPU; the throwaway PoC (`docs/spikes/screenshot_poc.py`) is written but **un-run against a live game**.
- **VR mirror-window fidelity** — whether the letterboxed/distorted mirror is legible enough for a vision read is unknown without an HMD session.

---

*Throwaway capture PoC: `docs/spikes/screenshot_poc.py` — `mss`-based, byte-compiled only, NOT run against a live ED session. It is deliberately outside the shipped `covas/` package and adds no runtime dependency.*
