"""Authenticated localhost service for SUIE staging.

This service has deliberately no promotion endpoint and no privilege on
``soul_v3.memories``.
"""

from __future__ import annotations

import hmac
import base64
import binascii
import hashlib
import json
import os
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

import asyncpg
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .adapters.chat import ChatAdapter, ChatMessage
from .adapters.email import EmailAdapter
from .adapters.pdf import PdfAdapter
from .adapters.text import TextAdapter
from .adapters.youtube import YouTubeAdapter
from .artifacts import ArtifactStore
from .contracts import RawArtifact, Scope, Sensitivity, TrustTier
from .engine import IngestionEngine
from .storage import StagingRepository


DEFAULT_TENANT = UUID("00000000-0000-0000-0000-000000000001")
TOKEN_PATH = Path(os.environ.get("SUIE_TOKEN_FILE", "var/soul_ingestion/service.token")).resolve()
ARTIFACT_PATH = Path(os.environ.get("SUIE_ARTIFACT_ROOT", "var/soul_ingestion/artifacts")).resolve()


async def _configure_connection(connection: asyncpg.Connection) -> None:
    """Decode PostgreSQL JSON/JSONB as structured API values, never strings."""

    for type_name in ("json", "jsonb"):
        await connection.set_type_codec(
            type_name,
            schema="pg_catalog",
            encoder=json.dumps,
            decoder=json.loads,
            format="text",
        )


def _database_dsn(token: str) -> str:
    """Derive a domain-separated password for the restricted SUIE login."""

    password = hashlib.sha256(b"seal-suie-db-v1\0" + token.encode("utf-8")).hexdigest()
    host = os.environ.get("SUIE_DB_HOST", "127.0.0.1")
    port = int(os.environ.get("SUIE_DB_PORT", "5433"))
    database = os.environ.get("SUIE_DB_NAME", "seal_memory")
    login = os.environ.get("SUIE_DB_LOGIN", "svc_soul_ingestion")
    if login != "svc_soul_ingestion":
        raise RuntimeError("SUIE refuses to start with a non-dedicated database login")
    return f"postgresql://{login}:{quote(password)}@{host}:{port}/{database}"


def _load_or_create_token(path: Path) -> str:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        token = path.read_text(encoding="utf-8").strip()
        if len(token) < 32:
            raise RuntimeError("SUIE token file is invalid")
        if path.stat().st_mode & 0o077:
            raise RuntimeError("SUIE token file permissions are too broad")
        return token
    token = secrets.token_urlsafe(48)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(token)
        handle.flush()
        os.fsync(handle.fileno())
    return token


class TextIngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=5_000_000)
    tenant_id: UUID = DEFAULT_TENANT
    owner_agent: str = Field(min_length=1, max_length=64)
    scope: Scope = Scope.PRIVATE
    source_ref: str | None = Field(default=None, max_length=2048)
    title: str | None = Field(default=None, max_length=2048)
    language: str = Field(default="es", pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]+)*$|^und$")
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    profile_id: str = Field(default="generic_v1", max_length=128)
    propose_candidates: bool = False


class BinaryIngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_base64: str = Field(min_length=1, max_length=70_000_000)
    tenant_id: UUID = DEFAULT_TENANT
    owner_agent: str = Field(min_length=1, max_length=64)
    scope: Scope = Scope.PRIVATE
    source_ref: str | None = Field(default=None, max_length=2048)
    title: str | None = Field(default=None, max_length=2048)
    language: str = Field(default="und", pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]+)*$|^und$")
    sensitivity: Sensitivity = Sensitivity.CONFIDENTIAL
    profile_id: str = Field(default="generic_v1", max_length=128)
    propose_candidates: bool = False


class ChatIngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    messages: list[ChatMessage] = Field(min_length=1, max_length=5000)
    tenant_id: UUID = DEFAULT_TENANT
    owner_agent: str = Field(min_length=1, max_length=64)
    scope: Scope = Scope.TEAM
    allowed_channel: str = Field(min_length=1, max_length=255)
    propose_candidates: bool = False


class YouTubeIngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    video: str = Field(min_length=1, max_length=2048)
    tenant_id: UUID = DEFAULT_TENANT
    owner_agent: str = Field(min_length=1, max_length=64)
    scope: Scope = Scope.PRIVATE
    languages: tuple[str, ...] = ("es", "en")
    profile_id: str = Field(default="generic_v1", max_length=128)
    propose_candidates: bool = False


@asynccontextmanager
async def lifespan(application: FastAPI):
    token = _load_or_create_token(TOKEN_PATH)
    ARTIFACT_PATH.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(ARTIFACT_PATH, 0o700)
    pool = await asyncpg.create_pool(
        _database_dsn(token),
        min_size=1,
        max_size=5,
        command_timeout=15,
        init=_configure_connection,
    )
    application.state.token = token
    application.state.pool = pool
    application.state.repository = StagingRepository(pool)
    application.state.artifacts = ArtifactStore(ARTIFACT_PATH)
    application.state.engine = IngestionEngine()
    yield
    await pool.close()


app = FastAPI(
    title="SOUL Universal Ingestion Engine",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)


async def require_token(authorization: Annotated[str | None, Header()] = None) -> None:
    expected = app.state.token
    supplied = authorization.removeprefix("Bearer ") if authorization else ""
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="authenticated SUIE capability required")


