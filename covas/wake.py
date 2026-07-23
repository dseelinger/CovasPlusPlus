"""Wake-word gate — an optional arming phrase for hands-free mode (issue #64).

Continuous listening (issue #63) opens a turn on *any* speech the mic hears. In a room
where you also talk to other people — or where the mic catches the TV — that fires the LLM
on utterances you never meant for COVAS. The wake-word gate sits IN FRONT of the continuous
turn: when ``[listen].wake_word`` is set, a captured utterance only runs a turn if its
TRANSCRIPT carries the wake phrase; otherwise the turn is dropped before it ever reaches the
model. Push-to-talk is a deliberate act, so it stays UNGATED — a PTT press always runs.

Design mirrors the ED / memory detectors (``covas/ed/detector.py``,
``covas/memory/detector.py``): a pure rules class that is a function of *(config, text)*
only — no audio, no network, no LLM, no threads — so match / no-match / strip / fuzzy are
all exercised offline with plain strings. The interesting part (keyword spotting) runs on
the LOCALLY-produced Whisper transcript, which is the simplest reliable wake path: no extra
model, no new dependency, and it can't burn cloud tokens on a false trigger because the drop
happens before the LLM call.

Rules, all case-insensitive and whitespace/punctuation-robust:

* **Disabled** — an empty ``wake_word`` means the gate is OFF: every utterance is armed and
  passes through UNCHANGED, so continuous mode behaves exactly as issue #63 shipped.
* **Match** — the phrase is spotted anywhere in the utterance (typically leading: "COVAS,
  what's my fuel"). Fuzzy tolerance (on by default) forgives the STT slips a short call sign
  attracts — "Kovas", "Covis", "Cove us" — via a per-word similarity ratio, so a real
  address isn't lost to a one-letter mistranscription.
* **Strip** — only the wake phrase itself is removed from what the model sees (both sides of
  it are preserved), so "COVAS, what's my fuel" becomes "what's my fuel" and the model never
  parrots its own name back. A turn that is *only* the wake word cleans to empty — the caller
  treats that as "armed but nothing to say" and returns to Idle.
* **No match** — the utterance is dropped: not armed, not spoken to, no tokens spent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

# Words only (unicode) — punctuation and spacing are matched around, never against. Matching
# on word tokens (not a raw substring) stops a wake word "cove" from arming on "discover".
_WORD_RE = re.compile(r"\w+", re.UNICODE)

# Per-word similarity a fuzzy candidate must reach to count as the wake word. Tuned so common
# one-letter Whisper slips of a short call sign pass ("kovas"/"covis" ~0.8 vs "covas") while
# unrelated short words ("gas", "cover") stay well below.
_FUZZY_THRESHOLD = 0.72


@dataclass(frozen=True)
class WakeResult:
    """Outcome of gating one transcript. ``armed`` is whether a turn should run; ``text`` is
    the wake-phrase-stripped command the model should see (only meaningful when armed);
    ``reason`` is an explainable log line. Twin of ``ed.detector.ContextRef`` in spirit."""
    armed: bool
    text: str
    reason: str


@dataclass(frozen=True)
class WakeWordConfig:
    """Immutable snapshot of the wake-word policy from ``[listen]``. Kept separate from the
    gate so :meth:`WakeWordGate.check` stays a pure function of (config, text). Default is
    DISABLED (empty phrase) — the feature is opt-in and OFF out of the box."""
    phrase: str = ""          # the arming phrase; empty = gate disabled
    fuzzy: bool = True        # tolerate STT slips of the call sign (Kovas/Covis)

    @classmethod
    def from_cfg(cls, cfg: dict) -> WakeWordConfig:
        listen = (cfg or {}).get("listen", {}) or {}
        d = cls()
        return cls(
            phrase=str(listen.get("wake_word", d.phrase) or ""),
            fuzzy=bool(listen.get("wake_word_fuzzy", d.fuzzy)),
        )


class WakeWordGate:
    """Decides whether a hands-free utterance is addressed to COVAS. Build once from config;
    :meth:`check` is pure over (config, text). Not tied to any audio path — the app feeds it
    the local transcript and honours the result. Mirrors the shape of the ED/memory detectors
    so the worker loop consults it the same way it does those."""

    def __init__(self, cfg: WakeWordConfig) -> None:
        self.cfg = cfg
        # Pre-tokenise the phrase once; an all-punctuation/empty phrase yields no words = OFF.
        self._phrase_words = [w.lower() for w in _WORD_RE.findall(cfg.phrase)]

    @classmethod
    def from_cfg(cls, cfg: dict) -> WakeWordGate:
        return cls(WakeWordConfig.from_cfg(cfg))

    @property
    def enabled(self) -> bool:
        """True only when a non-empty wake phrase is configured."""
        return bool(self._phrase_words)

    def check(self, transcript: str) -> WakeResult:
        """Gate one transcript. Disabled -> always armed, text unchanged. Enabled -> armed only
        if the wake phrase is present (fuzzy-tolerant), with the phrase stripped from the text."""
        text = transcript or ""
        if not self.enabled:
            return WakeResult(True, text.strip(), "wake word disabled")

        span = self._find(text)
        if span is None:
            return WakeResult(False, "", f"wake word '{self.cfg.phrase}' not heard")
        start, end = span
        # Excise ONLY the phrase, keeping any words on either side of it (handles a trailing or
        # embedded call sign too), then tidy the seam. Drop the separator that preceded the
        # address ("time, COVAS" -> "time", not "time,"); rejoin the two sides with one space;
        # collapse spacing and strip now-dangling leading punctuation ("COVAS, what's my fuel"
        # -> "what's my fuel").
        left = re.sub(r"[\s,;:]+$", "", text[:start])
        right = text[end:]
        cleaned = (f"{left} {right}" if left and right else (left or right))
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        cleaned = re.sub(r"^[\W_]+", "", cleaned).strip()
        return WakeResult(True, cleaned, f"wake word '{self.cfg.phrase}' matched")

    # ---- internals --------------------------------------------------------
    def _find(self, text: str) -> tuple[int, int] | None:
        """Character span of the wake phrase within ``text``, or None. Scans word tokens so the
        phrase matches as whole words; returns the char offsets of the first match so the
        caller can excise exactly the phrase from the original (case/punctuation preserved)."""
        tokens = [(m.group(0).lower(), m.start(), m.end()) for m in _WORD_RE.finditer(text)]
        w = len(self._phrase_words)
        if w == 0 or len(tokens) < w:
            return None
        for i in range(len(tokens) - w + 1):
            if self._match_at(tokens, i):
                return tokens[i][1], tokens[i + w - 1][2]
        return None

    def _match_at(self, tokens: list[tuple[str, int, int]], i: int) -> bool:
        """Do the ``w`` tokens starting at ``i`` match the phrase, word for word?"""
        for j, pw in enumerate(self._phrase_words):
            tw = tokens[i + j][0]
            if tw == pw:
                continue
            if self.cfg.fuzzy and SequenceMatcher(None, tw, pw).ratio() >= _FUZZY_THRESHOLD:
                continue
            return False
        return True
