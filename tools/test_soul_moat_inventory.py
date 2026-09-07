from __future__ import annotations

import pytest

import soul_moat_inventory as moat


def test_identity_requires_tables_and_material_evidence() -> None:
    tables = set(moat.REQUIRED_SSAI_TABLES)
    empty = {name: 0 for name in moat.REQUIRED_SSAI_EVIDENCE}
    assert moat._identity_is_materialized(tables, empty) is False

    material = {name: 1 for name in moat.REQUIRED_SSAI_EVIDENCE}
    assert moat._identity_is_materialized(tables, material) is True
    assert moat._identity_is_materialized(tables - {"ssai_keys"}, material) is False
    for name in moat.REQUIRED_SSAI_EVIDENCE:
        incomplete = dict(material)
        incomplete[name] = 0
        assert moat._identity_is_materialized(tables, incomplete) is False


def test_inventory_dsn_requires_explicit_nonprivileged_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        moat,
        "service_pg_dsn",
        lambda name: "postgresql://svc_soul_moat:synthetic@localhost:5433/seal_memory",
    )
    assert moat._resolve_dsn().startswith("postgresql://svc_soul_moat:")

    for role in ("seal", "postgres", "s%65al"):
        monkeypatch.setattr(
            moat,
            "service_pg_dsn",
            lambda name, role=role: (
                f"postgresql://{role}:synthetic@localhost:5433/seal_memory"
            ),
        )
        with pytest.raises(RuntimeError, match="privileged"):
            moat._resolve_dsn()


def test_inventory_source_has_no_privileged_literal_or_fallback() -> None:
    source = moat.Path(moat.__file__).read_text(encoding="utf-8")
    assert "seal_memory_2026" not in source
    assert 'os.environ.get("SEAL_PG_DSN")' not in source
    assert "service_pg_dsn(\"SOUL_MOAT_INVENTORY_DSN\")" in source
