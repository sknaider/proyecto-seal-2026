"""Tests for SessionFTS — unit tests (mock) + integration tests (real DB)."""

import asyncio
import sys
import pathlib
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.memory.session_fts import SessionFTS, FTSResult, _row_to_result

_DSN = "postgresql://seal:REDACTADO@localhost:5433/seal_memory"


# ── Mock helpers ──────────────────────────────────────────────────────────────

def _make_row(source="exchange", rid=1, agent="ADA", rank=0.5,
              headline="found <b>term</b> here", created_at=None, summary="test"):
    row = {
        "source": source,
        "id": rid,
        "agent": agent,
        "rank": rank,
        "headline": headline,
        "created_at": created_at or datetime(2026, 4, 30, tzinfo=timezone.utc),
        "summary": summary,
    }
    return row


def _make_pool(exchange_rows=None, session_rows=None):
    exchange_rows = exchange_rows or []
    session_rows  = session_rows  or []

    conn = AsyncMock()
    conn.fetch = AsyncMock(side_effect=[exchange_rows, session_rows])

    pool = MagicMock()
    ctx  = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__  = AsyncMock(return_value=False)
    pool.acquire   = MagicMock(return_value=ctx)
    pool.close     = AsyncMock()
    return pool, conn


# ── Unit tests ─────────────────────────────────────────────────────────────────

def test_empty_query_returns_empty():
    fts = SessionFTS(pool=None)
    results = asyncio.run(fts.search(""))
    assert results == []


def test_blank_query_returns_empty():
    fts = SessionFTS(pool=None)
    results = asyncio.run(fts.search("   "))
    assert results == []


def test_search_returns_fts_results():
    pool, _ = _make_pool(
        exchange_rows=[_make_row(source="exchange", rid=1, rank=0.9)],
        session_rows =[_make_row(source="session",  rid=2, rank=0.3)],
    )
    fts = SessionFTS(pool=pool)
    results = asyncio.run(fts.search("boot"))
    assert len(results) == 2
    assert results[0].rank == 0.9
    assert results[0].source == "exchange"
    assert results[1].source == "session"


def test_search_sorted_by_rank_descending():
    pool, _ = _make_pool(
        exchange_rows=[
            _make_row(rank=0.2, rid=1),
            _make_row(rank=0.8, rid=2),
        ],
        session_rows=[_make_row(source="session", rank=0.5, rid=3)],
    )
    fts = SessionFTS(pool=pool)
    results = asyncio.run(fts.search("memory"))
    assert results[0].rank == 0.8
    assert results[1].rank == 0.5
    assert results[2].rank == 0.2


def test_search_source_exchanges_only():
    pool, conn = _make_pool(
        exchange_rows=[_make_row(rid=1)],
        session_rows =[_make_row(source="session", rid=2)],
    )
    fts = SessionFTS(pool=pool)
    results = asyncio.run(fts.search("term", source="exchanges"))
    assert all(r.source == "exchange" for r in results)
    assert conn.fetch.call_count == 1


def test_search_source_sessions_only():
    pool, conn = _make_pool(
        exchange_rows=[_make_row(rid=1)],
        session_rows =[_make_row(source="session", rid=2)],
    )
    # Reverse side_effect order: only sessions will be fetched
    conn.fetch = AsyncMock(return_value=[_make_row(source="session", rid=2)])
    fts = SessionFTS(pool=pool)
    results = asyncio.run(fts.search("term", source="sessions"))
    assert all(r.source == "session" for r in results)
    assert conn.fetch.call_count == 1


def test_search_agent_filter_passed_to_query():
    pool, conn = _make_pool(exchange_rows=[], session_rows=[])
    fts = SessionFTS(pool=pool)
    asyncio.run(fts.search("boot", agent="JARVIS"))
    call_args = conn.fetch.call_args_list[0][0]
    assert "JARVIS" in call_args


def test_search_limit_respected():
    rows = [_make_row(rid=i, rank=float(i)/10) for i in range(15)]
    pool, _ = _make_pool(exchange_rows=rows, session_rows=[])
    fts = SessionFTS(pool=pool)
    results = asyncio.run(fts.search("test", limit=5))
    assert len(results) <= 5


def test_fts_result_to_dict():
    r = FTSResult(
        source="exchange", id=42, agent="ADA", rank=0.75,
        headline="found <<term>> here",
        created_at=datetime(2026, 4, 30, tzinfo=timezone.utc),
        summary="test summary",
    )
    d = r.to_dict()
    assert d["source"] == "exchange"
    assert d["id"] == 42
    assert d["rank"] == 0.75
    assert "2026-04-30" in d["created_at"]


def test_fts_result_none_datetime():
    r = FTSResult(source="session", id=1, agent="ADA", rank=0.1, headline="x",
                  created_at=None, summary=None)
    d = r.to_dict()
    assert d["created_at"] is None
    assert d["summary"] is None


def test_row_to_result_conversion():
    row = _make_row(source="exchange", rid=99, agent="NEXUS", rank=0.66,
                    headline="test <<headline>>", summary="full summary")
    result = _row_to_result(row)
    assert result.source == "exchange"
    assert result.id == 99
    assert result.agent == "NEXUS"
    assert abs(result.rank - 0.66) < 0.001
    assert result.headline == "test <<headline>>"


def test_row_to_result_none_agent():
    row = _make_row(agent=None)
    result = _row_to_result(row)
    assert result.agent == ""


# ── Integration tests (real DB) ───────────────────────────────────────────────

def _integration_available() -> bool:
    try:
        import asyncpg
        loop = asyncio.new_event_loop()
        conn = loop.run_until_complete(asyncpg.connect(_DSN, timeout=3))
        loop.run_until_complete(conn.close())
        loop.close()
        return True
    except Exception:
        return False


def test_integration_search_returns_results():
    if not _integration_available():
        print("[SKIP] DB not available")
        return

    async def run():
        fts = await SessionFTS.create(_DSN)
        results = await fts.search("memory", limit=10)
        await fts.close()
        return results

    results = asyncio.run(run())
    assert isinstance(results, list)
    print(f"  [live] 'memory' → {len(results)} results")


def test_integration_agent_filter():
    if not _integration_available():
        print("[SKIP] DB not available")
        return

    async def run():
        fts = await SessionFTS.create(_DSN)
        all_results  = await fts.search("agent", limit=20)
        ada_results  = await fts.search("agent", agent="ADA", limit=20)
        await fts.close()
        return all_results, ada_results

    all_r, ada_r = asyncio.run(run())
    if ada_r:
        assert all(r.agent == "ADA" for r in ada_r)
    print(f"  [live] all={len(all_r)} ADA={len(ada_r)}")


def test_integration_setup_indexes():
    if not _integration_available():
        print("[SKIP] DB not available")
        return

    async def run():
        fts = await SessionFTS.create(_DSN)
        await fts.setup_indexes()
        await fts.close()

    asyncio.run(run())
    print("  [live] GIN indexes created/verified OK")


def main() -> int:
    tests = [
        test_empty_query_returns_empty,
        test_blank_query_returns_empty,
        test_search_returns_fts_results,
        test_search_sorted_by_rank_descending,
        test_search_source_exchanges_only,
        test_search_source_sessions_only,
        test_search_agent_filter_passed_to_query,
        test_search_limit_respected,
        test_fts_result_to_dict,
        test_fts_result_none_datetime,
        test_row_to_result_conversion,
        test_row_to_result_none_agent,
        test_integration_search_returns_results,
        test_integration_agent_filter,
        test_integration_setup_indexes,
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
