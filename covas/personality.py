"""Personality composition (N7) — Base + Persona + Campaign.

The system prompt is composed from three separable pieces so switching the VOICE never
touches the Commander's personal facts:

  * Base    — shared ground rules for every persona (from `personalities/presets.md`,
              shipped + committed, no personal data).
  * Persona — the voice/register (one of the presets in that file, or a saved Custom one,
              git-ignored). Selected by name via `[personality].persona`.
  * Campaign — the Commander's personal facts (name, ranks, holdings, ongoing goals),
              stored git-ignored like `personality.txt` is today.

`compose_system(cfg)` assembles the three (when personality is ON) and `llm.build_system`
delegates here. Pure parsing/composition + small file reads/writes; everything is offline
and unit-testable (DESIGN §9).
"""
from __future__ import annotations

import re
from pathlib import Path

# --- config helpers --------------------------------------------------------
_DEF_PRESETS = "personalities/presets.md"
_DEF_CAMPAIGN = "campaign.txt"
_DEF_CUSTOM_DIR = "personalities/custom"


def _p(cfg: dict, key: str, default: str) -> Path:
    return Path(str(cfg.get("personality", {}).get(key, default) or default))


# --- presets.md parsing ----------------------------------------------------
_HEADER = re.compile(r"^##\s+(Base|Persona)\b(.*)$")
_ITALIC_SUFFIX = re.compile(r"\*\(.*?\)\*")


def parse_presets(text: str) -> tuple[str, list[dict]]:
    """Parse `presets.md` into (base, personas). Each persona is
    {name, body, preview, source='preset'}; `preview` is the illustrative quote (for the UI),
    kept OUT of `body` (what's sent to the model)."""
    base = ""
    personas: list[dict] = []
    kind: str | None = None
    name: str | None = None
    buf: list[str] = []

    def flush() -> None:
        nonlocal base
        if kind == "base":
            base = _clean("\n".join(buf))
        elif kind == "persona" and name:
            body_lines, preview = _split_preview(buf)
            personas.append({"name": name, "body": _clean("\n".join(body_lines)),
                             "preview": preview, "source": "preset"})

    for line in text.splitlines():
        m = _HEADER.match(line)
        if m:
            flush()
            kind = m.group(1).lower()
            buf = []
            if kind == "persona":
                rest = m.group(2)
                after = rest.split("—", 1)[1] if "—" in rest else rest
                name = _ITALIC_SUFFIX.sub("", after).strip().strip("*").strip()
            else:
                name = None
            continue
        if kind is not None:
            buf.append(line)
    flush()
    return base, personas


def _split_preview(lines: list[str]) -> tuple[list[str], str]:
    """Separate a persona block's blockquote preview (> *"…"*) from its description body."""
    body: list[str] = []
    preview_parts: list[str] = []
    for ln in lines:
        s = ln.strip()
        if s.startswith(">"):
            preview_parts.append(s.lstrip(">").strip().strip("*").strip().strip('"').strip("*"))
        else:
            body.append(ln)
    return body, " ".join(p for p in preview_parts if p).strip()


def _clean(text: str) -> str:
    """Trim horizontal rules and surrounding blank lines from a parsed block."""
    lines = [ln for ln in text.splitlines() if ln.strip() != "---"]
    return "\n".join(lines).strip()


# --- persona sources (presets + custom) ------------------------------------
def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-") or "persona"


def load_presets(cfg: dict) -> tuple[str, list[dict]]:
    """(base, preset personas) from the configured presets file. Fail-soft: ('', []) if the
    file is missing/unreadable."""
    p = _p(cfg, "presets_file", _DEF_PRESETS)
    try:
        return parse_presets(p.read_text(encoding="utf-8"))
    except OSError:
        return "", []


