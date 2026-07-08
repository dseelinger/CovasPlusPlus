"""Unit tests for ollama_llm._model_available — the ping model-availability check.

Pure logic. Guards against the loose substring match that reported a model as
'ready' when Ollama's /api/chat would 404 on it (e.g. config 'qwen3' vs installed
'qwen3.6:latest'). Mirrors Ollama's real resolution: exact tag, or a bare name
resolving to '<name>:latest'.
"""
from __future__ import annotations

from covas.providers.ollama_llm import _model_available


def test_exact_tag_matches():
    assert _model_available("qwen3:8b", ["qwen3:8b", "gpt-oss:20b"])


def test_bare_name_resolves_to_latest():
    assert _model_available("qwen3", ["qwen3:latest"])
    assert _model_available("llama3.1", ["llama3.1:latest", "qwen3:latest"])


def test_bare_name_without_latest_installed_is_unavailable():
    # Ollama does NOT resolve a bare name to an arbitrary tag like ':8b'.
    assert not _model_available("qwen3", ["qwen3:8b"])


def test_substring_is_not_a_match():
    # The exact bug that caused the confusing /api/chat 404.
    assert not _model_available("qwen3", ["qwen3.6:latest"])
    assert not _model_available("qwen", ["qwen3:latest"])


def test_missing_model_is_unavailable():
    assert not _model_available("mistral", ["qwen3:latest", "gpt-oss:20b"])


def test_no_models_installed():
    assert not _model_available("qwen3", [])


def test_configured_tag_must_match_exactly():
    # If the user pins a tag, only that exact tag counts.
    assert _model_available("qwen3:14b", ["qwen3:14b"])
    assert not _model_available("qwen3:14b", ["qwen3:latest"])
