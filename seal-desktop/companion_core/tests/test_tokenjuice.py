"""Tests for TokenJuice integration in companion_core agent."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from companion_core.agent import _tokenjuice_compact, _COMPACT_THRESHOLD


@pytest.mark.asyncio
async def test_compact_below_threshold_skips_http():
    """Short text must bypass HTTP call entirely."""
    short = "x" * (_COMPACT_THRESHOLD - 1)
    with patch("httpx.AsyncClient") as mock_client:
        result = await _tokenjuice_compact(short)
    mock_client.assert_not_called()
    assert result == short


@pytest.mark.asyncio
async def test_compact_above_threshold_calls_api():
    """Long text must call :8800 and return compressed result."""
    long_text = "a" * _COMPACT_THRESHOLD
    compressed = "a" * 200

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"text": compressed}

    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_http):
        result = await _tokenjuice_compact(long_text, tool_name="bash")

    mock_http.post.assert_called_once()
    call_kwargs = mock_http.post.call_args
    assert "tokenjuice/compact" in call_kwargs[0][0]
    assert result == compressed


@pytest.mark.asyncio
async def test_compact_falls_back_on_http_error():
    """If :8800 is down, return original text without raising."""
    long_text = "b" * _COMPACT_THRESHOLD

    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.post = AsyncMock(side_effect=Exception("connection refused"))

    with patch("httpx.AsyncClient", return_value=mock_http):
        result = await _tokenjuice_compact(long_text)

    assert result == long_text


@pytest.mark.asyncio
async def test_compact_falls_back_on_bad_status():
    """Non-200 response → return original text."""
    long_text = "c" * _COMPACT_THRESHOLD

    mock_response = MagicMock()
    mock_response.status_code = 500

    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_http):
        result = await _tokenjuice_compact(long_text)

    assert result == long_text


@pytest.mark.asyncio
async def test_get_reply_compresses_long_history(monkeypatch):
    """get_reply must compress history entries >= threshold before calling Claude."""
    from companion_core.agent import get_reply

    long_content = "d" * _COMPACT_THRESHOLD
    compressed_content = "d" * 100

    compact_calls = []

    async def mock_compact(text, tool_name="bash"):
        compact_calls.append(text)
        return compressed_content if len(text) >= _COMPACT_THRESHOLD else text

    monkeypatch.setattr("companion_core.agent._tokenjuice_compact", mock_compact)

    captured_messages = []

    async def mock_call_claude(messages, api_key, model, system):
        captured_messages.extend(messages)
        return "ok"

    monkeypatch.setattr("companion_core.agent.call_claude", mock_call_claude)

    history = [{"role": "user", "content": long_content},
               {"role": "assistant", "content": "short reply"}]

    await get_reply(history, "new question", api_key="test-key", model=None)

    assert len(compact_calls) >= 1
    # The long history entry must have been compressed
    compressed_msgs = [m for m in captured_messages if m.get("content") == compressed_content]
    assert len(compressed_msgs) >= 1
