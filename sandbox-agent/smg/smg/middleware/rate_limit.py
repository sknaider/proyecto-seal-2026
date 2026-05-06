"""Token-bucket rate limiter per agent.

Each agent has a refill rate (tokens/min) and a burst capacity. Unauthenticated
or unknown callers fall back to the global default bucket.
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass


@dataclass
class Bucket:
    capacity: int
    refill_per_sec: float
    tokens: float
    last_refill: float


class RateLimiter:
    def __init__(self, default_per_min: int, burst: int, per_agent: dict[str, int] | None = None):
        self.default_rate = default_per_min / 60.0
        self.default_burst = burst
        self.per_agent = per_agent or {}
        self._buckets: dict[str, Bucket] = defaultdict(self._new_bucket)

    def _new_bucket(self) -> Bucket:
        now = time.monotonic()
        return Bucket(
            capacity=self.default_burst,
            refill_per_sec=self.default_rate,
            tokens=float(self.default_burst),
            last_refill=now,
        )

    def _bucket(self, agent: str) -> Bucket:
        if agent in self._buckets:
            return self._buckets[agent]
        per_min = self.per_agent.get(agent)
        now = time.monotonic()
        if per_min:
            b = Bucket(
                capacity=self.default_burst,
                refill_per_sec=per_min / 60.0,
                tokens=float(self.default_burst),
                last_refill=now,
            )
        else:
            b = self._new_bucket()
        self._buckets[agent] = b
        return b

    def allow(self, agent: str, cost: float = 1.0) -> tuple[bool, float]:
        """Return (allowed, seconds_until_next_token)."""
        b = self._bucket(agent or "_anon")
        now = time.monotonic()
        elapsed = now - b.last_refill
        b.tokens = min(b.capacity, b.tokens + elapsed * b.refill_per_sec)
        b.last_refill = now
        if b.tokens >= cost:
            b.tokens -= cost
            return True, 0.0
        deficit = cost - b.tokens
        wait = deficit / b.refill_per_sec if b.refill_per_sec > 0 else 60.0
        return False, wait
