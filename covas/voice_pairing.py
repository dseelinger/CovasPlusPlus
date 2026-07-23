"""Auto-pair a sensible default voice per pre-built persona (issue #96).

At startup the app asks the cheap LLM, ONCE, to match each shipped persona (name + body) to a
voice from the active TTS provider's catalog (ElevenLabs first — the richest metadata), so a
freshly-selected persona arrives already sounding right instead of everyone defaulting to the one
configured voice. This module is the PURE, offline-testable core of that flow:

  * `pairing_key` — a stable hash of (persona set + available voice ids) so the result is CACHED
    and recomputed ONLY when the personas or the account's voice list actually change;
  * `build_pairing_prompt` — the one batched LLM input (personas + the catalog WITH metadata);
  * `make_pairing_generator` — the thin `generate(prompt) -> text` adapter over an LLMProvider;
  * `parse_pairing_response` — tolerant JSON parsing + validation against the real ids/personas;
  * `load_cache` / `save_cache` — the git-ignored per-account cache;
  * `pair_voices` — the cache-keyed orchestrator (recompute only on change), fail-soft to None;
  * `voice_for_persona` — the apply rule: an EXPLICIT user voice always wins over an auto pairing.

Everything is dependency-injected (the generator + cache path are passed in), so the default
`pytest` run exercises it with fakes — no network, no LLM, no ElevenLabs. Fail-soft is the rule:
any error (LLM off, bad JSON, unreadable cache, empty catalog) yields NO pairing, never an
exception, so a persona is never left voiceless and startup is never blocked.
"""
from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# Where the generated cache lives by default (git-ignored; per-account, never committed).
DEFAULT_CACHE = "personalities/voice_pairings.json"
# Label keys we surface to the model, in a stable order, so the prompt (and thus the cache-warming
# call) is deterministic for a given catalog.
_LABEL_KEYS = ("gender", "age", "accent", "descriptive", "description", "use_case", "use case")


def default_cache_path(cfg: dict) -> Path:
    """The configured cache path (already resolved to absolute by config.py), or the default."""
    raw = (cfg.get("personality", {}) or {}).get("voice_pairings_file") or DEFAULT_CACHE
    return Path(raw)


def pairing_key(personas: list[dict], voice_ids: list[str]) -> str:
    """A stable hash of the persona set (name + body) AND the available voice ids. The mapping is
    recomputed only when this key changes — i.e. a persona was added/edited or the account's voice
    list changed — so a normal launch reuses the cache and makes NO LLM call. Order-independent."""
    h = hashlib.sha256()
    for p in sorted(personas, key=lambda p: (p.get("name") or "").strip().lower()):
        h.update((p.get("name") or "").strip().encode("utf-8"))
        h.update(b"\x00")
        h.update((p.get("body") or "").strip().encode("utf-8"))
        h.update(b"\x1e")
    h.update(b"\x1d")
    for vid in sorted({str(v) for v in voice_ids}):
        h.update(vid.encode("utf-8"))
        h.update(b"\x1e")
    return h.hexdigest()


def _voice_line(v: dict) -> str:
    """One compact catalog line: id, name, category, and whatever labels/description exist."""
    parts = [f"voice_id={v.get('voice_id')}", f"name={v.get('name')!r}"]
    if v.get("category"):
        parts.append(f"category={v['category']}")
    labels = v.get("labels") or {}
    seen: set[str] = set()
    for k in _LABEL_KEYS:
        val = labels.get(k)
        if val and k not in seen:
            seen.add(k)
            parts.append(f"{k}={val}")
    if v.get("description"):
        parts.append(f"description={v['description']}")
    return "; ".join(parts)


# The STATIC instruction prefix — kept first and byte-stable so a prompt-caching provider can reuse
# it across the (rare) repeat calls; only the persona/catalog block that follows varies.
_PAIRING_PREFIX = (
    "You are casting voices for an Elite Dangerous voice companion. Match EACH persona below to the "
    "single best-fitting voice from the available catalog, using the persona's character and the "
    "voice's metadata (gender, age, accent, description, use-case). A voice MAY be reused if it "
    "fits more than one persona. Reply with ONLY a JSON object of the form:\n"
    '{"pairings": [{"persona": "<persona name>", "voice_id": "<voice_id>", "reason": "<one short line>"}]}\n'
    "Use the EXACT persona names and voice_id values given. No prose outside the JSON."
)


