from __future__ import annotations

import inspect
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest

import db
import gam
import reflective_diagnoses as diagnoses
import tool_broker as tb


def test_mcp_runtime_credential_wins_over_legacy_db_url(tmp_path: Path) -> None:
    cred = tmp_path / "runtime.cred"
    cred.write_text(
        "postgresql://mcp_runtime:not-a-real-secret@localhost:5433/seal_memory",
        encoding="utf-8",
    )
    cred.chmod(0o600)
    selected = db.resolve_db_url(
        {
            "SEAL_MCP_RUNTIME_DB": "1",
            "SEAL_MCP_RUNTIME_CRED": str(cred),
            "SEAL_DB_URL": "postgresql://seal:legacy@localhost:5433/seal_memory",
        }
    )
    assert urlsplit(selected).username == "mcp_runtime"


def test_mcp_runtime_missing_credential_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="refusing privileged fallback"):
        db.resolve_db_url(
            {
                "SEAL_MCP_RUNTIME_DB": "1",
                "SEAL_MCP_RUNTIME_CRED": str(tmp_path / "missing.cred"),
                "SEAL_DB_URL": "postgresql://seal:legacy@localhost:5433/seal_memory",
            }
        )


def test_mcp_runtime_rejects_privileged_runtime_url() -> None:
    with pytest.raises(RuntimeError, match="privileged login"):
        db.resolve_db_url(
            {
                "SEAL_MCP_RUNTIME_DB": "1",
                "SEAL_MCP_RUNTIME_DB_URL": (
                    "postgresql://seal:legacy@localhost:5433/seal_memory"
                ),
            }
        )


@pytest.mark.parametrize(
    ("module_name", "shared_import"),
    [
        ("tool_broker.py", "from db import DB_URL"),
        ("memory_indexer.py", "from db import get_pool"),
        ("seal_bench_v2.py", "from db import get_pool"),
        ("emotional_continuity.py", "from db import get_pool"),
        ("agent_tools_registry.py", "from db import get_pool"),
        ("latent_graphmem_serve.py", "from db import get_pool"),
    ],
)
def test_mcp_reachable_helpers_share_db_principal(
    module_name: str, shared_import: str
) -> None:
    source = (Path(__file__).parent / module_name).read_text(encoding="utf-8")
    assert "from seal_secrets import pg_dsn" not in source
    assert shared_import in source


def test_mcp_agent_credentials_are_private_and_identity_bound(tmp_path: Path) -> None:
    cred_dir = tmp_path / "agents"
    cred_dir.mkdir()
    ada = cred_dir / "ada.dsn"
    ada.write_text(
        "postgresql://mcp_runtime_ada:not-a-real-secret@localhost:5433/seal_memory",
        encoding="utf-8",
    )
    ada.chmod(0o600)
    selected = db.resolve_mcp_agent_db_url(
        "ADA", {"SEAL_MCP_AGENT_CRED_DIR": str(cred_dir)}
    )
    assert urlsplit(selected).username == "mcp_runtime_ada"


def test_mcp_agent_credential_rejects_wrong_login(tmp_path: Path) -> None:
    cred_dir = tmp_path / "agents"
    cred_dir.mkdir()
    ada = cred_dir / "ada.dsn"
    ada.write_text(
        "postgresql://mcp_runtime_nexus:not-a-real-secret@localhost:5433/seal_memory",
        encoding="utf-8",
    )
    ada.chmod(0o600)
    with pytest.raises(RuntimeError, match="principal mismatch"):
        db.resolve_mcp_agent_db_url(
            "ADA", {"SEAL_MCP_AGENT_CRED_DIR": str(cred_dir)}
        )


def test_mcp_agent_credential_rejects_world_readable_file(tmp_path: Path) -> None:
    cred_dir = tmp_path / "agents"
    cred_dir.mkdir()
    ada = cred_dir / "ada.dsn"
    ada.write_text(
        "postgresql://mcp_runtime_ada:not-a-real-secret@localhost:5433/seal_memory",
        encoding="utf-8",
    )
    ada.chmod(0o644)
    with pytest.raises(RuntimeError, match="not private"):
        db.resolve_mcp_agent_db_url(
            "ADA", {"SEAL_MCP_AGENT_CRED_DIR": str(cred_dir)}
        )


