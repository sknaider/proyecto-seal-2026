from __future__ import annotations

import asyncio
from email.message import EmailMessage
import os
from datetime import UTC, datetime
from pathlib import Path

from fastapi import HTTPException
from pydantic import ValidationError
import pytest

from soul_ingestion.adapters.text import TextAdapter
from soul_ingestion.adapters.email import EmailAdapter
from soul_ingestion.contracts import Scope, Sensitivity, TrustTier
from soul_ingestion.memory_writer import load_private_token
from soul_ingestion.service import (
    DEFAULT_TENANT,
    ReviewDecisionRequest,
    _process_and_persist,
    app,
)


class ArtifactTrap:
    def __init__(self) -> None:
        self.called = False

    def put(self, _content: bytes) -> str:
        self.called = True
        raise AssertionError("secret bytes reached the artifact store")


def test_secret_is_rejected_before_raw_artifact_write() -> None:
    artifact = TextAdapter().acquire(
        "Decisión: usar api_key = 'gsk_abc123def456ghi789jkl012mno345pqr' mañana.",
        tenant_id=DEFAULT_TENANT,
        owner_agent="ADA",
        scope=Scope.WILLIAM,
        sensitivity=Sensitivity.CONFIDENTIAL,
        trust_tier=TrustTier.OWNER_VERIFIED,
        observed_at=datetime.now(UTC),
    )
    previous = getattr(app.state, "artifacts", None)
    trap = ArtifactTrap()
    app.state.artifacts = trap
    try:
        with pytest.raises(HTTPException) as raised:
            asyncio.run(_process_and_persist(artifact, "generic_v1", True))
        assert raised.value.status_code == 422
        assert raised.value.detail["code"] == "secret_detected_before_persistence"
        assert trap.called is False
    finally:
        if previous is None:
            del app.state.artifacts
        else:
            app.state.artifacts = previous


def test_email_attachment_secret_is_rejected_before_raw_artifact_write() -> None:
    message = EmailMessage()
    message["From"] = "source@example.test"
    message["To"] = "william@example.test"
    message["Subject"] = "Conocimiento con adjunto"
    message.set_content("Cuerpo humano sin secretos.")
    message.add_attachment(
        b"api_key = 'gsk_abc123def456ghi789jkl012mno345pqr'",
        maintype="text",
        subtype="plain",
        filename="nota.txt",
    )
    artifact = EmailAdapter().acquire(
        message.as_bytes(),
        tenant_id=DEFAULT_TENANT,
        owner_agent="ADA",
        scope=Scope.WILLIAM,
        sensitivity=Sensitivity.CONFIDENTIAL,
        observed_at=datetime.now(UTC),
    )
    previous = getattr(app.state, "artifacts", None)
    trap = ArtifactTrap()
    app.state.artifacts = trap
    try:
        with pytest.raises(HTTPException) as raised:
            asyncio.run(_process_and_persist(artifact, "generic_v1", True))
        assert raised.value.status_code == 422
        assert raised.value.detail["code"] == "secret_detected_before_persistence"
        assert trap.called is False
    finally:
        if previous is None:
            del app.state.artifacts
        else:
            app.state.artifacts = previous


def test_review_identity_and_payload_are_fail_closed() -> None:
    valid = {
        "decision": "approved",
        "actor": "William",
        "actor_session_id": "a" * 64,
        "reason": "Revisado por efecto",
    }
    assert ReviewDecisionRequest.model_validate(valid).actor == "William"
    with pytest.raises(ValidationError):
        ReviewDecisionRequest.model_validate({**valid, "actor": "ADA"})
    with pytest.raises(ValidationError):
        ReviewDecisionRequest.model_validate({**valid, "tenant_id": str(DEFAULT_TENANT)})


def test_memory_capability_rejects_symlink_and_broad_mode(tmp_path: Path) -> None:
    token = tmp_path / "token"
    token.write_text("x" * 48, encoding="utf-8")
    os.chmod(token, 0o600)
    assert load_private_token(token) == "x" * 48
    os.chmod(token, 0o644)
    with pytest.raises(RuntimeError, match="permissions"):
        load_private_token(token)
    os.chmod(token, 0o600)
    link = tmp_path / "link"
    link.symlink_to(token)
    with pytest.raises(RuntimeError, match="regular file"):
        load_private_token(link)


def test_enforce_migration_has_exactly_once_and_narrow_resolver() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "memory/migrations/20260809_suie_human_review_enforce.sql"
    ).read_text(encoding="utf-8")
    assert "ingestion_one_initial_decision_per_candidate" in source
    assert "ingestion_one_promotion_per_candidate" in source
    assert "memories_ingestion_candidate_once" in source
    assert "ingestion_promoter_claim" in source
    assert "ingestion_promoter_fail" in source
    assert "stale or unowned promotion lease" in source
    assert "canonical memory binding not verified" in source
    assert "canonical invalidation not verified" in source
    assert "REVOKE INSERT, UPDATE, DELETE, TRUNCATE" in source
    assert "SECURITY DEFINER" in source
    assert "REVOKE ALL ON FUNCTION" in source
    assert "GRANT EXECUTE" in source


def test_browser_gateway_derives_authority_server_side() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "seal-studio/frontend/src/lib/soulKnowledgeProxy.ts"
    ).read_text(encoding="utf-8")
    assert 'owner_agent: "ADA"' in source
    assert 'scope: "william"' in source
    assert 'sensitivity: "confidential"' in source
    assert 'actor: "William"' in source
    assert "api/auth/me" in source
    assert "X-SUIE-Review-Token" in source
    assert "localStorage" not in source
    assert "sessionStorage" not in source