def build_pairing_prompt(personas: list[dict], voices: list[dict]) -> str:
    """The single batched LLM input: the static rules prefix, then each persona (name + body) and
    the whole voice catalog with its metadata. Pure + deterministic for a given input."""
    lines = [_PAIRING_PREFIX, "", "PERSONAS:"]
    for p in personas:
        lines.append(f"- {p.get('name')}: {(p.get('body') or '').strip()}")
    lines.append("")
    lines.append("VOICES:")
    for v in voices:
        lines.append(f"- {_voice_line(v)}")
    return "\n".join(lines)


def make_pairing_generator(
    llm, *, model: str | None = None, max_tokens: int = 900  # noqa: ANN001 — LLMProvider
) -> Callable[[str], str]:
    """Adapt an LLMProvider into a `generate(prompt) -> text` callable by accumulating its streamed
    reply. Thin by design (matches the chatter/comms adapters); the app injects the cheap tier."""
    def generate(prompt: str) -> str:
        parts: list[str] = []
        for kind, chunk in llm.stream_reply(
            [{"role": "user", "content": prompt}], threading.Event(), lambda *_a: None,
            model=model, max_tokens=max_tokens,
        ):
            if kind == "text":
                parts.append(chunk)
        return "".join(parts).strip()

    return generate


def _extract_json(text: str) -> dict | None:
    """Best-effort: pull the first {...} object out of a reply (models sometimes wrap it in prose
    or a ```json fence). Returns the parsed dict, or None if nothing parses."""
    t = (text or "").strip()
    if not t:
        return None
    start = t.find("{")
    end = t.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        obj = json.loads(t[start:end + 1])
    except (ValueError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def parse_pairing_response(text: str, valid_ids: set[str],
                          valid_names: list[str]) -> dict[str, str]:
    """Parse the model's JSON into a validated `{persona_name -> voice_id}` map. An entry is KEPT
    only when its persona matches a known persona (case-insensitive, mapped back to the canonical
    name) AND its voice_id is a real catalog id — anything invented is silently dropped. Pure."""
    obj = _extract_json(text)
    if not obj:
        return {}
    canon = {str(n).strip().lower(): n for n in valid_names}
    out: dict[str, str] = {}
    for entry in obj.get("pairings") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("persona") or "").strip().lower()
        vid = str(entry.get("voice_id") or "").strip()
        canon_name = canon.get(name)
        if canon_name and vid in valid_ids:
            out[canon_name] = vid
    return out


@dataclass(frozen=True)
class Pairing:
    """A computed (or cached) pairing: the `{persona -> voice_id}` map and the key it was keyed by
    (so the app can tell a fresh compute from a cache hit for logging)."""
    mapping: dict[str, str]
    key: str
    from_cache: bool = False


