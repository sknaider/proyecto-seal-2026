"""Ed25519 authorization issuance and one-shot edge verification."""
from __future__ import annotations

import base64
import secrets
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .contracts import (
    ActionIntent,
    BoundedAuthorization,
    Decision,
    PolicyDecision,
    canonical_json,
)


@dataclass(frozen=True)
class SignedAuthorization:
    authorization: BoundedAuthorization
    key_id: str
    algorithm: str
    signature_b64: str


class InMemoryNonceStore:
    """Test/reference replay cache. Production adapters require durable storage."""

    def __init__(self) -> None:
        self._used: set[str] = set()

    def consume(self, nonce: str) -> bool:
        if nonce in self._used:
            return False
        self._used.add(nonce)
        return True


class NonceStore(Protocol):
    def consume(self, nonce: str) -> bool: ...


class SqliteNonceStore:
    """Atomic durable replay cache for the reference edge verifier."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        with sqlite3.connect(self.path) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS consumed_nonces (
                    nonce TEXT PRIMARY KEY,
                    consumed_at TEXT NOT NULL
                )
                """
            )

    def consume(self, nonce: str) -> bool:
        try:
            with sqlite3.connect(self.path, timeout=5.0) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO consumed_nonces(nonce,consumed_at) VALUES(?,?)",
                    (nonce, datetime.now().astimezone().isoformat()),
                )
            return True
        except sqlite3.IntegrityError:
            return False


class AuthorizationSigner:
    def __init__(self, private_key: Ed25519PrivateKey, key_id: str) -> None:
        self._key = private_key
        self.key_id = key_id

    @classmethod
    def generate(cls, key_id: str = "ser-dev-key") -> "AuthorizationSigner":
        return cls(Ed25519PrivateKey.generate(), key_id)

    def public_key_bytes(self) -> bytes:
        return self._key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    def issue(
        self,
        intent: ActionIntent,
        decision: PolicyDecision,
        *,
        now: datetime,
        ttl: timedelta = timedelta(seconds=5),
    ) -> SignedAuthorization:
        if decision.decision is not Decision.ALLOW or decision.effective_constraints is None:
            raise ValueError("denied policy decisions cannot be signed")
        expiry = min(intent.expires_at, now + ttl)
        authorization = BoundedAuthorization(
            authorization_id=str(uuid.uuid4()),
            intent_hash=intent.digest(),
            issuer=intent.issuer,
            body_id=intent.body_id,
            allowed_capability=intent.capability,
            effective_constraints=decision.effective_constraints,
            consent_receipts=decision.consent_ids,
            policy_version=decision.policy_version,
            not_before=now,
            expires_at=expiry,
            max_uses=1,
            nonce=secrets.token_urlsafe(24),
        )
        signature = self._key.sign(canonical_json(authorization))
        return SignedAuthorization(
            authorization=authorization,
            key_id=self.key_id,
            algorithm="Ed25519",
            signature_b64=base64.b64encode(signature).decode("ascii"),
        )


class AuthorizationVerifier:
    def __init__(
        self,
        trusted_keys: dict[str, bytes],
        nonce_store: NonceStore,
    ) -> None:
        self._trusted_keys = dict(trusted_keys)
        self._nonce_store = nonce_store

    def verify_and_consume(
        self,
        signed: SignedAuthorization,
        *,
        expected_body_id: str,
        expected_intent_hash: str,
        now: datetime,
    ) -> tuple[bool, str]:
        authorization = signed.authorization
        raw_key = self._trusted_keys.get(signed.key_id)
        if raw_key is None:
            return False, "unknown_key"
        if signed.algorithm != "Ed25519":
            return False, "unsupported_algorithm"
        try:
            signature = base64.b64decode(signed.signature_b64, validate=True)
            Ed25519PublicKey.from_public_bytes(raw_key).verify(
                signature, canonical_json(authorization)
            )
        except (ValueError, InvalidSignature):
            return False, "invalid_signature"
        if authorization.body_id != expected_body_id:
            return False, "wrong_body"
        if authorization.intent_hash != expected_intent_hash:
            return False, "wrong_intent"
        if not (authorization.not_before <= now < authorization.expires_at):
            return False, "authorization_not_current"
        if not self._nonce_store.consume(authorization.nonce):
            return False, "replay_detected"
        return True, "authorized"
