"""Local append-only consent ledger for the Core 0.1 reference runtime."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .contracts import ConsentGrant, canonical_json


GENESIS_HASH = "sha256:" + "0" * 64
EVENT_TYPES = {"granted", "paused", "resumed", "revoked", "expired"}


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass(frozen=True)
class ConsentEvent:
    event_id: str
    consent_id: str
    event_type: str
    subject_id: str
    body_id: str
    mode: str
    capabilities: tuple[str, ...]
    purposes: tuple[str, ...]
    data_categories: tuple[str, ...]
    controller: str
    jurisdiction: str
    effective_at: datetime
    expires_at: datetime
    recorded_at: datetime
    withdrawal_method: str
    previous_event_hash: str
    event_hash: str


class ConsentLedger:
    """Append-only state transitions with a per-ledger hash chain."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;
                CREATE TABLE IF NOT EXISTS consent_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    consent_id TEXT NOT NULL,
                    event_type TEXT NOT NULL CHECK(event_type IN ('granted','paused','resumed','revoked','expired')),
                    subject_id TEXT NOT NULL,
                    body_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    capabilities_json TEXT NOT NULL,
                    purposes_json TEXT NOT NULL,
                    data_categories_json TEXT NOT NULL,
                    controller TEXT NOT NULL,
                    jurisdiction TEXT NOT NULL,
                    effective_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    withdrawal_method TEXT NOT NULL,
                    previous_event_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL UNIQUE
                );
                CREATE INDEX IF NOT EXISTS consent_events_consent_sequence
                    ON consent_events(consent_id, sequence DESC);
                CREATE TRIGGER IF NOT EXISTS consent_events_no_update
                BEFORE UPDATE ON consent_events
                BEGIN
                    SELECT RAISE(ABORT, 'consent_events is append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS consent_events_no_delete
                BEFORE DELETE ON consent_events
                BEGIN
                    SELECT RAISE(ABORT, 'consent_events is append-only');
                END;
                """
            )

    def grant(
        self,
        *,
        subject_id: str,
        body_id: str,
        mode: str,
        capabilities: set[str] | frozenset[str],
        purposes: set[str] | frozenset[str],
        data_categories: set[str] | frozenset[str],
        controller: str,
        jurisdiction: str,
        effective_at: datetime,
        expires_at: datetime,
        withdrawal_method: str,
        recorded_at: datetime | None = None,
    ) -> ConsentEvent:
        if expires_at <= effective_at:
            raise ValueError("consent expiry must follow effective_at")
        if not capabilities or not purposes:
            raise ValueError("capabilities and purposes must be explicit")
        return self._append(
            consent_id=str(uuid.uuid4()),
            event_type="granted",
            subject_id=subject_id,
            body_id=body_id,
            mode=mode,
            capabilities=tuple(sorted(capabilities)),
            purposes=tuple(sorted(purposes)),
            data_categories=tuple(sorted(data_categories)),
            controller=controller,
            jurisdiction=jurisdiction,
            effective_at=effective_at,
            expires_at=expires_at,
            withdrawal_method=withdrawal_method,
            recorded_at=recorded_at or datetime.now(timezone.utc),
        )

    def transition(
        self,
        consent_id: str,
        event_type: str,
        *,
        recorded_at: datetime | None = None,
        withdrawal_method: str = "user_request",
    ) -> ConsentEvent:
        if event_type not in EVENT_TYPES - {"granted"}:
            raise ValueError("invalid consent transition")
        previous = self.latest(consent_id)
        if previous is None:
            raise KeyError("consent not found")
        allowed = {
            "granted": {"paused", "revoked", "expired"},
            "paused": {"resumed", "revoked", "expired"},
            "resumed": {"paused", "revoked", "expired"},
            "revoked": set(),
            "expired": set(),
        }
        if event_type not in allowed[previous.event_type]:
            raise ValueError(f"transition {previous.event_type}->{event_type} is forbidden")
        return self._append(
            consent_id=consent_id,
            event_type=event_type,
            subject_id=previous.subject_id,
            body_id=previous.body_id,
            mode=previous.mode,
            capabilities=previous.capabilities,
            purposes=previous.purposes,
            data_categories=previous.data_categories,
            controller=previous.controller,
            jurisdiction=previous.jurisdiction,
            effective_at=previous.effective_at,
            expires_at=previous.expires_at,
            withdrawal_method=withdrawal_method,
            recorded_at=recorded_at or datetime.now(timezone.utc),
        )

    def latest(self, consent_id: str) -> ConsentEvent | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM consent_events WHERE consent_id=? ORDER BY sequence DESC LIMIT 1",
                (consent_id,),
            ).fetchone()
        return self._from_row(row) if row else None

    def active_grants(
        self,
        *,
        subject_id: str,
        body_id: str,
        mode: str,
        now: datetime,
    ) -> tuple[ConsentGrant, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT e.* FROM consent_events e
                JOIN (
                    SELECT consent_id, MAX(sequence) AS max_sequence
                    FROM consent_events GROUP BY consent_id
                ) latest ON latest.max_sequence=e.sequence
                WHERE e.subject_id=? AND e.body_id=? AND e.mode=?
                  AND e.event_type IN ('granted','resumed')
                ORDER BY e.sequence
                """,
                (subject_id, body_id, mode),
            ).fetchall()
        grants: list[ConsentGrant] = []
        for row in rows:
            event = self._from_row(row)
            if event.effective_at <= now < event.expires_at:
                grants.append(
                    ConsentGrant(
                        consent_id=event.consent_id,
                        subject_id=event.subject_id,
                        body_id=event.body_id,
                        mode=event.mode,
                        capabilities=frozenset(event.capabilities),
                        granted_at=event.effective_at,
                        expires_at=event.expires_at,
                        purposes=frozenset(event.purposes),
                        data_categories=frozenset(event.data_categories),
                    )
                )
        return tuple(grants)

    def verify_chain(self) -> bool:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM consent_events ORDER BY sequence"
            ).fetchall()
        previous = GENESIS_HASH
        for row in rows:
            event = self._from_row(row)
            if event.previous_event_hash != previous:
                return False
            if self._hash_payload(self._payload(event, include_hash=False)) != event.event_hash:
                return False
            previous = event.event_hash
        return True

    def _append(self, **values) -> ConsentEvent:
        for required in (
            "subject_id", "body_id", "mode", "controller", "jurisdiction", "withdrawal_method"
        ):
            if not str(values[required]).strip():
                raise ValueError(f"{required} is required")
        event_id = str(uuid.uuid4())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT event_hash FROM consent_events ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            previous_hash = row[0] if row else GENESIS_HASH
            event = ConsentEvent(
                event_id=event_id,
                previous_event_hash=previous_hash,
                event_hash="",
                **values,
            )
            event = ConsentEvent(
                **{**event.__dict__, "event_hash": self._hash_payload(self._payload(event, include_hash=False))}
            )
            connection.execute(
                """
                INSERT INTO consent_events(
                    event_id,consent_id,event_type,subject_id,body_id,mode,
                    capabilities_json,purposes_json,data_categories_json,
                    controller,jurisdiction,effective_at,expires_at,recorded_at,
                    withdrawal_method,previous_event_hash,event_hash
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event.event_id,
                    event.consent_id,
                    event.event_type,
                    event.subject_id,
                    event.body_id,
                    event.mode,
                    json.dumps(event.capabilities, separators=(",", ":")),
                    json.dumps(event.purposes, separators=(",", ":")),
                    json.dumps(event.data_categories, separators=(",", ":")),
                    event.controller,
                    event.jurisdiction,
                    _iso(event.effective_at),
                    _iso(event.expires_at),
                    _iso(event.recorded_at),
                    event.withdrawal_method,
                    event.previous_event_hash,
                    event.event_hash,
                ),
            )
        return event

    @staticmethod
    def _payload(event: ConsentEvent, *, include_hash: bool) -> dict:
        payload = {
            "event_id": event.event_id,
            "consent_id": event.consent_id,
            "event_type": event.event_type,
            "subject_id": event.subject_id,
            "body_id": event.body_id,
            "mode": event.mode,
            "capabilities": event.capabilities,
            "purposes": event.purposes,
            "data_categories": event.data_categories,
            "controller": event.controller,
            "jurisdiction": event.jurisdiction,
            "effective_at": event.effective_at,
            "expires_at": event.expires_at,
            "recorded_at": event.recorded_at,
            "withdrawal_method": event.withdrawal_method,
            "previous_event_hash": event.previous_event_hash,
        }
        if include_hash:
            payload["event_hash"] = event.event_hash
        return payload

    @staticmethod
    def _hash_payload(payload: dict) -> str:
        return "sha256:" + hashlib.sha256(canonical_json(payload)).hexdigest()

    @staticmethod
    def _from_row(row: sqlite3.Row) -> ConsentEvent:
        return ConsentEvent(
            event_id=row["event_id"],
            consent_id=row["consent_id"],
            event_type=row["event_type"],
            subject_id=row["subject_id"],
            body_id=row["body_id"],
            mode=row["mode"],
            capabilities=tuple(json.loads(row["capabilities_json"])),
            purposes=tuple(json.loads(row["purposes_json"])),
            data_categories=tuple(json.loads(row["data_categories_json"])),
            controller=row["controller"],
            jurisdiction=row["jurisdiction"],
            effective_at=_dt(row["effective_at"]),
            expires_at=_dt(row["expires_at"]),
            recorded_at=_dt(row["recorded_at"]),
            withdrawal_method=row["withdrawal_method"],
            previous_event_hash=row["previous_event_hash"],
            event_hash=row["event_hash"],
        )
