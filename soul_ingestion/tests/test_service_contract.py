from __future__ import annotations

import inspect

from pydantic import ValidationError
import pytest

from soul_ingestion.service import TextIngestRequest, app
import soul_ingestion.storage as storage_module


def test_service_has_no_promotion_or_generic_uri_route() -> None:
    paths = {route.path for route in app.routes}
    assert "/v1/ingest/text" in paths
    assert "/v1/ingest/pdf" in paths
    assert "/v1/ingest/email" in paths
    assert "/v1/ingest/chat" in paths
    assert "/v1/ingest/youtube" in paths
    assert "/health" in paths
    assert not any("promot" in path for path in paths)
    assert not any(path.endswith("uri") or "ingest_uri" in path for path in paths)


def test_text_request_rejects_unknown_capability_fields() -> None:
    with pytest.raises(ValidationError):
        TextIngestRequest.model_validate(
            {"text": "dato", "owner_agent": "ADA", "execute_tools": True}
        )


def test_service_code_does_not_import_memory_core() -> None:
    source = inspect.getsource(inspect.getmodule(app).__class__) if False else inspect.getsource(__import__("soul_ingestion.service", fromlist=["app"]))
    assert "mcp_server_v4" not in source
    assert "memory_store" not in source


def test_document_read_contract_exposes_evidence_and_candidates() -> None:
    source = inspect.getsource(storage_module.StagingRepository.get_document)
    assert "SELECT document_id, tenant_id, owner_agent" in source
    assert '"segments": [dict(item) for item in segments]' in source
    assert '"derivations": [dict(item) for item in derivations]' in source
    assert '"candidates": [dict(item) for item in candidates]' in source
    assert "protected_spans" in source
    assert "confidence_factors" in source


def test_service_decodes_jsonb_as_structured_values() -> None:
    source = inspect.getsource(__import__("soul_ingestion.service", fromlist=["app"]))
    assert "init=_configure_connection" in source
    assert 'for type_name in ("json", "jsonb")' in source
    assert "decoder=json.loads" in source


def test_service_uses_only_the_dedicated_database_login() -> None:
    source = inspect.getsource(__import__("soul_ingestion.service", fromlist=["app"]))
    assert 'login != "svc_soul_ingestion"' in source
    assert "postgresql://seal:" not in source
    assert "SUIE_DATABASE_URL" not in source
    bootstrap_source = (
        __import__("pathlib").Path(__file__).resolve().parents[2]
        / "scripts"
        / "bootstrap_soul_ingestion_db_role.py"
    ).read_text(encoding="utf-8")
    assert "postgresql://seal:" not in bootstrap_source
