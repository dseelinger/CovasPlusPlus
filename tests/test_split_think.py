"""Unit tests for ollama_llm._split_think — the pure <think>-tag splitter.

Pure logic, no Ollama server or hardware needed. Covers the plain cases, the
two ways a tag can straddle a streamed-chunk boundary (in-think state carried by
('state', ...) events; a tag token split mid-way carried by ('pending', ...)),
plus empty/nested/adjacent edge cases.
"""
from __future__ import annotations

from covas.providers.ollama_llm import _partial_tag_len, _split_think


def _drive(chunks: list[str]) -> tuple[str, str, str]:
    """Feed `chunks` through _split_think exactly as OllamaLLM.stream_reply does,
    threading in_think + pending across chunks. Return (spoken, thinking, leftover).
    `leftover` is any still-pending fragment at the end (would be flushed on done)."""
    in_think = False
    pending = ""
    spoken: list[str] = []
    thinking: list[str] = []
    for chunk in chunks:
        combined, pending = pending + chunk, ""
        for kind, piece in _split_think(combined, in_think):
            if kind == "state":
                in_think = piece
            elif kind == "pending":
                pending = piece
            elif kind == "thinking":
                thinking.append(piece)
            else:
                spoken.append(piece)
    return "".join(spoken), "".join(thinking), pending


def _speak(chunks: list[str]) -> str:
    """Spoken text after the end-of-stream flush (pending released as text)."""
    spoken, _thinking, leftover = _drive(chunks)
    return spoken + leftover  # not in_think at end in these cases -> flush to text


# --- no tags ---------------------------------------------------------------

def test_plain_text_passthrough():
    assert _drive(["hello there"]) == ("hello there", "", "")


def test_empty_string():
    assert _drive([""]) == ("", "", "")


# --- single block in one chunk --------------------------------------------

def test_think_block_stripped_from_speech():
    spoken, thinking, pend = _drive(["before<think>secret</think>after"])
    assert spoken == "beforeafter"
    assert thinking == "secret"
    assert pend == ""


def test_leading_think_block():
    assert _drive(["<think>plan</think>answer"]) == ("answer", "plan", "")


def test_trailing_open_think_consumes_rest():
    # Unclosed tag in a single (final) chunk: everything after <think> is reasoning.
    spoken, thinking, pend = _drive(["talk<think>still thinking"])
    assert spoken == "talk"
    assert thinking == "still thinking"
    assert pend == ""


def test_empty_think_block():
    assert _drive(["a<think></think>b"]) == ("ab", "", "")


def test_multiple_think_blocks():
    spoken, thinking, _ = _drive(["a<think>x</think>b<think>y</think>c"])
    assert spoken == "abc"
    assert thinking == "xy"


# --- state carried across chunks (the common case) -------------------------

def test_open_and_close_span_chunks():
    # <think> opens in chunk 1, closes in chunk 3 — in_think must carry over.
    spoken, thinking, _ = _drive(["say <think>rea", "soning ", "here</think> done"])
    assert spoken == "say  done"
    assert thinking == "reasoning here"


def test_text_split_plainly_across_chunks():
    assert _drive(["hel", "lo"]) == ("hello", "", "")


# --- the tag TOKEN itself split across a chunk boundary --------------------

def test_open_tag_split_across_chunks():
    # "<think>" is torn as "<thi" | "nk>" — must not leak "<thi" into speech.
    spoken, thinking, _ = _drive(["ok <thi", "nk>hidden</think> visible"])
    assert "<thi" not in spoken
    assert spoken == "ok  visible"
    assert thinking == "hidden"


def test_close_tag_split_across_chunks():
    spoken, thinking, _ = _drive(["<think>reason</thi", "nk>shown"])
    assert spoken == "shown"
    assert thinking == "reason"
    assert "</thi" not in thinking


def test_tag_split_one_char_at_a_time():
    # Pathological: every character arrives in its own chunk.
    spoken, thinking, _ = _drive(list("hi<think>z</think>yo"))
    assert spoken == "hiyo"
    assert thinking == "z"


def test_lone_lt_that_is_not_a_tag_is_released():
    # A dangling '<' is held, then turns out to be ordinary text -> spoken.
    assert _speak(["a <", "b c"]) == "a <b c"


def test_dangling_partial_tag_flushed_at_end():
    # Stream ends on a partial tag that never completed -> it must still be spoken.
    spoken, thinking, leftover = _drive(["hello <thi"])
    assert spoken == "hello "
    assert leftover == "<thi"
    assert thinking == ""


# --- _partial_tag_len helper ----------------------------------------------

def test_partial_tag_len_detects_prefixes():
    assert _partial_tag_len("foo <thi", "<think>") == 4      # "<thi"
    assert _partial_tag_len("foo <", "<think>") == 1         # "<"
    assert _partial_tag_len("plain text", "<think>") == 0    # nothing dangling


def test_partial_tag_len_ignores_full_tag():
    # A full match is not a *proper* prefix, so it isn't reported as partial.
    assert _partial_tag_len("x<think>", "<think>") == 0


def test_partial_tag_len_whole_string_is_partial():
    assert _partial_tag_len("<thi", "<think>") == 4