def test_emotional_continuity_has_no_independent_connection_escape() -> None:
    source = (Path(__file__).parent / "emotional_continuity.py").read_text(
        encoding="utf-8"
    )
    assert "asyncpg.connect(" not in source
    assert "async with pool.acquire()" in source


def test_mcp_rebinds_shared_db_facade_to_caller_scoped_pool() -> None:
    source = (Path(__file__).parent / "mcp_server_v4.py").read_text(encoding="utf-8")
    assert "_mcp_db_module.get_pool = _mcp_get_pool" in source
    assert "_mcp_db_module.close_pool = _mcp_close_pool" in source


@pytest.mark.parametrize("mode", ["observe", "migrate", "enforce"])
def test_scoped_index_repo_is_allowed_by_broker(monkeypatch, mode: str) -> None:
    monkeypatch.setenv("SEAL_TOOL_BROKER_MODE", mode)
    decision = tb.policy_decision(
        agent="ADA",
        tool="index_repo",
        args={"path": "/home/dadito/IA/proyecto-seal", "name": "soul"},
        scope_allowed=True,
    )
    assert decision.allow is True
    assert decision.rule_id.endswith("CAPABILITY_ALLOW")


def test_allowed_roots_accepts_real_descendant(tmp_path: Path) -> None:
    root = tmp_path / "allowed"
    child = root / "repo"
    child.mkdir(parents=True)
    ok, reason = tb.validate_scope_constraints(
        "index_repo", {"path": str(child)}, {"allowed_roots": [str(root)]}
    )
    assert ok is True, reason


def test_allowed_roots_rejects_outside_and_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "allowed"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    escape = root / "escape"
    escape.symlink_to(outside, target_is_directory=True)

    outside_ok, _ = tb.validate_scope_constraints(
        "index_repo", {"path": str(outside)}, {"allowed_roots": [str(root)]}
    )
    symlink_ok, _ = tb.validate_scope_constraints(
        "index_repo", {"path": str(escape)}, {"allowed_roots": [str(root)]}
    )
    assert outside_ok is False
    assert symlink_ok is False


@pytest.mark.asyncio
async def test_indexer_rejects_file_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    outside = tmp_path / "outside.py"
    root.mkdir()
    outside.write_text("print('outside')\n", encoding="utf-8")
    (root / "escape.py").symlink_to(outside)
    kernel_root = Path(__file__).parents[1] / "sandbox-agent" / "NEXUS"
    sys.path.insert(0, str(kernel_root))
    try:
        from kernel.code_graph.indexer import index_file

        with pytest.raises(PermissionError, match="escapes repository root"):
            await index_file(None, 1, "escape.py", str(root))
    finally:
        sys.path.remove(str(kernel_root))


class _ScopeConn:
    def __init__(self, root: Path):
        self.root = root

    async def fetchrow(self, query, *args):
        if "FROM soul_v3.capability_scope" in query:
            return {"allowed": True, "constraints": {"allowed_roots": [str(self.root)]}}
        if "FROM soul_v3.capability_grants" in query:
            return None
        raise AssertionError(query)


@pytest.mark.asyncio
async def test_scope_check_actually_enforces_allowed_roots(tmp_path: Path) -> None:
    root = tmp_path / "allowed"
    inside = root / "repo"
    outside = tmp_path / "outside"
    inside.mkdir(parents=True)
    outside.mkdir()
    conn = _ScopeConn(root)

    allowed, _ = await tb._scope_check(
        conn, "ADA", "index_repo", "index_repo", {"path": str(inside)}
    )
    denied, reason = await tb._scope_check(
        conn, "ADA", "index_repo", "index_repo", {"path": str(outside)}
    )
    assert allowed is True
    assert denied is False
    assert "outside allowed_roots" in reason


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Acquire(self.conn)


