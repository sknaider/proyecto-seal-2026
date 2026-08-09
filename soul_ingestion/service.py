"""Authenticated localhost SUIE staging and human-review service.

Ingestion and review use separate restricted PostgreSQL identities. Approved
decisions enter a durable outbox consumed by the isolated promoter service.
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
from email import policy
from email.parser import BytesParser
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
REVIEW_TOKEN_PATH = Path(
    os.environ.get("SUIE_REVIEW_TOKEN_FILE", "var/soul_ingestion/review.token")
).resolve()
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


def _database_dsn(token: str, *, login: str = "svc_soul_ingestion") -> str:
    """Derive a domain-separated password for one restricted SUIE login."""

    allowed = {
        "svc_soul_ingestion": b"seal-suie-db-v1\0",
        "svc_soul_ingestion_review": b"seal-suie-review-db-v1\0",
        "svc_soul_ingestion_promoter": b"seal-suie-promoter-db-v1\0",
    }
    if login not in allowed:
        raise RuntimeError("SUIE refuses to start with a non-dedicated database login")
    password = hashlib.sha256(allowed[login] + token.encode("utf-8")).hexdigest()
    host = os.environ.get("SUIE_DB_HOST", "127.0.0.1")
    port = int(os.environ.get("SUIE_DB_PORT", "5433"))
    database = os.environ.get("SUIE_DB_NAME", "seal_memory")
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


class ReviewDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: str = Field(pattern=r"^(approved|rejected|revoked)$")
    actor: str = Field(pattern=r"^William$", max_length=64)
    actor_session_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=3, max_length=1000)


@asynccontextmanager
async def lifespan(application: FastAPI):
    token = _load_or_create_token(TOKEN_PATH)
    review_token = _load_or_create_token(REVIEW_TOKEN_PATH)
    ARTIFACT_PATH.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(ARTIFACT_PATH, 0o700)
    pool = await asyncpg.create_pool(
        _database_dsn(token, login=os.environ.get("SUIE_DB_LOGIN", "svc_soul_ingestion")),
        min_size=1,
        max_size=5,
        command_timeout=15,
        init=_configure_connection,
    )
    review_pool = await asyncpg.create_pool(
        _database_dsn(review_token, login="svc_soul_ingestion_review"),
        min_size=1,
        max_size=2,
        command_timeout=15,
        init=_configure_connection,
    )
    application.state.token = token
    application.state.review_token = review_token
    application.state.pool = pool
    application.state.review_pool = review_pool
    application.state.repository = StagingRepository(pool, review_pool=review_pool)
    application.state.artifacts = ArtifactStore(ARTIFACT_PATH)
    application.state.engine = IngestionEngine()
    yield
    await review_pool.close()
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


async def require_review_token(
    x_suie_review_token: Annotated[str | None, Header()] = None,
) -> None:
    supplied = x_suie_review_token or ""
    if not supplied or not hmac.compare_digest(supplied, app.state.review_token):
        raise HTTPException(status_code=401, detail="verified human review capability required")


@app.get("/health")
async def health() -> dict[str, object]:
    details = await app.state.repository.health(DEFAULT_TENANT)
    review_details = await app.state.repository.review_health(DEFAULT_TENANT)
    artifact_ready = ARTIFACT_PATH.exists() and os.access(ARTIFACT_PATH, os.R_OK | os.W_OK)
    healthy = (
        # The processor intentionally cannot see the review outbox.
        details["staging_tables"] == 6
        and details["role"] == "pr_ingestion_processor"
        and details["memory_insert_privilege"] is False
        and details["review_role_membership"] is False
        and review_details["login"] == "svc_soul_ingestion_review"
        and review_details["role"] == "pr_ingestion_reviewer"
        and review_details["memory_insert_privilege"] is False
        and review_details["promoter_role_membership"] is False
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
        "processor_can_review": False,
        "review_database_login": review_details["login"],
        "review_memory_write": False,
        "reviewer_can_promote": False,
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
    # Secrets are rejected before writing even the immutable raw artifact.
    from memory.secret_scanner import scan_text

    scan_targets = [artifact.extracted_text or "", artifact.content.decode("utf-8", errors="ignore")]
    if artifact.media_type == "message/rfc822":
        # MIME transfer encodings hide attachment bytes from a raw-text scan.
        # Decode every leaf before the immutable raw message can reach disk.
        message = BytesParser(policy=policy.default).parsebytes(artifact.content)
        for part in message.walk():
            if part.is_multipart():
                continue
            payload = part.get_payload(decode=True) or b""
            charset = part.get_content_charset() or "utf-8"
            try:
                scan_targets.append(payload.decode(charset, errors="replace"))
            except LookupError:
                scan_targets.append(payload.decode("utf-8", errors="replace"))
    detections = [finding for target in scan_targets for finding in scan_text(target)]
    if detections:
        kinds = sorted({item.pattern_name for item in detections})
        raise HTTPException(
            status_code=422,
            detail={"code": "secret_detected_before_persistence", "types": kinds},
        )
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


@app.get("/v1/review/candidates", dependencies=[Depends(require_review_token)])
async def list_review_candidates(
    tenant_id: UUID = Query(default=DEFAULT_TENANT),
    limit: int = Query(default=100, ge=1, le=200),
) -> dict[str, object]:
    candidates = await app.state.repository.list_review_candidates(tenant_id, limit=limit)
    return {"ok": True, "count": len(candidates), "candidates": candidates}


@app.post(
    "/v1/review/candidates/{candidate_id}/decision",
    dependencies=[Depends(require_review_token)],
)
async def review_candidate(
    candidate_id: UUID,
    request: ReviewDecisionRequest,
    tenant_id: UUID = Query(default=DEFAULT_TENANT),
) -> dict[str, object]:
    from memory.secret_scanner import scan_text

    if scan_text(request.reason):
        raise HTTPException(status_code=422, detail="review reason contains secret material")
    try:
        result = await app.state.repository.review_candidate(
            tenant_id,
            candidate_id,
            decision=request.decision,
            actor=request.actor,
            actor_session_id=request.actor_session_id,
            reason=request.reason,
        )
    except asyncpg.NoDataFoundError as exc:
        raise HTTPException(status_code=404, detail="candidate not found in authorized tenant") from exc
    except (asyncpg.CheckViolationError, asyncpg.UniqueViolationError) as exc:
        raise HTTPException(status_code=409, detail=str(exc).split("\n", 1)[0]) from exc
    except asyncpg.InsufficientPrivilegeError as exc:
        raise HTTPException(status_code=403, detail="review authority rejected") from exc
    return {"ok": True, **result}


@app.get("/v1/review/export.md", dependencies=[Depends(require_review_token)])
async def export_reviewed_markdown(
    tenant_id: UUID = Query(default=DEFAULT_TENANT),
) -> dict[str, object]:
    candidates = await app.state.repository.list_review_candidates(tenant_id, limit=200)
    promoted = [item for item in candidates if item["review_state"] == "promoted"]
    lines = ["# Conocimiento humano revisado en SOUL", ""]
    for item in promoted:
        lines.extend(
            [
                f"## {item['title'] or item['proposed_category']}",
                "",
                str(item["proposed_content"]),
                "",
                f"- Categoría: `{item['proposed_category']}`",
                f"- Fuente: `{item['source_ref']}`",
                f"- Evidencia SHA-256: `{item['raw_hash_sha256']}`",
                "",
            ]
        )
    return {"ok": True, "count": len(promoted), "markdown": "\n".join(lines)}
