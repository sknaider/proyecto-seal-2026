"""Durable, model-independent capture of conversational memory.

The orchestration layer deliberately does not call an LLM.  It records the complete
turn, exposes the exact extraction input, and requires the extractor to close every
attempt with a durable success/failure receipt.  Storage is hidden behind a protocol;
SQLite is only the portable reference adapter.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import unicodedata
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _text(
    value: object,
    field_name: str,
    *,
    maximum: int = 1_000_000,
    preserve: bool = False,
) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be text")
    original = value
    value = value.strip()
    if not value:
        raise ValueError(f"{field_name} must not be empty")
    if "\x00" in value or len(value) > maximum:
        raise ValueError(f"{field_name} is invalid or too large")
    return original if preserve else value


def _canonical(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _digest(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class Turn:
    turn_id: str
    session_id: str
    user_text: str
    assistant_text: str | None
    status: str
    error: str
    created_at: str
    completed_at: str | None


@dataclass(frozen=True, slots=True)
class ExtractionInput:
    """Byte-bound full turn supplied to an external fact extractor."""

    turn_id: str
    session_id: str
    user_text: str
    assistant_text: str
    input_sha256: str


@dataclass(frozen=True, slots=True)
class FactCandidate:
    content: str
    evidence: str = ""
    importance: int = 5
    confidence: float = 1.0
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FactWriteResult:
    fact_id: str
    dedupe_sha256: str
    created: bool


@dataclass(frozen=True, slots=True)
class StoredFact:
    fact_id: str
    content: str
    importance: int
    confidence: float
    metadata: Mapping[str, Any]
    source_turn_ids: tuple[str, ...]
    projection_status: str
    projection_error: str


@dataclass(frozen=True, slots=True)
class ExtractionReceipt:
    receipt_id: str
    turn_id: str
    attempt: int
    status: str
    input_sha256: str
    output_sha256: str
    submitted_facts: int
    new_facts: int
    error: str
    created_at: str


@dataclass(frozen=True, slots=True)
class Episode:
    episode_id: str
    session_id: str
    summary: str
    turn_ids: tuple[str, ...]
    content_sha256: str
    created_at: str


@dataclass(frozen=True, slots=True)
class EpisodeProjection:
    episode_id: str
    status: str
    core_memory_id: int | None
    error: str
    updated_at: str


class ConversationStore(Protocol):
    def begin_turn(self, session_id: str, user_text: str) -> str: ...
    def complete_turn(self, turn_id: str, assistant_text: str) -> None: ...
    def fail_turn(self, turn_id: str, error: str) -> None: ...
    def extraction_input(self, turn_id: str) -> ExtractionInput: ...
    def record_extraction(
        self,
        turn_id: str,
        status: str,
        facts: Sequence[FactCandidate | str] = (),
        error: str = "",
    ) -> ExtractionReceipt: ...
    def store_fact_candidate(
        self,
        turn_id: str,
        content: str,
        *,
        evidence: str = "",
        importance: int = 5,
        confidence: float = 1.0,
        metadata: Mapping[str, Any] | None = None,
    ) -> FactWriteResult: ...
    def store_episode(
        self, session_id: str, summary: str, turn_ids: Sequence[str]
    ) -> Episode: ...
    def episode_projection(self, episode_id: str) -> EpisodeProjection: ...
    def mark_episode_projected(self, episode_id: str, core_memory_id: int) -> None: ...
    def mark_episode_projection_failed(self, episode_id: str, error: str) -> None: ...
    def pending_facts(
        self, limit: int = 100, exclude_fact_ids: Sequence[str] = ()
    ) -> list[StoredFact]: ...
    def mark_fact_projected(self, fact_id: str, core_memory_id: int) -> None: ...
    def mark_fact_projection_failed(self, fact_id: str, error: str) -> None: ...
    def recent_turns(self, limit: int = 20, session_id: str | None = None) -> list[Turn]: ...
    def retryable_extractions(
        self,
        limit: int = 100,
        max_attempts: int = 3,
        exclude_turn_ids: Sequence[str] = (),
    ) -> list[ExtractionInput]: ...
    def recent_episodes(
        self, limit: int = 20, session_id: str | None = None
    ) -> list[Episode]: ...
    def status_counts(self) -> dict[str, int]: ...


class ConversationMemory:
    """Stable facade used by HTTP/UI code, independent of model and database."""

    def __init__(self, store: ConversationStore) -> None:
        self._store = store

    @classmethod
    def for_sqlite(cls, path: str | Path) -> ConversationMemory:
        return cls(SQLiteConversationStore(path))

    def begin_turn(self, session_id: str, user_text: str) -> str:
        return self._store.begin_turn(session_id, user_text)

    def complete_turn(self, turn_id: str, assistant_text: str) -> None:
        self._store.complete_turn(turn_id, assistant_text)

    def fail_turn(self, turn_id: str, error: str) -> None:
        self._store.fail_turn(turn_id, error)

    def extraction_input(self, turn_id: str) -> ExtractionInput:
        return self._store.extraction_input(turn_id)

    def record_extraction(
        self,
        turn_id: str,
        status: str,
        facts: Sequence[FactCandidate | str] = (),
        error: str = "",
    ) -> ExtractionReceipt:
        return self._store.record_extraction(turn_id, status, facts, error)

    def store_fact_candidate(
        self,
        turn_id: str,
        content: str,
        *,
        evidence: str = "",
        importance: int = 5,
        confidence: float = 1.0,
        metadata: Mapping[str, Any] | None = None,
    ) -> FactWriteResult:
        return self._store.store_fact_candidate(
            turn_id,
            content,
            evidence=evidence,
            importance=importance,
            confidence=confidence,
            metadata=metadata,
        )

    def store_episode(
        self, session_id: str, summary: str, turn_ids: Sequence[str]
    ) -> Episode:
        return self._store.store_episode(session_id, summary, turn_ids)

    def episode_projection(self, episode_id: str) -> EpisodeProjection:
        return self._store.episode_projection(episode_id)

    def mark_episode_projected(self, episode_id: str, core_memory_id: int) -> None:
        self._store.mark_episode_projected(episode_id, core_memory_id)

    def mark_episode_projection_failed(self, episode_id: str, error: str) -> None:
        self._store.mark_episode_projection_failed(episode_id, error)

    def pending_facts(
        self, limit: int = 100, exclude_fact_ids: Sequence[str] = ()
    ) -> list[StoredFact]:
        return self._store.pending_facts(limit, exclude_fact_ids)

    def mark_fact_projected(self, fact_id: str, core_memory_id: int) -> None:
        self._store.mark_fact_projected(fact_id, core_memory_id)

    def mark_fact_projection_failed(self, fact_id: str, error: str) -> None:
        self._store.mark_fact_projection_failed(fact_id, error)

    def recent_turns(self, limit: int = 20, session_id: str | None = None) -> list[Turn]:
        return self._store.recent_turns(limit, session_id)

    def retryable_extractions(
        self,
        limit: int = 100,
        max_attempts: int = 3,
        exclude_turn_ids: Sequence[str] = (),
    ) -> list[ExtractionInput]:
        return self._store.retryable_extractions(
            limit, max_attempts, exclude_turn_ids
        )

    def recent_episodes(
        self, limit: int = 20, session_id: str | None = None
    ) -> list[Episode]:
        return self._store.recent_episodes(limit, session_id)

    def status_counts(self) -> dict[str, int]:
        return self._store.status_counts()


class SQLiteConversationStore:
    """Portable WAL-backed adapter; every public mutation is one transaction."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(Path(path))
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def _initialize(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversation_turns (
                    turn_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                    user_text TEXT NOT NULL, assistant_text TEXT,
                    status TEXT NOT NULL CHECK(status IN ('started','completed','failed')),
                    error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS ix_conversation_turns_session
                    ON conversation_turns(session_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS extraction_state (
                    turn_id TEXT PRIMARY KEY REFERENCES conversation_turns(turn_id),
                    status TEXT NOT NULL CHECK(status IN ('waiting','pending','success','failure')),
                    attempts INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory_facts (
                    fact_id TEXT PRIMARY KEY, content TEXT NOT NULL,
                    normalized_content TEXT NOT NULL, dedupe_sha256 TEXT NOT NULL UNIQUE,
                    importance INTEGER NOT NULL CHECK(importance BETWEEN 1 AND 10),
                    confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
                    metadata_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    core_memory_id INTEGER,
                    projection_status TEXT NOT NULL DEFAULT 'pending'
                        CHECK(projection_status IN ('pending','projected','failed')),
                    projection_error TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS fact_sources (
                    fact_id TEXT NOT NULL REFERENCES memory_facts(fact_id),
                    turn_id TEXT NOT NULL REFERENCES conversation_turns(turn_id),
                    evidence TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(fact_id, turn_id)
                );
                CREATE TABLE IF NOT EXISTS extraction_receipts (
                    receipt_id TEXT PRIMARY KEY,
                    turn_id TEXT NOT NULL REFERENCES conversation_turns(turn_id),
                    attempt INTEGER NOT NULL, status TEXT NOT NULL,
                    input_sha256 TEXT NOT NULL, output_sha256 TEXT NOT NULL,
                    submitted_facts INTEGER NOT NULL, new_facts INTEGER NOT NULL,
                    error TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(turn_id, attempt)
                );
                CREATE TABLE IF NOT EXISTS conversation_episodes (
                    episode_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                    summary TEXT NOT NULL, turn_ids_json TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_conversation_episodes_session
                    ON conversation_episodes(session_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS episode_projections (
                    episode_id TEXT PRIMARY KEY
                        REFERENCES conversation_episodes(episode_id),
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending','projected','failed')),
                    core_memory_id INTEGER,
                    error TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
                );
                """
            )
        finally:
            conn.close()

    @staticmethod
    def _begin(conn: sqlite3.Connection) -> None:
        conn.execute("BEGIN IMMEDIATE")

    @staticmethod
    def _turn(conn: sqlite3.Connection, turn_id: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM conversation_turns WHERE turn_id=?", (turn_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown turn: {turn_id}")
        return row

    def begin_turn(self, session_id: str, user_text: str) -> str:
        session_id = _text(session_id, "session_id", maximum=200)
        user_text = _text(user_text, "user_text", preserve=True)
        turn_id, created = str(uuid.uuid4()), _now()
        conn = self._connect()
        try:
            self._begin(conn)
            conn.execute(
                "INSERT INTO conversation_turns(turn_id,session_id,user_text,status,created_at) "
                "VALUES(?,?,?,'started',?)",
                (turn_id, session_id, user_text, created),
            )
            conn.execute(
                "INSERT INTO extraction_state(turn_id,status,updated_at) VALUES(?,'waiting',?)",
                (turn_id, created),
            )
            conn.commit()
            return turn_id
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def complete_turn(self, turn_id: str, assistant_text: str) -> None:
        assistant_text = _text(assistant_text, "assistant_text", preserve=True)
        conn = self._connect()
        try:
            self._begin(conn)
            row = self._turn(conn, turn_id)
            if row["status"] == "completed":
                if row["assistant_text"] == assistant_text:
                    conn.commit()
                    return
                raise ValueError("completed turn cannot be rewritten")
            if row["status"] != "started":
                raise ValueError("only a started turn can be completed")
            completed = _now()
            conn.execute(
                "UPDATE conversation_turns SET assistant_text=?,status='completed',"
                "completed_at=? WHERE turn_id=?",
                (assistant_text, completed, turn_id),
            )
            conn.execute(
                "UPDATE extraction_state SET status='pending',updated_at=? WHERE turn_id=?",
                (completed, turn_id),
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def fail_turn(self, turn_id: str, error: str) -> None:
        error = _text(error, "error", maximum=10_000)
        conn = self._connect()
        try:
            self._begin(conn)
            row = self._turn(conn, turn_id)
            if row["status"] == "failed" and row["error"] == error:
                conn.commit()
                return
            if row["status"] != "started":
                raise ValueError("only a started turn can fail")
            completed = _now()
            conn.execute(
                "UPDATE conversation_turns SET status='failed',error=:error,"
                "completed_at=:completed_at WHERE turn_id=:turn_id",
                {"error": error, "completed_at": completed, "turn_id": turn_id},
            )
            conn.execute(
                "UPDATE extraction_state SET status='waiting',updated_at=? WHERE turn_id=?",
                (completed, turn_id),
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _input_from_row(row: sqlite3.Row) -> ExtractionInput:
        if row["status"] != "completed" or not row["assistant_text"]:
            raise ValueError("extraction requires a completed user+assistant turn")
        payload = {
            "turn_id": row["turn_id"],
            "session_id": row["session_id"],
            "user_text": row["user_text"],
            "assistant_text": row["assistant_text"],
        }
        return ExtractionInput(**payload, input_sha256=_digest(payload))

    def extraction_input(self, turn_id: str) -> ExtractionInput:
        conn = self._connect()
        try:
            return self._input_from_row(self._turn(conn, turn_id))
        finally:
            conn.close()

    @staticmethod
    def _candidate(value: FactCandidate | str) -> FactCandidate:
        return value if isinstance(value, FactCandidate) else FactCandidate(content=value)

    @staticmethod
    def _validate_candidate(candidate: FactCandidate) -> tuple[str, str, int, float, str]:
        content = _text(candidate.content, "fact content", maximum=10_000)
        if not isinstance(candidate.evidence, str):
            raise TypeError("fact evidence must be text")
        evidence = candidate.evidence.strip()
        if "\x00" in evidence or len(evidence) > 100_000:
            raise ValueError("fact evidence is invalid or too large")
        if not isinstance(candidate.importance, int) or isinstance(candidate.importance, bool):
            raise TypeError("importance must be an integer")
        if not 1 <= candidate.importance <= 10:
            raise ValueError("importance must be between 1 and 10")
        if isinstance(candidate.confidence, bool):
            raise TypeError("confidence must be a number")
        confidence = float(candidate.confidence)
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        metadata_json = json.dumps(
            dict(candidate.metadata), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return content, evidence, candidate.importance, confidence, metadata_json

    def _store_fact(
        self, conn: sqlite3.Connection, turn: sqlite3.Row, candidate: FactCandidate
    ) -> FactWriteResult:
        content, evidence, importance, confidence, metadata_json = self._validate_candidate(
            candidate
        )
        if evidence and evidence.casefold() not in str(turn["user_text"]).casefold():
            raise ValueError("fact evidence must be a literal span of the user turn")
        normalized = _canonical(content)
        dedupe = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        existing = conn.execute(
            "SELECT fact_id FROM memory_facts WHERE dedupe_sha256=?", (dedupe,)
        ).fetchone()
        created = existing is None
        fact_id = str(uuid.uuid4()) if created else str(existing["fact_id"])
        now = _now()
        if created:
            conn.execute(
                "INSERT INTO memory_facts("
                "fact_id,content,normalized_content,dedupe_sha256,importance,confidence,"
                "metadata_json,created_at,core_memory_id,projection_status,projection_error"
                ") VALUES(?,?,?,?,?,?,?,?,NULL,'pending','')",
                (
                    fact_id, content, normalized, dedupe, importance, confidence,
                    metadata_json, now,
                ),
            )
        conn.execute(
            "INSERT OR IGNORE INTO fact_sources(fact_id,turn_id,evidence,created_at) "
            "VALUES(?,?,?,?)",
            (fact_id, turn["turn_id"], evidence, now),
        )
        return FactWriteResult(fact_id, dedupe, created)

    @staticmethod
    def _fact(row: sqlite3.Row) -> StoredFact:
        return StoredFact(
            fact_id=row["fact_id"],
            content=row["content"],
            importance=int(row["importance"]),
            confidence=float(row["confidence"]),
            metadata=json.loads(row["metadata_json"]),
            source_turn_ids=tuple(filter(None, str(row["source_turn_ids"] or "").split(","))),
            projection_status=row["projection_status"],
            projection_error=row["projection_error"],
        )

    def pending_facts(
        self, limit: int = 100, exclude_fact_ids: Sequence[str] = ()
    ) -> list[StoredFact]:
        """Return never-projected and failed facts; failures remain retryable."""

        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        excluded = tuple(dict.fromkeys(exclude_fact_ids))
        if len(excluded) > 10_000:
            raise ValueError("exclude_fact_ids cannot exceed 10000")
        if any(not isinstance(value, str) or not value for value in excluded):
            raise ValueError("exclude_fact_ids must contain non-empty strings")
        conn = self._connect()
        try:
            excluded_clause = ""
            params: list[object] = []
            if excluded:
                excluded_clause = (
                    f"AND f.fact_id NOT IN ({','.join('?' for _ in excluded)}) "
                )
                params.extend(excluded)
            params.append(limit)
            rows = conn.execute(
                "SELECT f.*,group_concat(s.turn_id) AS source_turn_ids "
                "FROM memory_facts f LEFT JOIN fact_sources s ON s.fact_id=f.fact_id "
                "WHERE f.projection_status IN ('pending','failed') "
                + excluded_clause
                + "GROUP BY f.fact_id ORDER BY CASE f.projection_status "
                "WHEN 'pending' THEN 0 ELSE 1 END,f.created_at,f.fact_id LIMIT ?",
                tuple(params),
            ).fetchall()
            return [self._fact(row) for row in rows]
        finally:
            conn.close()

    def mark_fact_projected(self, fact_id: str, core_memory_id: int) -> None:
        fact_id = _text(fact_id, "fact_id", maximum=200)
        if (
            not isinstance(core_memory_id, int)
            or isinstance(core_memory_id, bool)
            or core_memory_id <= 0
        ):
            raise ValueError("core_memory_id must be a positive integer")
        conn = self._connect()
        try:
            self._begin(conn)
            row = conn.execute(
                "SELECT projection_status,core_memory_id FROM memory_facts WHERE fact_id=?",
                (fact_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown fact: {fact_id}")
            if row["projection_status"] == "projected":
                if int(row["core_memory_id"]) == core_memory_id:
                    conn.commit()
                    return
                raise ValueError("projected fact cannot be rebound to a different Core memory")
            conn.execute(
                "UPDATE memory_facts SET projection_status='projected',core_memory_id=?,"
                "projection_error='' WHERE fact_id=?",
                (core_memory_id, fact_id),
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def mark_fact_projection_failed(self, fact_id: str, error: str) -> None:
        fact_id = _text(fact_id, "fact_id", maximum=200)
        error = _text(error, "projection error", maximum=10_000)
        conn = self._connect()
        try:
            self._begin(conn)
            row = conn.execute(
                "SELECT projection_status FROM memory_facts WHERE fact_id=?", (fact_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown fact: {fact_id}")
            if row["projection_status"] == "projected":
                raise ValueError("a projected fact cannot regress to failed")
            conn.execute(
                "UPDATE memory_facts SET projection_status='failed',projection_error=? "
                "WHERE fact_id=?",
                (error, fact_id),
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def store_fact_candidate(
        self,
        turn_id: str,
        content: str,
        *,
        evidence: str = "",
        importance: int = 5,
        confidence: float = 1.0,
        metadata: Mapping[str, Any] | None = None,
    ) -> FactWriteResult:
        candidate = FactCandidate(content, evidence, importance, confidence, metadata or {})
        conn = self._connect()
        try:
            self._begin(conn)
            row = self._turn(conn, turn_id)
            if row["status"] != "completed":
                raise ValueError("facts can only reference completed turns")
            result = self._store_fact(conn, row, candidate)
            conn.commit()
            return result
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def record_extraction(
        self,
        turn_id: str,
        status: str,
        facts: Sequence[FactCandidate | str] = (),
        error: str = "",
    ) -> ExtractionReceipt:
        if status not in {"success", "failure"}:
            raise ValueError("extraction status must be success or failure")
        candidates = tuple(self._candidate(value) for value in facts)
        error = error.strip()
        if status == "failure" and not error:
            raise ValueError("failed extraction requires an error")
        if status == "failure" and candidates:
            raise ValueError("failed extraction cannot persist facts")
        if status == "success" and error:
            raise ValueError("successful extraction cannot include an error")

        output = {
            "status": status,
            "facts": [asdict(value) for value in candidates],
            "error": error,
        }
        output_sha256 = _digest(output)

        conn = self._connect()
        try:
            self._begin(conn)
            turn = self._turn(conn, turn_id)
            extraction_input = self._input_from_row(turn)
            state = conn.execute(
                "SELECT status,attempts FROM extraction_state WHERE turn_id=?", (turn_id,)
            ).fetchone()
            if state["status"] == "success":
                existing = conn.execute(
                    "SELECT * FROM extraction_receipts WHERE turn_id=? "
                    "ORDER BY attempt DESC LIMIT 1",
                    (turn_id,),
                ).fetchone()
                if existing is not None and existing["output_sha256"] == output_sha256:
                    conn.commit()
                    return ExtractionReceipt(
                        existing["receipt_id"], existing["turn_id"],
                        int(existing["attempt"]), existing["status"],
                        existing["input_sha256"], existing["output_sha256"],
                        int(existing["submitted_facts"]), int(existing["new_facts"]),
                        existing["error"], existing["created_at"],
                    )
                raise ValueError("successful extraction cannot be rewritten")
            attempt = int(state["attempts"]) + 1
            writes = [self._store_fact(conn, turn, value) for value in candidates]
            receipt = ExtractionReceipt(
                receipt_id=str(uuid.uuid4()),
                turn_id=turn_id,
                attempt=attempt,
                status=status,
                input_sha256=extraction_input.input_sha256,
                output_sha256=output_sha256,
                submitted_facts=len(candidates),
                new_facts=sum(result.created for result in writes),
                error=error,
                created_at=_now(),
            )
            conn.execute(
                "INSERT INTO extraction_receipts VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    receipt.receipt_id, receipt.turn_id, receipt.attempt, receipt.status,
                    receipt.input_sha256, receipt.output_sha256, receipt.submitted_facts,
                    receipt.new_facts, receipt.error, receipt.created_at,
                ),
            )
            conn.execute(
                "UPDATE extraction_state SET status=?,attempts=?,updated_at=? WHERE turn_id=?",
                (status, attempt, receipt.created_at, turn_id),
            )
            conn.commit()
            return receipt
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def store_episode(
        self, session_id: str, summary: str, turn_ids: Sequence[str]
    ) -> Episode:
        session_id = _text(session_id, "session_id", maximum=200)
        summary = _text(summary, "summary")
        ids = tuple(dict.fromkeys(turn_ids))
        if not ids:
            raise ValueError("episode needs at least one turn")
        conn = self._connect()
        try:
            self._begin(conn)
            marks = ",".join("?" for _ in ids)
            rows = conn.execute(
                f"SELECT turn_id,session_id,status FROM conversation_turns WHERE turn_id IN ({marks})",
                ids,
            ).fetchall()
            if len(rows) != len(ids):
                raise KeyError("episode contains an unknown turn")
            if any(row["session_id"] != session_id for row in rows):
                raise ValueError("episode turns must belong to its session")
            if any(row["status"] != "completed" for row in rows):
                raise ValueError("episode can only summarize completed turns")
            # Episode identity is derived only from immutable conversation facts.
            # An LLM summary is presentation/content, never identity: retries may
            # legitimately produce different wording for the same completed turns.
            content_hash = _digest({"session_id": session_id, "turn_ids": ids})
            existing = conn.execute(
                "SELECT * FROM conversation_episodes WHERE content_sha256=?",
                (content_hash,),
            ).fetchone()
            if existing is None:
                # 0.1.3 included the nondeterministic LLM summary in its hash.
                # Adopt that row by the actual immutable identity instead of
                # creating a second episode during an in-place 0.1.4 upgrade.
                existing = conn.execute(
                    "SELECT * FROM conversation_episodes "
                    "WHERE session_id=? AND turn_ids_json=? "
                    "ORDER BY created_at,episode_id LIMIT 1",
                    (session_id, json.dumps(ids)),
                ).fetchone()
            if existing:
                episode = self._episode(existing)
            else:
                episode = Episode(
                    str(uuid.uuid5(uuid.NAMESPACE_URL, f"soul-web:episode:{content_hash}")),
                    session_id,
                    summary,
                    ids,
                    content_hash,
                    _now(),
                )
                conn.execute(
                    "INSERT INTO conversation_episodes VALUES(?,?,?,?,?,?)",
                    (
                        episode.episode_id, episode.session_id, episode.summary,
                        json.dumps(ids), episode.content_sha256, episode.created_at,
                    ),
                )
            conn.execute(
                "INSERT OR IGNORE INTO episode_projections(episode_id,status,updated_at) "
                "VALUES(?,'pending',?)",
                (episode.episode_id, _now()),
            )
            conn.commit()
            return episode
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _episode(row: sqlite3.Row) -> Episode:
        return Episode(
            row["episode_id"], row["session_id"], row["summary"],
            tuple(json.loads(row["turn_ids_json"])), row["content_sha256"], row["created_at"],
        )

    @staticmethod
    def _episode_projection(row: sqlite3.Row) -> EpisodeProjection:
        return EpisodeProjection(
            row["episode_id"], row["status"], row["core_memory_id"],
            row["error"], row["updated_at"],
        )

    def episode_projection(self, episode_id: str) -> EpisodeProjection:
        episode_id = _text(episode_id, "episode_id", maximum=200)
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM episode_projections WHERE episode_id=?", (episode_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown episode: {episode_id}")
            return self._episode_projection(row)
        finally:
            conn.close()

    def mark_episode_projected(self, episode_id: str, core_memory_id: int) -> None:
        episode_id = _text(episode_id, "episode_id", maximum=200)
        if (
            not isinstance(core_memory_id, int)
            or isinstance(core_memory_id, bool)
            or core_memory_id <= 0
        ):
            raise ValueError("core_memory_id must be a positive integer")
        conn = self._connect()
        try:
            self._begin(conn)
            row = conn.execute(
                "SELECT status,core_memory_id FROM episode_projections WHERE episode_id=?",
                (episode_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown episode: {episode_id}")
            if row["status"] == "projected":
                if int(row["core_memory_id"]) == core_memory_id:
                    conn.commit()
                    return
                raise ValueError("projected episode cannot be rebound")
            conn.execute(
                "UPDATE episode_projections SET status='projected',core_memory_id=?,"
                "error='',updated_at=? WHERE episode_id=?",
                (core_memory_id, _now(), episode_id),
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def mark_episode_projection_failed(self, episode_id: str, error: str) -> None:
        episode_id = _text(episode_id, "episode_id", maximum=200)
        error = _text(error, "episode projection error", maximum=10_000)
        conn = self._connect()
        try:
            self._begin(conn)
            row = conn.execute(
                "SELECT status FROM episode_projections WHERE episode_id=?", (episode_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown episode: {episode_id}")
            if row["status"] == "projected":
                raise ValueError("a projected episode cannot regress to failed")
            conn.execute(
                "UPDATE episode_projections SET status='failed',error=?,updated_at=? "
                "WHERE episode_id=?",
                (error, _now(), episode_id),
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def recent_turns(self, limit: int = 20, session_id: str | None = None) -> list[Turn]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        conn = self._connect()
        try:
            if session_id is None:
                rows = conn.execute(
                    "SELECT * FROM conversation_turns ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM conversation_turns WHERE session_id=? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (_text(session_id, "session_id", maximum=200), limit),
                ).fetchall()
            return [
                Turn(
                    row["turn_id"], row["session_id"], row["user_text"],
                    row["assistant_text"], row["status"], row["error"],
                    row["created_at"], row["completed_at"],
                )
                for row in rows
            ]
        finally:
            conn.close()

    def retryable_extractions(
        self,
        limit: int = 100,
        max_attempts: int = 3,
        exclude_turn_ids: Sequence[str] = (),
    ) -> list[ExtractionInput]:
        """Return completed turns whose durable extraction outbox needs work."""

        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        if (
            not isinstance(max_attempts, int)
            or isinstance(max_attempts, bool)
            or not 1 <= max_attempts <= 20
        ):
            raise ValueError("max_attempts must be between 1 and 20")
        excluded = tuple(dict.fromkeys(exclude_turn_ids))
        if len(excluded) > 10_000:
            raise ValueError("exclude_turn_ids cannot exceed 10000")
        if any(not isinstance(value, str) or not value for value in excluded):
            raise ValueError("exclude_turn_ids must contain non-empty strings")
        conn = self._connect()
        try:
            excluded_clause = ""
            params: list[object] = [max_attempts]
            if excluded:
                excluded_clause = (
                    f"AND t.turn_id NOT IN ({','.join('?' for _ in excluded)}) "
                )
                params.extend(excluded)
            params.append(limit)
            rows = conn.execute(
                "SELECT t.* FROM conversation_turns t "
                "JOIN extraction_state e ON e.turn_id=t.turn_id "
                "WHERE t.status='completed' AND e.status IN ('pending','failure') "
                "AND e.attempts < ? "
                + excluded_clause
                + "ORDER BY CASE e.status WHEN 'pending' THEN 0 ELSE 1 END,"
                "t.created_at,t.turn_id LIMIT ?",
                tuple(params),
            ).fetchall()
            return [self._input_from_row(row) for row in rows]
        finally:
            conn.close()

    def recent_episodes(
        self, limit: int = 20, session_id: str | None = None
    ) -> list[Episode]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        conn = self._connect()
        try:
            if session_id is None:
                rows = conn.execute(
                    "SELECT * FROM conversation_episodes ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM conversation_episodes WHERE session_id=? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (_text(session_id, "session_id", maximum=200), limit),
                ).fetchall()
            return [self._episode(row) for row in rows]
        finally:
            conn.close()

    def status_counts(self) -> dict[str, int]:
        conn = self._connect()
        try:
            result: dict[str, int] = {}
            for row in conn.execute(
                "SELECT status,count(*) AS n FROM conversation_turns GROUP BY status"
            ):
                result[f"turn:{row['status']}"] = int(row["n"])
            for row in conn.execute(
                "SELECT status,count(*) AS n FROM extraction_state GROUP BY status"
            ):
                result[f"extraction:{row['status']}"] = int(row["n"])
            result["facts"] = int(conn.execute("SELECT count(*) FROM memory_facts").fetchone()[0])
            result["episodes"] = int(
                conn.execute("SELECT count(*) FROM conversation_episodes").fetchone()[0]
            )
            for row in conn.execute(
                "SELECT status,count(*) AS n FROM episode_projections GROUP BY status"
            ):
                result[f"episode_projection:{row['status']}"] = int(row["n"])
            for row in conn.execute(
                "SELECT projection_status,count(*) AS n FROM memory_facts "
                "GROUP BY projection_status"
            ):
                result[f"projection:{row['projection_status']}"] = int(row["n"])
            return result
        finally:
            conn.close()