def load_custom_personas(cfg: dict) -> list[dict]:
    """Custom personas saved under the (git-ignored) custom dir. Each file: a '# Name' header
    then the body. Missing dir -> []."""
    d = _p(cfg, "custom_dir", _DEF_CUSTOM_DIR)
    out: list[dict] = []
    try:
        files = sorted(d.glob("*.md"))
    except OSError:
        return out
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        name, body = _parse_custom(text, fallback=f.stem)
        out.append({"name": name, "body": body, "preview": "", "source": "custom"})
    return out


def _parse_custom(text: str, *, fallback: str) -> tuple[str, str]:
    lines = text.splitlines()
    name = fallback.replace("-", " ").title()
    body_start = 0
    for i, ln in enumerate(lines):
        if ln.strip().startswith("# "):
            name = ln.strip()[2:].strip()
            body_start = i + 1
            break
    return name, "\n".join(lines[body_start:]).strip()


def list_personas(cfg: dict) -> list[dict]:
    """All selectable personas: presets first, then customs. Custom overrides a preset of the
    same name (case-insensitive)."""
    _base, presets = load_presets(cfg)
    customs = load_custom_personas(cfg)
    custom_names = {p["name"].strip().lower() for p in customs}
    merged = [p for p in presets if p["name"].strip().lower() not in custom_names]
    merged.extend(customs)
    return merged


def find_persona(cfg: dict, name: str | None) -> dict | None:
    """The persona with `name` (case-insensitive), else the first available (Classic-ish)."""
    personas = list_personas(cfg)
    if not personas:
        return None
    want = str(name or "").strip().lower()
    for p in personas:
        if p["name"].strip().lower() == want:
            return p
    return personas[0]


def save_custom_persona(cfg: dict, name: str, body: str) -> str:
    """Write a custom persona to the git-ignored custom dir (creating it). Returns the display
    name saved. A blank name/body is rejected by the caller; here we just persist."""
    d = _p(cfg, "custom_dir", _DEF_CUSTOM_DIR)
    d.mkdir(parents=True, exist_ok=True)
    name = name.strip()
    (d / f"{_slug(name)}.md").write_text(f"# {name}\n\n{body.strip()}\n", encoding="utf-8")
    return name


# --- campaign (personal facts, git-ignored) --------------------------------
def read_campaign(cfg: dict) -> str:
    """The Commander's campaign text. Falls back to the legacy monolithic personality.txt when
    no campaign file exists yet (back-compat migration: your old prompt keeps shaping replies as
    the campaign until you set a proper one via the Personality tab)."""
    cf = _p(cfg, "campaign_file", _DEF_CAMPAIGN)
    if cf.exists():
        try:
            return cf.read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    legacy = cfg.get("personality", {}).get("file")
    if legacy:
        lp = Path(legacy)
        if lp.exists():
            try:
                return lp.read_text(encoding="utf-8").strip()
            except OSError:
                return ""
    return ""


def save_campaign(cfg: dict, text: str) -> None:
    cf = _p(cfg, "campaign_file", _DEF_CAMPAIGN)
    cf.parent.mkdir(parents=True, exist_ok=True)
    cf.write_text(text.strip() + "\n", encoding="utf-8")


# --- composition -----------------------------------------------------------
def compose_system(cfg: dict) -> str | None:
    """The composed system prompt (Base + selected Persona + Campaign) when personality is ON,
    else None (neutral replies). Fail-soft: any missing piece is simply omitted."""
    if not cfg.get("personality", {}).get("enabled"):
        return None
    base, _presets = load_presets(cfg)
    persona = find_persona(cfg, cfg.get("personality", {}).get("persona"))
    campaign = read_campaign(cfg)
    parts: list[str] = []
    if base.strip():
        parts.append(base.strip())
    if persona and persona["body"].strip():
        parts.append(persona["body"].strip())
    if campaign.strip():
        parts.append("The Commander (your campaign — personal facts, treat as ground truth):\n"
                     + campaign.strip())
    return "\n\n".join(parts) if parts else None
