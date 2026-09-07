from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HEARTBEAT = ROOT / "memory" / "seal_heartbeat.py"


def test_heartbeat_uses_canonical_secret_loader_without_password_literal() -> None:
    source = HEARTBEAT.read_text(encoding="utf-8")

    assert "from seal_secrets import pg_dsn" in source
    assert "_DB_URL = pg_dsn(required=True)" in source
    assert "seal_memory_2026" not in source
    assert "postgresql://seal:" not in source
