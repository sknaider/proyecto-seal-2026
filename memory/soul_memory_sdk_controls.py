"""Small, dependency-free external controls for the SOUL Memory SDK.

The controls deliberately keep no raw credentials, request bodies, queries, or
network addresses.  They are process-local so a single worker remains safe
without requiring Redis; deployments with multiple workers must still enforce
an aggregate limit at the edge.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


audit_logger = logging.getLogger("soul_memory_sdk.audit")


def credential_fingerprint(secret: str | None) -> str:
    """Return a non-reversible stable identifier; never return the input."""
    if not secret:
        return "missing"
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int
    reset_after: int

    def headers(self) -> dict[str, str]:
        headers = {
            "RateLimit-Limit": str(self.limit),
            "RateLimit-Remaining": str(self.remaining),
            "RateLimit-Reset": str(self.reset_after),
        }
        if not self.allowed:
            headers["Retry-After"] = str(self.retry_after)
        return headers


class SlidingWindowRateLimiter:
    """Thread-safe exact sliding-window limiter with bounded stale state."""

    def __init__(self, limit: int, window_seconds: float, *, max_keys: int = 10_000) -> None:
        if limit < 1 or window_seconds <= 0 or max_keys < 1:
            raise ValueError("invalid_rate_limit_configuration")
        self.limit = int(limit)
        self.window_seconds = float(window_seconds)
        self.max_keys = int(max_keys)
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._last_seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def check(self, key: str, *, now: float | None = None) -> RateLimitDecision:
        current = time.monotonic() if now is None else float(now)
        cutoff = current - self.window_seconds
        with self._lock:
            bucket = self._hits[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self.limit:
                retry = max(1, math.ceil(bucket[0] + self.window_seconds - current))
                self._last_seen[key] = current
                return RateLimitDecision(False, self.limit, 0, retry, retry)
            bucket.append(current)
            self._last_seen[key] = current
            if len(self._hits) > self.max_keys:
                stale = sorted(self._last_seen, key=self._last_seen.get)[: len(self._hits) - self.max_keys]
                for stale_key in stale:
                    if stale_key != key:
                        self._hits.pop(stale_key, None)
                        self._last_seen.pop(stale_key, None)
            remaining = self.limit - len(bucket)
            reset = max(1, math.ceil(bucket[0] + self.window_seconds - current))
            return RateLimitDecision(True, self.limit, remaining, 0, reset)

    def clear(self) -> None:
        with self._lock:
            self._hits.clear()
            self._last_seen.clear()


class SafeMeter:
    """Bounded in-process audit trail plus structured logs of allowlisted fields."""

    _ALLOWED = {
        "event",
        "operation",
        "status",
        "status_code",
        "tenant_id",
        "api_key_hash",
        "duration_ms",
        "reason",
    }

    def __init__(self, max_events: int = 2_000) -> None:
        self._events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self._lock = threading.Lock()

    def emit(self, **fields: Any) -> dict[str, Any]:
        event = {key: value for key, value in fields.items() if key in self._ALLOWED and value is not None}
        event["timestamp"] = datetime.now(UTC).isoformat()
        with self._lock:
            self._events.append(event)
        audit_logger.info(json.dumps(event, sort_keys=True, separators=(",", ":")))
        return event

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(event) for event in self._events]

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


meter = SafeMeter()
