import asyncio
import importlib.util
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "sandbox-agent"
    / "seal_memory_anomaly_monitor.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("seal_memory_anomaly_monitor_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeConnection:
    def __init__(self, columns=(), audit_log_exists=False):
        self.columns = columns
        self.audit_log_exists = audit_log_exists
        self.fetch_queries = []
        self.fetchval_queries = []
        self.fetchrow_queries = []

    async def fetch(self, query, *args):
        self.fetch_queries.append((query, args))
        if "information_schema.columns" in query:
            return [{"column_name": column} for column in self.columns]
        return []

    async def fetchrow(self, query, *args):
        self.fetchrow_queries.append((query, args))
        return {"present": 1} if self.audit_log_exists else None

    async def fetchval(self, query, *args):
        self.fetchval_queries.append((query, args))
        return set(self.columns) == set(_load_module()._DRIFT_REQUIRED_COLUMNS)


def test_missing_drift_columns_are_detected_once_without_failing_queries():
    monitor = _load_module()
    conn = FakeConnection(columns=())
    messages = []
    monitor.log = messages.append

    async def exercise():
        await monitor.scan_drift_revisions(conn)
        await monitor.scan_drift_semantic(conn)
        await monitor.scan_drift_revisions(conn)

    asyncio.run(exercise())

    assert len(conn.fetchval_queries) == 1
    assert "soul_v3.memory_monitor_capabilities_boundary" in conn.fetchval_queries[0][0]
    assert not any("SELECT id, agent, revision_count" in query for query, _ in conn.fetch_queries)
    assert len(messages) == 1
    assert "revision_count" in messages[0]


def test_drift_queries_run_when_schema_capability_is_present():
    monitor = _load_module()
    conn = FakeConnection(columns=monitor._DRIFT_REQUIRED_COLUMNS)

    async def exercise():
        await monitor.scan_drift_revisions(conn)
        await monitor.scan_drift_semantic(conn)

    asyncio.run(exercise())

    assert len(conn.fetchval_queries) == 1
    assert len(conn.fetch_queries) == 2
    assert sum("SELECT id, agent, revision_count" in query for query, _ in conn.fetch_queries) == 2


def test_audit_table_probe_is_schema_scoped_and_returns_boolean():
    monitor = _load_module()
    monitor.log = lambda _message: None
    conn = FakeConnection(audit_log_exists=False)

    result = asyncio.run(monitor._check_audit_log_exists(conn))

    assert result is False
    assert len(conn.fetchrow_queries) == 1
    assert "table_schema='soul_v3'" in conn.fetchrow_queries[0][0]


def test_capability_class_contract_is_complete_versioned_and_copy_safe():
    monitor = _load_module()

    first = monitor._capabilities_class_payload()
    second = monitor._capabilities_class_payload()

    assert monitor.CAPABILITIES_CLASS_SCHEMA == (
        "seal.memory-anomaly-capabilities-class.v1"
    )
    assert first == {
        "burst_hash_audit": "optional",
        "revision_drift": "future",
        "signature_integrity": "required",
    }
    assert set(first.values()) <= {"required", "optional", "future"}
    assert first is not second
    first["signature_integrity"] = "future"
    assert second["signature_integrity"] == "required"


def test_invalid_capability_class_fails_closed(monkeypatch):
    monitor = _load_module()
    monkeypatch.setattr(
        monitor,
        "CAPABILITIES_CLASS",
        {"signature_integrity": "unknown"},
    )

    try:
        monitor._capabilities_class_payload()
    except RuntimeError as exc:
        assert "invalid memory monitor capability classification" in str(exc)
    else:
        raise AssertionError("invalid capability class must fail closed")