def load_cache(path: Path) -> tuple[str | None, dict[str, str]]:
    """(key, mapping) from the cache file, or (None, {}) when missing/unreadable/malformed."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None, {}
    if not isinstance(raw, dict):
        return None, {}
    mapping = raw.get("pairings")
    key = raw.get("key")
    if not isinstance(mapping, dict) or not isinstance(key, str):
        return None, {}
    return key, {str(k): str(v) for k, v in mapping.items()}


def save_cache(path: Path, key: str, mapping: dict[str, str]) -> None:
    """Persist (key, mapping) to the git-ignored cache. Fail-soft — a write error is swallowed
    (the app just recomputes next launch)."""
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"key": key, "pairings": mapping}, indent=2), encoding="utf-8")
    except OSError:
        pass


def pair_voices(
    personas: list[dict],
    voices: list[dict],
    generate: Callable[[str], str] | None,
    *,
    cache_path: Path,
    log: Callable[[str], None] | None = None,
) -> Pairing | None:
    """Resolve the persona→voice mapping, using the cache when it's still valid (recompute ONLY when
    the persona set or the voice list changed — the whole point of the key). On a miss it calls the
    injected generator ONCE, validates the result against the real ids/personas, and saves it.

    Fail-soft: no personas/voices, no generator, a generator error, or an empty/garbage reply all
    return None (NO pairing — the app keeps the current default voice). Never raises."""
    if not personas or not voices:
        return None
    voice_ids = [str(v.get("voice_id")) for v in voices if v.get("voice_id")]
    names = [p.get("name") for p in personas if p.get("name")]
    key = pairing_key(personas, voice_ids)

    cached_key, cached_map = load_cache(cache_path)
    if cached_key == key and cached_map:
        # Keep only pairings still valid for the current catalog (a voice may have been removed).
        valid = {n: v for n, v in cached_map.items() if v in set(voice_ids)}
        if valid:
            return Pairing(mapping=valid, key=key, from_cache=True)

    if generate is None:
        return None
    try:
        reply = generate(build_pairing_prompt(personas, voices))
    except Exception as e:  # noqa: BLE001 — a generator failure yields no pairing, never a crash
        if log is not None:
            log(f"voice pairing: generator error ({type(e).__name__})")
        return None
    mapping = parse_pairing_response(reply, set(voice_ids), [n for n in names if n])
    if not mapping:
        if log is not None:
            log("voice pairing: no usable pairings in the reply")
        return None
    save_cache(cache_path, key, mapping)
    if log is not None:
        log(f"voice pairing: paired {len(mapping)} persona(s)")
    return Pairing(mapping=mapping, key=key, from_cache=False)


def _lookup_ci(mapping: dict | None, name: str) -> str | None:
    """Case-insensitive name lookup returning a non-empty value, else None."""
    want = str(name or "").strip().lower()
    for k, v in (mapping or {}).items():
        if str(k).strip().lower() == want and v:
            return str(v)
    return None


def voice_for_persona(explicit_map: dict | None, pairings: dict | None,
                     persona_name: str) -> str | None:
    """The voice to use for `persona_name`: an EXPLICIT user choice ALWAYS wins over an auto
    pairing; with neither, None (keep the current default). Case-insensitive name match. Pure."""
    if not str(persona_name or "").strip():
        return None
    return _lookup_ci(explicit_map, persona_name) or _lookup_ci(pairings, persona_name)


# --- Locale-aware voice pairing (issue #182 layer 4, #198) ------------------------------------
# When the reply language is non-English, a voice that can't pronounce it reads the reply badly.
# These PURE helpers steer the configured voice to one that speaks the active language — but only
# when it would otherwise mispronounce, and never over an EXPLICIT user choice (which we flag
# instead). Edge/Azure tag voices with a BCP-47 `locale`; ElevenLabs/OpenAI don't (multilingual /
# untagged), so `i18n.voice_speaks` treats those as "copes" and we leave them alone.

# Which config key each TTS provider stores its single reply voice under (the value `voice_speaks`
# is evaluated against). Only Edge/Azure carry locale-tagged catalogs we can steer within; the
# others are listed so the resolver can no-op cleanly rather than raise on an unknown provider.
_PROVIDER_VOICE_KEY: dict[str, tuple[str, str]] = {
    "edge": ("edge", "voice"),
    "azure": ("azure", "voice"),
    "elevenlabs": ("elevenlabs", "voice_id"),
    "openai_tts": ("openai_tts", "voice"),
    "cartesia": ("cartesia", "voice"),
}


def _vid(v: dict) -> str:
    """A normalized voice dict's id — providers expose it as `ref` (Edge/Azure cast shape) or
    `voice_id` (ElevenLabs). Blank string when neither is present."""
    return str((v or {}).get("ref") or (v or {}).get("voice_id") or "")


@dataclass(frozen=True)
class LanguageVoice:
    """The outcome of steering a reply voice to the active language (issue #198).

    `voice_id` is the voice to USE (the current one when we keep it, a new one when we steer, or
    None when there was no current voice to begin with). `steered` is True only when we changed
    it; `mismatch` is True when the result can't actually speak the language — either because an
    explicit user voice was respected, or because the catalog has no voice for that language — so
    the caller can warn instead of silently mispronouncing."""
    voice_id: str | None
    steered: bool = False
    mismatch: bool = False


def _best_speaker(speakers: list[dict], prefer_gender: str | None) -> dict | None:
    """Pick a locale-appropriate voice: prefer one matching `prefer_gender` (keep the persona's
    feel), else the first (the catalog is already sorted deterministically). None if empty."""
    if not speakers:
        return None
    want = (prefer_gender or "").strip().lower()
    if want:
        for v in speakers:
            if str(v.get("gender") or "").strip().lower() == want:
                return v
    return speakers[0]


def pick_language_voice(
    voices: list[dict],
    code: str | None,
    *,
    current: str | None = None,
    explicit: bool = False,
    prefer_gender: str | None = None,
) -> LanguageVoice:
    """Resolve which voice should read a reply in ISO 639-1 language `code`, given the provider's
    `voices` catalog (normalized `{ref/voice_id, name, gender, locale}` dicts) and the `current`
    voice id. PURE and fully offline-testable — the whole point of layer 4 (#198).

    The rule, in order:

    1. No target language (`code` blank/None — English default or unmapped): no-op, keep `current`.
    2. `current` already speaks it (locale tag matches, or the provider is untagged/multilingual):
       keep it — "the pairing would NOT mispronounce", so don't steer.
    3. `current` can't speak it:
       - EXPLICIT user choice -> respect the override, but flag `mismatch` (the user set this voice
         on purpose; we warn rather than override it).
       - otherwise (an auto/default voice) -> STEER to the best catalog voice that speaks the
         language (same gender when possible).
    4. No catalog voice speaks it -> can't steer; keep `current` and flag `mismatch`.
    """
    from .i18n import voice_speaks

    voices = voices or []
    if not (code or "").strip():
        return LanguageVoice(current, steered=False, mismatch=False)

    by_id = {_vid(v): v for v in voices if _vid(v)}
    cur = by_id.get(str(current)) if current is not None else None

    # Already fine (speaks it, or an untagged/unknown voice we assume copes)?
    if current is not None and voice_speaks((cur or {}).get("locale"), code):
        return LanguageVoice(current, steered=False, mismatch=False)

    # A mismatch. An explicit user voice is honored, but surfaced.
    if explicit:
        return LanguageVoice(current, steered=False, mismatch=True)

    speakers = [v for v in voices if voice_speaks_strict(v, code)]
    pick = _best_speaker(speakers, prefer_gender or (cur or {}).get("gender"))
    if pick is not None:
        pid = _vid(pick)
        return LanguageVoice(pid, steered=(pid != str(current or "")), mismatch=False)

    # Nothing in the catalog speaks it — keep what we have and let the caller warn.
    return LanguageVoice(current, steered=False, mismatch=True)


def voice_speaks_strict(v: dict, code: str | None) -> bool:
    """Like `i18n.voice_speaks`, but for PICKING a replacement: an untagged voice does NOT qualify
    (we only steer TO a voice we can positively confirm speaks the language)."""
    from .i18n import voice_speaks

    if not str((v or {}).get("locale") or "").strip():
        return False
    return voice_speaks((v or {}).get("locale"), code)


def reply_voice_patch(
    cfg: dict,
    voices: list[dict],
    *,
    explicit: bool = False,
) -> tuple[dict | None, LanguageVoice]:
    """Given the live `cfg` and the active provider's `voices` catalog, return `(patch, outcome)`
    where `patch` is a config patch that steers the reply voice to the active language — or None
    when nothing should change. `outcome` (a `LanguageVoice`) carries the mismatch flag so the
    caller can warn. Reads `[language].reply` and `[tts].provider` from cfg; honors the
    `[language].match_voice` opt-out. PURE (no network) — the caller fetches the catalog. """
    from . import i18n

    lang = cfg.get("language", {}) or {}
    if not lang.get("match_voice", True):
        return None, LanguageVoice(None)
    code = i18n.language_code(i18n.reply_language(cfg))
    if not code:  # English / unmapped -> never steer
        return None, LanguageVoice(None)

    provider = str((cfg.get("tts", {}) or {}).get("provider") or "").strip().lower()
    keys = _PROVIDER_VOICE_KEY.get(provider)
    if not keys:
        return None, LanguageVoice(None)
    section, field = keys
    current = str((cfg.get(section, {}) or {}).get(field) or "") or None

    outcome = pick_language_voice(voices, code, current=current, explicit=explicit)
    if not outcome.steered or not outcome.voice_id:
        return None, outcome
    return {section: {field: outcome.voice_id}}, outcome