@app.get("/health")
async def health() -> dict[str, object]:
    details = await app.state.repository.health(DEFAULT_TENANT)
    artifact_ready = ARTIFACT_PATH.exists() and os.access(ARTIFACT_PATH, os.R_OK | os.W_OK)
    healthy = (
        details["staging_tables"] == 6
        and details["role"] == "pr_ingestion_processor"
        and details["memory_insert_privilege"] is False
        and artifact_ready
    )
    if not healthy:
        raise HTTPException(status_code=503, detail="SUIE capacity check failed")
    return {
        "ok": True,
        "status": "healthy",
        "processor": "extractive_v1@1.1.0",
        "database": details["database"],
        "database_login": details["login"],
        "database_role": details["role"],
        "staging_tables": details["staging_tables"],
        "memory_write": False,
        "artifact_store": "ready",
    }


@app.post("/v1/ingest/text", dependencies=[Depends(require_token)])
async def ingest_text(request: TextIngestRequest) -> dict[str, object]:
    artifact = TextAdapter().acquire(
        request.text,
        tenant_id=request.tenant_id,
        owner_agent=request.owner_agent,
        scope=request.scope,
        source_ref=request.source_ref,
        title=request.title,
        language=request.language,
        sensitivity=request.sensitivity,
        trust_tier=TrustTier.OWNER_VERIFIED,
        observed_at=datetime.now(UTC),
    )
    return await _process_and_persist(artifact, request.profile_id, request.propose_candidates)


async def _process_and_persist(
    artifact: RawArtifact, profile_id: str, propose_candidates: bool
) -> dict[str, object]:
    artifact_ref = app.state.artifacts.put(artifact.content)
    if artifact_ref != f"artifact://sha256/{artifact.raw_hash_sha256}":
        raise HTTPException(status_code=500, detail="artifact address verification failed")
    result = app.state.engine.process(
        artifact,
        profile_id=profile_id,
        propose_candidates=propose_candidates,
    )
    replay = await app.state.repository.persist(artifact, result)
    derivation = result.derivations[0]
    return {
        "ok": result.ok,
        "document_id": str(result.document.document_id),
        "state": result.state,
        "idempotent_replay": replay,
        "derivation_id": str(derivation.derivation_id),
        "candidate_ids": [str(candidate.candidate_id) for candidate in result.candidates],
        "digest": derivation.content,
        "coverage": derivation.coverage.model_dump(mode="json"),
        "warnings": list(result.warnings),
        "provenance_complete": result.provenance_complete,
    }


def _decode_base64(value: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail="content_base64 is not valid canonical base64") from exc


@app.post("/v1/ingest/pdf", dependencies=[Depends(require_token)])
async def ingest_pdf(request: BinaryIngestRequest) -> dict[str, object]:
    artifact = PdfAdapter().acquire(
        _decode_base64(request.content_base64),
        tenant_id=request.tenant_id,
        owner_agent=request.owner_agent,
        scope=request.scope,
        source_ref=request.source_ref,
        title=request.title,
        language=request.language,
        sensitivity=request.sensitivity,
        observed_at=datetime.now(UTC),
    )
    return await _process_and_persist(artifact, request.profile_id, request.propose_candidates)


@app.post("/v1/ingest/email", dependencies=[Depends(require_token)])
async def ingest_email(request: BinaryIngestRequest) -> dict[str, object]:
    artifact = EmailAdapter().acquire(
        _decode_base64(request.content_base64),
        tenant_id=request.tenant_id,
        owner_agent=request.owner_agent,
        scope=request.scope,
        sensitivity=request.sensitivity,
        observed_at=datetime.now(UTC),
    )
    return await _process_and_persist(artifact, request.profile_id, request.propose_candidates)


@app.post("/v1/ingest/chat", dependencies=[Depends(require_token)])
async def ingest_chat(request: ChatIngestRequest) -> dict[str, object]:
    artifact = ChatAdapter().acquire(
        request.messages,
        tenant_id=request.tenant_id,
        owner_agent=request.owner_agent,
        scope=request.scope,
        allowed_channel=request.allowed_channel,
        observed_at=datetime.now(UTC),
    )
    return await _process_and_persist(artifact, "team_conversation_v1", request.propose_candidates)


@app.post("/v1/ingest/youtube", dependencies=[Depends(require_token)])
async def ingest_youtube(request: YouTubeIngestRequest) -> dict[str, object]:
    try:
        artifact = await YouTubeAdapter().acquire(
            request.video,
            tenant_id=request.tenant_id,
            owner_agent=request.owner_agent,
            scope=request.scope,
            languages=request.languages,
            observed_at=datetime.now(UTC),
        )
    except RuntimeError as exc:
        reason = "subtitle_unavailable" if "no subtitle" in str(exc) else "youtube_upstream_failed"
        raise HTTPException(status_code=502, detail=reason) from exc
    return await _process_and_persist(artifact, request.profile_id, request.propose_candidates)


@app.get("/v1/documents/{document_id}", dependencies=[Depends(require_token)])
async def get_document(
    document_id: UUID,
    tenant_id: UUID = Query(default=DEFAULT_TENANT),
) -> dict[str, object]:
    document = await app.state.repository.get_document(tenant_id, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found in authorized tenant")
    return {"ok": True, "document": document}


@app.get("/v1/search", dependencies=[Depends(require_token)])
async def search_documents(
    q: str = Query(min_length=1, max_length=500),
    tenant_id: UUID = Query(default=DEFAULT_TENANT),
    limit: int = Query(default=10, ge=1, le=50),
) -> dict[str, object]:
    results = await app.state.repository.search_documents(tenant_id, q, limit=limit)
    return {"ok": True, "count": len(results), "results": results}
