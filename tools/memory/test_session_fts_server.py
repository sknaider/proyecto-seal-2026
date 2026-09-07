"""Tests for SessionFTSServer — HTTP session search endpoints."""

import asyncio
import json
import sys
import pathlib
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.memory.session_fts_server import SessionFTSServer
from tools.memory.session_fts import FTSResult


# ── Helpers ───────────────────────────────────────────────────────────────────

class FakeWriter:
    def __init__(self):
        self.data = b""
        self.closed = False

    def write(self, data: bytes):
        self.data += data

    async def wait_closed(self):
        self.closed = True

    def close(self):
        self.closed = True

    def response_body(self) -> bytes:
        """Return body after HTTP headers."""
        if b"\r\n\r\n" in self.data:
            return self.data.split(b"\r\n\r\n", 1)[1]
        return self.data

    def response_headers(self) -> str:
        return self.data.split(b"\r\n\r\n")[0].decode("utf-8", errors="replace")

    def status_code(self) -> int:
        line = self.data.split(b"\r\n")[0].decode()
        return int(line.split()[1])


def _make_reader(request_line: str) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(request_line.encode())
    return reader


def _make_fts_result(source="exchange", rid=1, agent="ADA", rank=0.5,
                     headline="found <<term>>", summary="s") -> FTSResult:
    return FTSResult(
        source=source, id=rid, agent=agent, rank=rank, headline=headline,
        created_at=datetime(2026, 4, 30, tzinfo=timezone.utc), summary=summary,
    )


def _server_with_mock_fts(results=None):
    srv = SessionFTSServer.__new__(SessionFTSServer)
    srv._host = "0.0.0.0"
    srv._port = 8770
    srv._dsn  = "mock"
    srv._srv  = None

    mock_fts = AsyncMock()
    mock_fts.search = AsyncMock(return_value=results or [])
    srv._fts = mock_fts
    return srv, mock_fts


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── Tests ──────────────────────────────────────────────────────────────────────

def test_health_returns_ok():
    srv, _ = _server_with_mock_fts()
    writer = FakeWriter()
    reader = _make_reader("GET /api/health HTTP/1.1\r\nHost: localhost\r\n\r\n")
    run(srv._handle(reader, writer))
    assert writer.status_code() == 200
    data = json.loads(writer.response_body())
    assert data["status"] == "ok"
    assert data["ready"] is True


def test_root_serves_html():
    srv, _ = _server_with_mock_fts()
    writer = FakeWriter()
    reader = _make_reader("GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
    run(srv._handle(reader, writer))
    assert writer.status_code() == 200
    assert b"SEAL Session Search" in writer.response_body()
    assert b"<form" in writer.response_body()


def test_search_no_query_returns_empty():
    srv, mock_fts = _server_with_mock_fts()
    writer = FakeWriter()
    reader = _make_reader("GET /api/search?q= HTTP/1.1\r\nHost: localhost\r\n\r\n")
    run(srv._handle(reader, writer))
    assert writer.status_code() == 200
    data = json.loads(writer.response_body())
    assert data["results"] == []
    mock_fts.search.assert_not_called()


def test_search_returns_results():
    results = [_make_fts_result(rid=1), _make_fts_result(rid=2)]
    srv, mock_fts = _server_with_mock_fts(results=results)
    writer = FakeWriter()
    reader = _make_reader("GET /api/search?q=memory HTTP/1.1\r\nHost: localhost\r\n\r\n")
    run(srv._handle(reader, writer))
    assert writer.status_code() == 200
    data = json.loads(writer.response_body())
    assert data["count"] == 2
    assert len(data["results"]) == 2
    assert data["query"] == "memory"


def test_search_agent_filter_passed():
    srv, mock_fts = _server_with_mock_fts()
    writer = FakeWriter()
    reader = _make_reader("GET /api/search?q=boot&agent=JARVIS HTTP/1.1\r\nHost: localhost\r\n\r\n")
    run(srv._handle(reader, writer))
    mock_fts.search.assert_called_once()
    _, kwargs = mock_fts.search.call_args
    assert kwargs.get("agent") == "JARVIS"


def test_search_source_filter_passed():
    srv, mock_fts = _server_with_mock_fts()
    writer = FakeWriter()
    reader = _make_reader("GET /api/search?q=test&source=sessions HTTP/1.1\r\nHost: localhost\r\n\r\n")
    run(srv._handle(reader, writer))
    _, kwargs = mock_fts.search.call_args
    assert kwargs.get("source") == "sessions"


def test_search_limit_defaults_to_20():
    srv, mock_fts = _server_with_mock_fts()
    writer = FakeWriter()
    reader = _make_reader("GET /api/search?q=test HTTP/1.1\r\nHost: localhost\r\n\r\n")
    run(srv._handle(reader, writer))
    _, kwargs = mock_fts.search.call_args
    assert kwargs.get("limit") == 20


def test_search_limit_capped_at_100():
    srv, mock_fts = _server_with_mock_fts()
    writer = FakeWriter()
    reader = _make_reader("GET /api/search?q=test&limit=999 HTTP/1.1\r\nHost: localhost\r\n\r\n")
    run(srv._handle(reader, writer))
    _, kwargs = mock_fts.search.call_args
    assert kwargs.get("limit") == 100


def test_404_for_unknown_route():
    srv, _ = _server_with_mock_fts()
    writer = FakeWriter()
    reader = _make_reader("GET /unknown HTTP/1.1\r\nHost: localhost\r\n\r\n")
    run(srv._handle(reader, writer))
    assert writer.status_code() == 404


def test_405_for_post():
    srv, _ = _server_with_mock_fts()
    writer = FakeWriter()
    reader = _make_reader("POST /api/search HTTP/1.1\r\nHost: localhost\r\n\r\n")
    run(srv._handle(reader, writer))
    assert writer.status_code() == 405


def test_cors_header_present():
    srv, _ = _server_with_mock_fts()
    writer = FakeWriter()
    reader = _make_reader("GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
    run(srv._handle(reader, writer))
    assert "Access-Control-Allow-Origin: *" in writer.response_headers()


def test_search_result_has_required_fields():
    results = [_make_fts_result(rid=42, agent="NEXUS", rank=0.9, headline="h <<x>>")]
    srv, _ = _server_with_mock_fts(results=results)
    writer = FakeWriter()
    reader = _make_reader("GET /api/search?q=test HTTP/1.1\r\nHost: localhost\r\n\r\n")
    run(srv._handle(reader, writer))
    data = json.loads(writer.response_body())
    r = data["results"][0]
    assert r["id"] == 42
    assert r["agent"] == "NEXUS"
    assert r["rank"] == 0.9
    assert r["headline"] == "h <<x>>"
    assert r["source"] == "exchange"


def test_empty_reader_handled_gracefully():
    srv, _ = _server_with_mock_fts()
    reader = asyncio.StreamReader()
    reader.feed_eof()
    writer = FakeWriter()
    run(srv._handle(reader, writer))
    # Should not raise — empty request just closes cleanly


def main() -> int:
    tests = [
        test_health_returns_ok,
        test_root_serves_html,
        test_search_no_query_returns_empty,
        test_search_returns_results,
        test_search_agent_filter_passed,
        test_search_source_filter_passed,
        test_search_limit_defaults_to_20,
        test_search_limit_capped_at_100,
        test_404_for_unknown_route,
        test_405_for_post,
        test_cors_header_present,
        test_search_result_has_required_fields,
        test_empty_reader_handled_gracefully,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"[OK] {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"[FAIL] {t.__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    raise SystemExit(main())
