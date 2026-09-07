"""Tests for Unicode surrogate sanitization in agent.py (fix for Anthropic HTTP 400)."""
import json
import pytest
from companion_core.agent import _sanitize, _clean_messages


# ── _sanitize ────────────────────────────────────────────────────────────────

def test_sanitize_clean_string_unchanged():
    assert _sanitize("hello world") == "hello world"


def test_sanitize_removes_lone_high_surrogate():
    text = "before\ud800after"
    result = _sanitize(text)
    assert "\ud800" not in result
    assert "before" in result
    assert "after" in result


def test_sanitize_removes_lone_low_surrogate():
    text = "before\udc00after"
    result = _sanitize(text)
    assert "\udc00" not in result


def test_sanitize_replaces_with_replacement_char():
    text = "x\ud800y"
    result = _sanitize(text)
    assert result == "x" + "�" + "y"


def test_sanitize_multiple_surrogates():
    text = "\ud800hello\udfff"
    result = _sanitize(text)
    assert "\ud800" not in result
    assert "\udfff" not in result
    assert "hello" in result


def test_sanitize_only_surrogates_string():
    text = "𐀀􏿿"
    result = _sanitize(text)
    assert all(0xD800 <= ord(c) <= 0xDFFF for c in result) is False


def test_sanitize_empty_string():
    assert _sanitize("") == ""


def test_sanitize_unicode_non_surrogate_preserved():
    text = "café ñ 中文 🚀"
    assert _sanitize(text) == text


def test_sanitize_result_is_valid_json():
    text = "data\ud800here"
    result = _sanitize(text)
    serialized = json.dumps({"content": result})
    parsed = json.loads(serialized)
    assert "data" in parsed["content"]
    assert "here" in parsed["content"]


# ── _clean_messages ──────────────────────────────────────────────────────────

def test_clean_messages_sanitizes_content():
    msgs = [{"role": "user", "content": "hello\ud800world"}]
    result = _clean_messages(msgs)
    assert "\ud800" not in result[0]["content"]
    assert "hello" in result[0]["content"]


def test_clean_messages_preserves_role():
    msgs = [{"role": "assistant", "content": "fine text"}]
    result = _clean_messages(msgs)
    assert result[0]["role"] == "assistant"


def test_clean_messages_non_string_content_unchanged():
    tool_result = [{"type": "tool_result", "tool_use_id": "x", "content": "ok"}]
    msgs = [{"role": "user", "content": tool_result}]
    result = _clean_messages(msgs)
    assert result[0]["content"] == tool_result


def test_clean_messages_empty_list():
    assert _clean_messages([]) == []


def test_clean_messages_multiple_turns():
    msgs = [
        {"role": "user", "content": "q\ud800?"},
        {"role": "assistant", "content": "a\udc00!"},
    ]
    result = _clean_messages(msgs)
    assert "\ud800" not in result[0]["content"]
    assert "\udc00" not in result[1]["content"]


def test_clean_messages_result_json_serializable():
    msgs = [{"role": "user", "content": "bad\ud835surrogate"}]
    result = _clean_messages(msgs)
    serialized = json.dumps(result)
    parsed = json.loads(serialized)
    assert parsed[0]["role"] == "user"