class _GamConn:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    async def fetchval(self, query, *args):
        self.calls.append((query, args))
        return False

    async def fetchrow(self, query, *args):
        self.calls.append((query, args))
        return None

    async def fetch(self, query, *args):
        self.calls.append((query, args))
        return []

    async def execute(self, query, *args):
        self.calls.append((query, args))
        return "UPDATE 0"


class _GamForeignParentConn(_GamConn):
    async def fetchval(self, query, *args):
        self.calls.append((query, args))
        if "FROM soul_v3.gam_topics" in query:
            return True
        if "FROM soul_v3.gam_event_graph" in query:
            return 0
        raise AssertionError(query)


@pytest.mark.asyncio
async def test_gam_foreign_ids_fail_closed(monkeypatch) -> None:
    conn = _GamConn()

    async def get_pool():
        return _Pool(conn)

    monkeypatch.setattr(gam, "get_pool", get_pool)

    with pytest.raises(PermissionError, match="not owned by ADA"):
        await gam.add_action("ADA", 9001, "foreign")
    with pytest.raises(PermissionError, match="not owned by ADA"):
        await gam.complete_action("ADA", 9002)
    with pytest.raises(PermissionError, match="not owned by ADA"):
        await gam.close_goal("ADA", 9003)


@pytest.mark.asyncio
async def test_gam_reads_include_owner_predicate(monkeypatch) -> None:
    conn = _GamConn()

    async def get_pool():
        return _Pool(conn)

    monkeypatch.setattr(gam, "get_pool", get_pool)
    assert await gam.get_actions("ADA", 7) == []
    query, args = conn.calls[-1]
    assert "topic_id = $1 AND agent = $2" in query
    assert args == (7, "ADA")


@pytest.mark.asyncio
async def test_gam_rejects_foreign_parent_action_ids(monkeypatch) -> None:
    conn = _GamForeignParentConn()

    async def get_pool():
        return _Pool(conn)

    monkeypatch.setattr(gam, "get_pool", get_pool)
    with pytest.raises(PermissionError, match="parent actions"):
        await gam.add_action("ADA", 7, "child", parent_action_ids=[99])


class _DiagnosisConn(_GamConn):
    pass


@pytest.mark.asyncio
async def test_diagnosis_ids_are_owner_scoped(monkeypatch) -> None:
    conn = _DiagnosisConn()

    async def get_pool():
        return _Pool(conn)

    monkeypatch.setattr(diagnoses, "get_pool", get_pool)
    assert await diagnoses.get_diagnosis("ADA", 41) is None
    query, args = conn.calls[-1]
    assert "WHERE id = $1 AND agent = $2" in query
    assert args == (41, "ADA")

    with pytest.raises(PermissionError, match="not owned by ADA"):
        await diagnoses.update_diagnosis_status("ADA", 41, "accepted")


def test_protected_router_keeps_owner_scoping_and_blocks_global_snapshot() -> None:
    source = (Path(__file__).parent / "mcp_server_v4.py").read_text(encoding="utf-8")
    assert 'WHERE id=$1 AND agent=$2", task_id, caller' in source
    assert "complete_action(caller, action_id, result)" in source
    assert "get_actions(caller, topic_id, status_filter or None)" in source
    assert "close_goal(caller, topic_id)" in source
    assert "snapshot_all is disabled on the per-agent MCP route" in source
    assert "/tmp/cgraph_index_run.py" not in source
    assert "index_source_with_connection" in source
    assert "async with pool.acquire() as conn" in source
    assert "full_reindex is operator-only" in source
    assert "SEAL-Bench run is operator-only" in source
    assert "governance actor must match authenticated caller" in source
    assert "only NEXUS or William/Henry may close governance challenges" in source


def test_external_helpers_no_longer_capture_privileged_dsn() -> None:
    assert "from db import DB_URL" in inspect.getsource(tb)
    assert "pg_dsn(required=True)" not in inspect.getsource(tb)
