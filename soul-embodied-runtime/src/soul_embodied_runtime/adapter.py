"""Vendor-neutral body adapter protocol and safe fake implementation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from .authorization import AuthorizationVerifier, SignedAuthorization


class BodyAdapter(Protocol):
    def discover(self) -> dict[str, Any]: ...
    def prepare(
        self,
        authorization: SignedAuthorization,
        *,
        expected_intent_hash: str,
        now: datetime,
    ) -> str: ...
    def execute(self, prepared_action: str) -> str: ...
    def cancel(self, action_handle: str, reason: str) -> dict[str, Any]: ...
    def safe_state(self, reason: str) -> dict[str, Any]: ...


@dataclass
class FakeBodyAdapter:
    body_id: str
    verifier: AuthorizationVerifier
    safe: bool = True

    def __post_init__(self) -> None:
        self._prepared: dict[str, SignedAuthorization] = {}
        self._executed: set[str] = set()
        self._cancelled: set[str] = set()
        self.events: list[dict[str, Any]] = []

    def discover(self) -> dict[str, Any]:
        return {"body_id": self.body_id, "kind": "fake", "safe": self.safe}

    def prepare(
        self,
        authorization: SignedAuthorization,
        *,
        expected_intent_hash: str,
        now: datetime,
    ) -> str:
        if not self.safe:
            raise RuntimeError("body is in safe state")
        allowed, reason = self.verifier.verify_and_consume(
            authorization,
            expected_body_id=self.body_id,
            expected_intent_hash=expected_intent_hash,
            now=now,
        )
        if not allowed:
            raise PermissionError(f"authorization rejected: {reason}")
        handle = "prepared:" + authorization.authorization.authorization_id
        self._prepared[handle] = authorization
        self.events.append({"type": "prepared", "handle": handle})
        return handle

    def execute(self, prepared_action: str) -> str:
        if not self.safe:
            raise RuntimeError("body is in safe state")
        if prepared_action not in self._prepared:
            raise ValueError("unknown prepared action")
        if prepared_action in self._cancelled:
            raise RuntimeError("prepared action was cancelled")
        if prepared_action in self._executed:
            raise RuntimeError("prepared action already executed")
        self._executed.add(prepared_action)
        handle = prepared_action.replace("prepared:", "action:", 1)
        self.events.append({"type": "executed", "handle": handle})
        return handle

    def cancel(self, action_handle: str, reason: str) -> dict[str, Any]:
        prepared_handle = action_handle.replace("action:", "prepared:", 1)
        if prepared_handle in self._prepared:
            self._cancelled.add(prepared_handle)
        event = {"type": "cancelled", "handle": action_handle, "reason": reason}
        self.events.append(event)
        return event

    def safe_state(self, reason: str) -> dict[str, Any]:
        self.safe = False
        event = {"type": "safe_state", "reason": reason}
        self.events.append(event)
        return event
