"""Health monitor with circuit breaker semantics.

Tracks each backend's reachability and exposes a simple API:
- is_healthy(name): bool
- mark_success(name), mark_failure(name)
- monitor_loop(): periodic async task that pings endpoints

The circuit opens after `failure_threshold` consecutive failures and
reopens once a recovery check succeeds.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

import httpx

LOG = logging.getLogger("smg.health")


@dataclass
class BackendState:
    name: str
    consecutive_failures: int = 0
    last_check: float = 0.0
    last_ok: float = 0.0
    circuit_open: bool = False
    open_since: float = 0.0


class HealthMonitor:
    def __init__(
        self,
        backends: dict[str, str],
        failure_threshold: int = 3,
        recovery_check_s: int = 60,
        interval_s: int = 30,
    ):
        self.backends = backends  # name -> health URL
        self.failure_threshold = failure_threshold
        self.recovery_check_s = recovery_check_s
        self.interval_s = interval_s
        self.state: dict[str, BackendState] = {
            name: BackendState(name=name) for name in backends
        }

    def is_healthy(self, name: str) -> bool:
        s = self.state.get(name)
        if not s:
            return False
        return not s.circuit_open

    def mark_success(self, name: str) -> None:
        s = self.state[name]
        was_open = s.circuit_open
        s.consecutive_failures = 0
        s.last_ok = time.time()
        s.circuit_open = False
        s.open_since = 0.0
        if was_open:
            LOG.warning("Circuit CLOSED for backend=%s after recovery", name)

    def mark_failure(self, name: str) -> None:
        s = self.state[name]
        s.consecutive_failures += 1
        now = time.time()
        if (
            not s.circuit_open
            and s.consecutive_failures >= self.failure_threshold
        ):
            s.circuit_open = True
            s.open_since = now
            LOG.error(
                "Circuit OPEN for backend=%s after %d consecutive failures",
                name,
                s.consecutive_failures,
            )

    async def _probe(self, name: str, url: str, client: httpx.AsyncClient) -> bool:
        try:
            r = await client.get(url, timeout=5.0)
            return r.status_code < 500
        except Exception as e:
            LOG.debug("Health probe failed backend=%s err=%s", name, e)
            return False

    async def monitor_loop(self) -> None:
        async with httpx.AsyncClient() as client:
            while True:
                for name, url in self.backends.items():
                    s = self.state[name]
                    now = time.time()
                    # If circuit is open, only retry every recovery_check_s
                    if s.circuit_open and (now - s.open_since) < self.recovery_check_s:
                        continue
                    ok = await self._probe(name, url, client)
                    s.last_check = now
                    if ok:
                        self.mark_success(name)
                    else:
                        self.mark_failure(name)
                await asyncio.sleep(self.interval_s)

    def snapshot(self) -> dict:
        return {
            name: {
                "healthy": not s.circuit_open,
                "consecutive_failures": s.consecutive_failures,
                "last_ok_age_s": (
                    round(time.time() - s.last_ok, 1) if s.last_ok else None
                ),
            }
            for name, s in self.state.items()
        }
