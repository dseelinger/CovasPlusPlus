# Translating the control panel

The web control panel's text is **extracted for translation** (issue #196, layer 2 of the
[localization epic](language.md)). COVAS++ ships the **English baseline** plus complete catalogs for
the five curated languages — German, French, Russian, Spanish and Portuguese. Those five are
**machine-translated (LLM) and awaiting native-speaker review**; the mechanism lets a translator
review or replace any catalog without touching templates or Python logic.

!!! note "Complete-catalog gate"
    A half-translated panel is more confusing than an honestly English one, so a language is
    **only activated once its catalog covers every string** (enforced by a test). A language you
    have no complete catalog for falls back to a fully-English panel — never a mix.

## How it works

Every user-visible string in the templates is wrapped in a tiny gettext-style helper:

```html
<button>{{ t('SAVE') }}</button>
```

`t()` is a lookup keyed by the **English source string itself**:

- For English (and any language without a shipped catalog), `t('SAVE')` returns `'SAVE'` unchanged
  — so the English panel renders exactly as it always did.
- For a language with a catalog, `t('SAVE')` returns the translation.

Each language's catalog is a JSON file under
[`covas/translations/`](https://github.com/dseelinger/CovasPlusPlus/blob/main/covas/translations)
(`de.json`, `fr.json`, `ru.json`, `es.json`, `pt.json`) — a flat `{english source: translation}`
map. `covas/ui_i18n.py` auto-discovers every `*.json` there and registers it; English stays an
implicit identity (no file needed). The lookup and the language gate live in `ui_i18n.py`.

The **active** UI language follows your [reply language](language.md) (`[language].reply`) — but
only if a complete catalog for it exists. An untranslated or unmapped reply language falls back to
English.

## Reviewing or adding a language

You edit one JSON file and never touch the templates or Python.

1. **Find the keys.** Every string wrapped in `{{ t('…') }}` across `covas/templates/` (and the
   first-run wizard) is a message; the English source string is the JSON key. An existing catalog
   (e.g. `de.json`) already lists all of them.
2. **Edit or create the catalog** at `covas/translations/<code>.json`, keyed by the ISO 639-1 code
   (the same codes as [reply language](language.md) — `de`, `fr`, `ru`, `es`, `pt`). For example:

    ```json
    {
      "SAVE": "SPEICHERN",
      "← control panel": "← Bedienfeld"
    }
    ```

3. **Cover every key.** A catalog must translate **every** template key — a missing one would fall
   back to English and produce a mixed panel. The test
   `test_shipped_catalog_covers_every_template_key` enforces exact coverage (no missing, no stale),
   so `pytest` tells you immediately if a key is unhandled.
4. **Preserve the non-text parts verbatim.** Keep inline HTML (`<b>…</b>`, `<a href="…">…</a>`),
   HTML entities, any `[section].key` config tokens, URLs, and Elite Dangerous / brand proper nouns
   (system, station, ship, engineer and module names; COVAS++, Anthropic, Edge, Whisper, …) exactly
   as in the English source — translate only the surrounding prose.

The moment the file is present and complete, setting **Reply language** to that language (or
`[language].reply` in `config.toml`) switches the panel to it.

## What's covered — and what isn't yet

Covered: the **server-rendered** chrome of every panel (navigation, headings, buttons, labels,
placeholders, hints, banners, empty states, and accessibility `aria-label`/`title` text) plus the
first-run wizard.

Not yet extracted (tracked as a follow-up, not this layer):

- **JavaScript-built strings** — text assembled in each page's inline `<script>` (live status
  names, log lines, the provider/voice blocks, toast messages). These are produced client-side, not
  by `t()`.
- **Settings labels and help** — the per-setting label, description and category shown on the
  Settings page come from `covas/settings_schema.py` and are rendered from the API payload, so they
  localize at that data layer rather than in the template.

Both are natural next passes on the same mechanism.
