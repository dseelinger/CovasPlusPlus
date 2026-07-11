"""Unit tests for the general copy-to-clipboard capability (N11; offline, DESIGN §9).

The tool is LLM-native — the model resolves "that" and passes exact text — so what's testable
here is the plumbing: the text routes to the injected clipboard verbatim, the confirmation
names exactly what was copied, a clipboard failure is spoken (never raised), and the help
metadata satisfies the registry contract. The real `nav/clipboard.py` copy() is never called.
"""
from __future__ import annotations

from covas.capabilities.base import CapabilityRegistry, help_meta_problems
from covas.capabilities.clipboard_capability import ClipboardCapability

_TOOL = "copy_to_clipboard"


def _cap(clipboard=None, log=None):
    copied: list[str] = []
    cap = ClipboardCapability(clipboard=clipboard or copied.append, log=log)
    return cap, copied


# --- the copy ------------------------------------------------------------------------------

def test_text_routes_to_the_clipboard_verbatim_and_confirms():
    cap, copied = _cap()
    out = cap.run_tool(_TOOL, {"text": "Khun"})
    assert copied == ["Khun"]
    assert out == "Copied Khun to your clipboard."


def test_label_flavors_the_confirmation():
    cap, copied = _cap()
    out = cap.run_tool(_TOOL, {"text": "Wolf 397", "label": "the system"})
    assert copied == ["Wolf 397"]
    assert out == "Copied the system Wolf 397 to your clipboard."


def test_current_system_is_still_copied():
    # EXPLICIT request: the search tools' skip-when-already-there rule must NOT apply — the
    # capability has no notion of the current system at all, so the copy always happens.
    cap, copied = _cap()
    cap.run_tool(_TOOL, {"text": "Wolf 397"})       # ...even if the Commander is in Wolf 397
    assert copied == ["Wolf 397"]


def test_whitespace_is_trimmed():
    cap, copied = _cap()
    out = cap.run_tool(_TOOL, {"text": "  Elvira Martuuk  "})
    assert copied == ["Elvira Martuuk"] and "Elvira Martuuk" in out


def test_empty_text_asks_and_copies_nothing():
    cap, copied = _cap()
    out = cap.run_tool(_TOOL, {})
    assert copied == [] and "copy" in out.lower()


# --- fail soft -----------------------------------------------------------------------------

def test_clipboard_failure_is_spoken_not_raised():
    def boom(text):
        raise OSError("no clipboard here")

    logged: list[str] = []
    cap = ClipboardCapability(clipboard=boom, log=logged.append)
    out = cap.run_tool(_TOOL, {"text": "Khun"})
    assert "couldn't" in out.lower() and "Khun" in out   # the value is still spoken
    assert any("failed" in m for m in logged)


def test_unknown_tool_is_soft():
    cap, _ = _cap()
    assert "Unknown tool" in cap.run_tool("copy_everything", {"text": "x"})


# --- registry contract ---------------------------------------------------------------------

def test_help_metadata_is_complete_and_registers():
    cap, _ = _cap()
    assert help_meta_problems(cap.help_meta()) == []
    reg = CapabilityRegistry()
    reg.register(cap)                                    # would raise on incomplete metadata
    assert "clipboard" in reg.categories()
    assert reg.run_tool(_TOOL, {"text": "Khun"}).startswith("Copied Khun")
