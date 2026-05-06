"""SEAL SOUL — Async utilities.

Extracted from mcp_server_v3.py (Wave 2).
Used by: mcp_server_v3.py, sleep_gate_cron.py

Includes:
- _fire_and_forget(): Schedule a coroutine with hard timeout, no orphan tasks.
"""
from __future__ import annotations

import asyncio
import logging

LOG = logging.getLogger("seal-memory.async")


def fire_and_forget(coro, timeout: float = 12.0) -> asyncio.Task:
    """Schedule a coroutine fire-and-forget with a hard timeout.

    Replaces bare asyncio.ensure_future() for background tasks.
    Prevents orphan coroutines in multi-tenant scenarios where the
    background task (embedding generation, DB write) may hang indefinitely.

    Args:
        coro: Coroutine to run in background.
        timeout: Maximum seconds to allow before cancellation (default 12s).

    Returns:
        The created Task (can be ignored by caller).
    """
    async def _guarded():
        try:
            await asyncio.wait_for(coro, timeout=timeout)
        except asyncio.TimeoutError:
            LOG.debug("[fire-and-forget] timeout after %ss — coroutine cancelled", timeout)
        except Exception as exc:
            LOG.debug("[fire-and-forget] suppressed: %s", exc)

    return asyncio.ensure_future(_guarded())


# Legacy alias — keeps mcp_server_v3.py working without changes
_fire_and_forget = fire_and_forget
