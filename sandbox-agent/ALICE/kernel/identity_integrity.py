"""ALICE identity_integrity — anti-impersonation guard for cortex outputs.

ALICE variant adapted from NEXUS. Same approach (regex anti-impersonation),
different agent_id and log path. Ensures ALICE's analyst replies never
prefix with another agent's name.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AGENT_ID = "ALICE"
VIOLATION_LOG = Path("/tmp/alice_identity_violations.jsonl")

OTHER_AGENTS = ("ADA", "JARVIS", "NEXUS", "SPECTRE", "DUM", "William", "Kinger", "Henry")

_PATTERNS = [
    re.compile(rf"^\s*\[(?:{'|'.join(OTHER_AGENTS)})\]", re.IGNORECASE),
    re.compile(rf"^\s*(?:{'|'.join(OTHER_AGENTS)})\s*(?:dice|responde|piensa|escribe|aquí|here|says)?\s*[:>]", re.IGNORECASE),
    re.compile(rf"^\s*(?:{'|'.join(OTHER_AGENTS)})\s*,", re.IGNORECASE),
    re.compile(rf"^\s*(?:Como|Como soy|I am|Soy|Yo soy|As)\s+(?:{'|'.join(OTHER_AGENTS)})\b", re.IGNORECASE),
]


class IdentityViolation(Exception):
    def __init__(self, response_excerpt: str, matched_pattern: str):
        self.response_excerpt = response_excerpt
        self.matched_pattern = matched_pattern
        super().__init__(
            f"ALICE response would impersonate another agent. "
            f"Matched: {matched_pattern!r}. Excerpt: {response_excerpt[:200]!r}"
        )


def _log_violation(response: str, matched_pattern: str, source_tier: str = "unknown") -> None:
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
        print(f"[ALICE/identity] violation log write failed: {ex}", flush=True)


def validate_response_identity(response: str, source_tier: str = "unknown") -> str:
    if not response or not response.strip():
        return response

    for pat in _PATTERNS:
        if pat.match(response):
            _log_violation(response, pat.pattern, source_tier)
            print(
                f"[ALICE/identity] ⚠️ impersonation blocked — pattern={pat.pattern[:60]!r} "
                f"tier={source_tier} excerpt={response[:80]!r}",
                flush=True,
            )
            raise IdentityViolation(response[:300], pat.pattern)

    return response


def sanitize_response(response: str) -> str:
    if not response or not response.strip():
        return response
    cleaned = response
    for pat in _PATTERNS:
        cleaned = pat.sub("", cleaned, count=1).lstrip(" ,:.\n")
    return cleaned


def get_violation_count(window_hours: int = 24) -> int:
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
