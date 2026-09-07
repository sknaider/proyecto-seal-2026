"""Steer check helper for SEAL agents.

Usage at agent boot:

    from seal.steer import check_steer

    steer = check_steer(agent="ADA")
    if steer:
        # incorporate steer['message'] into current turn context
        ...

check_steer() hits the chat_server steer endpoint once and returns the
payload if a steer is waiting, or None if there is none.  The server
marks the steer consumed on read (one-shot).

No external deps — urllib + stdlib only.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

logger = logging.getLogger("seal.steer")

_DEFAULT_BASE = "http://localhost:8765"


def check_steer(
    agent: str,
    *,
    base_url: str = _DEFAULT_BASE,
    timeout: float = 2.0,
) -> Optional[Dict[str, Any]]:
    """Return the pending steer for *agent*, or None.

    Args:
        agent:    Agent name as registered in chat_server (e.g. "ADA").
        base_url: Base URL of the SEAL chat server.
        timeout:  HTTP timeout in seconds — kept short so a dead server
                  never stalls agent boot.

    Returns:
        A dict with keys ``from``, ``message``, ``timestamp`` when a
        steer is waiting, or ``None`` when there is none or on any error.
    """
    url = f"{base_url}/api/agents/steer/{agent}"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode())
            if body.get("steer"):
                return body["steer"]
            return None
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        logger.debug("check_steer HTTP %s for agent %s", exc.code, agent)
        return None
    except Exception:
        logger.debug("check_steer unavailable for agent %s", agent, exc_info=True)
        return None
