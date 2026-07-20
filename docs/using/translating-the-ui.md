# Translating the control panel

The web control panel's text is **extracted for translation** (issue #196, layer 2 of the
[localization epic](language.md)). Today COVAS++ ships the **English baseline** only — the
mechanism is in place so a translator can add a language without touching templates or Python
logic.

!!! note "Why English-only for now"
    A half-translated panel is more confusing than an honestly English one, so a language is
    **gated off until its catalog is complete**. Shipping the extraction mechanism first means the
    strings are ready to translate; it does **not** mean COVAS auto-translates them (it never
    machine-translates the UI).

## How it works

Every user-visible string in the templates is wrapped in a tiny gettext-style helper:

```html
<button>{{ t('SAVE') }}</button>
```

`t()` is a lookup keyed by the **English source string itself**:

- For English (and any language without a shipped catalog), `t('SAVE')` returns `'SAVE'` unchanged
  — so the English panel renders exactly as it always did.
- For a language with a catalog, `t('SAVE')` returns the translation.

The catalog and the language gate live in [`covas/ui_i18n.py`](https://github.com/dseelinger/CovasPlusPlus/blob/main/covas/ui_i18n.py):

```python
CATALOGS: dict[str, dict[str, str]] = {
    "en": {},   # identity — English is the source, so it needs no entries
}
```

The **active** UI language follows your [reply language](language.md) (`[language].reply`) — but
only if a catalog for it exists. An untranslated or unmapped reply language falls back to English.

## Adding a language

You need no Python beyond editing one table, and you never touch the templates.

1. **Collect the strings.** Every string wrapped in `{{ t('…') }}` across `covas/templates/` (and
   the first-run wizard) is a message to translate. The English source string is the key.
2. **Add a catalog** to `CATALOGS` in `covas/ui_i18n.py`, keyed by the ISO 639-1 code (the same
   codes used for [reply language](language.md) — `de`, `fr`, `ru`, `es`, `pt`). For example:

    ```python
    CATALOGS = {
        "en": {},
        "de": {
            "SAVE": "SPEICHERN",
            "← control panel": "← Bedienfeld",
            "Reply language": "Antwortsprache",
            # …every wrapped string…
        },
    }
    ```

3. **Complete it before shipping.** Only add a language to `CATALOGS` once its catalog covers the
   wrapped strings — the presence of the key is what activates it. A missing key falls back to
   English, so a partial catalog produces a **mixed** panel; that's exactly what the gate exists to
   avoid.
4. **Keep the HTML in translated values.** A few strings carry inline markup (`<b>…</b>`,
   `<a href="…">…</a>`) — keep the tags in your translation; they're rendered as-is.

Once the catalog is present, set **Reply language** to that language (or `[language].reply` in
`config.toml`) and the panel switches to it.

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
