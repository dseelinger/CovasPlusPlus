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
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

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
    llm, *, model: Optional[str] = None, max_tokens: int = 900  # noqa: ANN001 — LLMProvider
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


def _extract_json(text: str) -> Optional[dict]:
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


def load_cache(path: Path) -> tuple[Optional[str], dict[str, str]]:
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
    generate: Optional[Callable[[str], str]],
    *,
    cache_path: Path,
    log: Optional[Callable[[str], None]] = None,
) -> Optional[Pairing]:
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


def _lookup_ci(mapping: Optional[dict], name: str) -> Optional[str]:
    """Case-insensitive name lookup returning a non-empty value, else None."""
    want = str(name or "").strip().lower()
    for k, v in (mapping or {}).items():
        if str(k).strip().lower() == want and v:
            return str(v)
    return None


def voice_for_persona(explicit_map: Optional[dict], pairings: Optional[dict],
                     persona_name: str) -> Optional[str]:
    """The voice to use for `persona_name`: an EXPLICIT user choice ALWAYS wins over an auto
    pairing; with neither, None (keep the current default). Case-insensitive name match. Pure."""
    if not str(persona_name or "").strip():
        return None
    return _lookup_ci(explicit_map, persona_name) or _lookup_ci(pairings, persona_name)
