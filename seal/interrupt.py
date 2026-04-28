"""SEAL Interrupt System — CANCEL and QUEUE modes.

Three-mode interrupt system (mirrors Hermes busy_input_mode):

  STEER  — already in seal/steer.py. Injects a redirect mid-run without
            stopping the agent.  William whispers a direction.

  CANCEL — stops the agent immediately.  William sends type='interrupt'.
            chat_server writes /tmp/.{agent}_interrupt.json.
            The agent hook calls check_cancel() on each turn start;
            if flagged, raises SystemExit(0) for a clean shutdown.

  QUEUE  — queues a message for the next turn.  William sends type='queue'.
            chat_server writes /tmp/.{agent}_queue.json.
            The agent hook calls pop_queued() on each turn start;
            the returned message is injected as additional context so
            the agent sees it at the top of its next response.

All three are one-shot: consumed on first read, auto-deleted.

Usage — sending from William:
    POST /api/agents/send { from: 'William', to: 'ADA',
                            type: 'interrupt', message: '' }
    POST /api/agents/send { from: 'William', to: 'ADA',
                            type: 'queue',     message: 'también valida RUC' }

Usage — reading in agent hooks (UserPromptSubmit):
    from seal.interrupt import check_cancel, pop_queued

    check_cancel(agent)          # raises SystemExit(0) if flagged
    msg = pop_queued(agent)      # returns str or None

No external deps — stdlib only.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Optional

logger = logging.getLogger("seal.interrupt")

_DEFAULT_BASE = "http://localhost:8765"

# ── file-based fallback paths (chat_server writes these) ─────────────────────

def _cancel_path(agent: str) -> str:
    return f"/tmp/.{agent.lower()}_interrupt.json"


def _queue_path(agent: str) -> str:
    return f"/tmp/.{agent.lower()}_queue.json"


# ── CANCEL ────────────────────────────────────────────────────────────────────

def check_cancel(
    agent: str,
    *,
    base_url: str = _DEFAULT_BASE,
    timeout: float = 1.0,
) -> None:
    """Raise SystemExit(0) if William sent a CANCEL interrupt for *agent*.

    Checks the chat_server endpoint first; falls back to the local flag file
    if the server is unreachable.  The flag is consumed (deleted) on read so
    subsequent turns are unaffected.

    Raises:
        SystemExit(0) — clean shutdown when a CANCEL is pending.
    """
    if _fetch_interrupt(agent, base_url=base_url, timeout=timeout):
        logger.info("CANCEL interrupt received for %s — shutting down", agent)
        raise SystemExit(0)


def _fetch_interrupt(
    agent: str,
    *,
    base_url: str,
    timeout: float,
) -> bool:
    """Return True if a CANCEL is pending.  Consume it on read."""
    # 1. Try server endpoint
    url = f"{base_url}/api/agents/interrupt/{agent}"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode())
            return bool(body.get("interrupt"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            pass  # no interrupt pending — fall through to file check
        else:
            logger.debug("check_cancel HTTP %s for %s", exc.code, agent)
    except Exception:
        logger.debug("check_cancel server unreachable for %s", agent, exc_info=True)

    # 2. Fall back to flag file written directly by chat_server
    return _consume_flag_file(_cancel_path(agent))


def _consume_flag_file(path: str) -> bool:
    """Read and delete a flag file.  Returns True if it existed and was set."""
    import os
    try:
        with open(path) as f:
            data = json.load(f)
        os.unlink(path)
        return bool(data.get("interrupt", data.get("value", True)))
    except FileNotFoundError:
        return False
    except Exception:
        return False


# ── QUEUE ─────────────────────────────────────────────────────────────────────

def pop_queued(
    agent: str,
    *,
    base_url: str = _DEFAULT_BASE,
    timeout: float = 1.0,
) -> Optional[str]:
    """Return the queued message for *agent*, or None.

    Checks the chat_server endpoint first; falls back to the local queue file.
    The queue entry is consumed on read (one-shot).

    Returns:
        The queued message string, or None if nothing is queued.
    """
    msg = _fetch_queued(agent, base_url=base_url, timeout=timeout)
    if msg:
        logger.debug("QUEUE message popped for %s: %s", agent, msg[:80])
    return msg


def _fetch_queued(
    agent: str,
    *,
    base_url: str,
    timeout: float,
) -> Optional[str]:
    """Return queued message string or None.  Consume on read."""
    # 1. Try server endpoint
    url = f"{base_url}/api/agents/queue/{agent}"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode())
            if body.get("message"):
                return str(body["message"])
            return None
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            pass
        else:
            logger.debug("pop_queued HTTP %s for %s", exc.code, agent)
    except Exception:
        logger.debug("pop_queued server unreachable for %s", agent, exc_info=True)

    # 2. Fall back to queue file
    return _consume_queue_file(_queue_path(agent))


def _consume_queue_file(path: str) -> Optional[str]:
    """Read and delete a queue file.  Returns message string or None."""
    import os
    try:
        with open(path) as f:
            data = json.load(f)
        os.unlink(path)
        return data.get("message") or None
    except FileNotFoundError:
        return None
    except Exception:
        return None


# ── convenience: send via chat_server (for testing / scripting) ───────────────

def send_cancel(
    agent: str,
    *,
    from_: str = "William",
    base_url: str = _DEFAULT_BASE,
    timeout: float = 2.0,
) -> bool:
    """Send a CANCEL interrupt to *agent* via chat_server.

    Returns True if the server accepted it, False otherwise.
    (Tests and scripts can call this instead of crafting the POST manually.)
    """
    return _post_interrupt(
        {"from": from_, "to": agent, "type": "interrupt", "message": ""},
        base_url=base_url,
        timeout=timeout,
    )


def send_queue(
    agent: str,
    message: str,
    *,
    from_: str = "William",
    base_url: str = _DEFAULT_BASE,
    timeout: float = 2.0,
) -> bool:
    """Enqueue *message* for *agent*'s next turn via chat_server."""
    return _post_interrupt(
        {"from": from_, "to": agent, "type": "queue", "message": message},
        base_url=base_url,
        timeout=timeout,
    )


def _post_interrupt(
    payload: dict,
    *,
    base_url: str,
    timeout: float,
) -> bool:
    url = f"{base_url}/api/agents/send"
    try:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status < 400
    except Exception:
        return False
