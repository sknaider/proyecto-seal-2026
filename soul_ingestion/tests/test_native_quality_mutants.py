from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SQL = (ROOT / "memory/migrations/20260809_suie_human_review_enforce.sql").read_text()
SERVICE = (ROOT / "soul_ingestion/service.py").read_text()


def secure_contract(sql: str, service: str) -> bool:
    return all(
        (
            "REVOKE INSERT, UPDATE, DELETE, TRUNCATE" in sql,
            "AND metadata ? 'ingestion_candidate_id';" in sql,
            "stale or unowned promotion lease" in sql,
            "get_payload(decode=True)" in service,
        )
    )


@pytest.mark.parametrize(
    ("sql", "service"),
    [
        pytest.param(
            SQL.replace("REVOKE INSERT, UPDATE, DELETE, TRUNCATE", "REVOKE INSERT"),
            SERVICE,
            id="direct-outbox-update",
        ),
        pytest.param(
            SQL.replace(
                "AND metadata ? 'ingestion_candidate_id';",
                "AND metadata ? 'ingestion_candidate_id' AND invalid_at IS NULL;",
            ),
            SERVICE,
            id="historical-uniqueness",
        ),
        pytest.param(
            SQL.replace("stale or unowned promotion lease", "promotion accepted"),
            SERVICE,
            id="lease-ownership",
        ),
        pytest.param(
            SQL,
            SERVICE.replace("get_payload(decode=True)", "get_payload(decode=False)"),
            id="mime-decode",
        ),
    ],
)
def test_each_security_mutation_is_detected(sql: str, service: str) -> None:
    assert secure_contract(SQL, SERVICE)
    assert not secure_contract(sql, service)
