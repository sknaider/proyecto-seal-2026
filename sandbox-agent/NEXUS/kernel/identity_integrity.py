"""NEXUS identity_integrity — anti-impersonation guard for cortex outputs.

NEXUS variant of SPECTRE's identity_integrity.py (§3.6 anti-impersonation).
Simpler than SPECTRE: NEXUS doesn't need Ed25519 boot keys (those are SPECTRE's
guardian role). NEXUS only needs to ensure its LLM responses NEVER impersonate
other team agents.

Threat model:
  - Multi-tier LLM (T1 Claude → T2 OpenCode → T3 Ollama). A downstream tier
    could emit a response that starts with "[ADA]", "JARVIS responde:", etc.
  - Without this guard, that response posts to webchat as if from NEXUS but
    pretending to be ADA — confusing William and the team.

Approach:
  1. After cortex receives LLM response, run validate_response_identity()
  2. If response starts with another agent's name/prefix → IdentityViolation
  3. Log violation to /tmp/nexus_identity_violations.jsonl for audit
  4. Cortex catches and either retries (clean response) or skips emit

Reference: NEXUS audit 2026-05-04 §4 RIESGO CRÍTICO 1
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AGENT_ID = "NEXUS"
VIOLATION_LOG = Path("/tmp/nexus_identity_violations.jsonl")

# Agents NEXUS must NEVER respond as
OTHER_AGENTS = ("ADA", "JARVIS", "ALICE", "SPECTRE", "DUM", "William", "Kinger", "Henry")

# Regex patterns that indicate impersonation
_PATTERNS = [
    # "[ADA] hola..." or "[JARVIS] ..."
    re.compile(rf"^\s*\[(?:{'|'.join(OTHER_AGENTS)})\]", re.IGNORECASE),
    # "ADA: hola..." or "JARVIS responde: ..."
    re.compile(rf"^\s*(?:{'|'.join(OTHER_AGENTS)})\s*(?:dice|responde|piensa|escribe|aquí|here|says)?\s*[:>]", re.IGNORECASE),
    # First word is another agent's name with a comma
    re.compile(rf"^\s*(?:{'|'.join(OTHER_AGENTS)})\s*,", re.IGNORECASE),
    # "Como ADA voy a..." / "I am JARVIS"
    re.compile(rf"^\s*(?:Como|Como soy|I am|Soy|Yo soy|As)\s+(?:{'|'.join(OTHER_AGENTS)})\b", re.IGNORECASE),
]


class IdentityViolation(Exception):
    """Raised when LLM response attempts to impersonate another agent."""

    def __init__(self, response_excerpt: str, matched_pattern: str):
        self.response_excerpt = response_excerpt
        self.matched_pattern = matched_pattern
        super().__init__(
            f"NEXUS response would impersonate another agent. "
            f"Matched: {matched_pattern!r}. "
            f"Excerpt: {response_excerpt[:200]!r}"
        )


def _log_violation(response: str, matched_pattern: str, source_tier: str = "unknown") -> None:
    """Append violation to /tmp/nexus_identity_violations.jsonl."""
    entry = {
        "agent": AGENT_ID,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "matched_pattern": matched_pattern,
        "source_tier": source_tier,
        "response_first_300": response[:300],
        "response_length": len(response),
    }
    try:
        with VIOLATION_LOG.open("a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as ex:
        print(f"[NEXUS/identity] violation log write failed: {ex}", flush=True)


def validate_response_identity(response: str, source_tier: str = "unknown") -> str:
    """Validate that response does NOT impersonate another agent.

    Args:
        response: raw LLM response string about to be posted to webchat.
        source_tier: which LLM tier produced it ("T1", "T2", "T3", "unknown").

    Returns:
        The response unchanged if valid.

    Raises:
        IdentityViolation: if response starts with another agent's name/prefix.
    """
    if not response or not response.strip():
        return response

    for pat in _PATTERNS:
        m = pat.match(response)
        if m:
            _log_violation(response, pat.pattern, source_tier)
            print(
                f"[NEXUS/identity] ⚠️ impersonation blocked — pattern={pat.pattern[:60]!r} "
                f"tier={source_tier} excerpt={response[:80]!r}",
                flush=True,
            )
            raise IdentityViolation(response[:300], pat.pattern)

    return response


def sanitize_response(response: str) -> str:
    """Best-effort sanitization: strip impersonation prefixes if present.

    Use as fallback when raising IdentityViolation would block useful output.
    Returns response with leading agent prefix removed (best effort).
    """
    if not response or not response.strip():
        return response

    cleaned = response
    for pat in _PATTERNS:
        cleaned = pat.sub("", cleaned, count=1).lstrip(" ,:.\n")

    return cleaned


def get_violation_count(window_hours: int = 24) -> int:
    """Return number of violations logged in the last N hours."""
    if not VIOLATION_LOG.exists():
        return 0
    cutoff = time.time() - window_hours * 3600
    count = 0
    try:
        for line in VIOLATION_LOG.read_text().splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                ts = entry.get("timestamp", "")
                if ts:
                    entry_time = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                    if entry_time >= cutoff:
                        count += 1
            except Exception:
                continue
    except Exception:
        pass
    return count
