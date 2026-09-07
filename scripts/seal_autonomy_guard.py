#!/usr/bin/env python3
"""Non-blocking lint for accidental permission requests to William."""

from __future__ import annotations

import re


APPROVAL_GATES = (
    "destructive",
    "external_commitment",
    "scope_change",
    "human_only",
)

_PERMISSION_REQUESTS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\besperando\s+(?:tu\s+)?(?:ok|luz\s+verde|autorizaci[oó]n|aprobaci[oó]n)\b",
        r"\bsi\s+(?:me|nos)\s+das\s+(?:luz\s+verde|permiso|tu\s+ok)\b",
        r"\b(?:me|nos)\s+autorizas\b",
        r"\bnecesito\s+(?:tu\s+)?(?:autorizaci[oó]n|aprobaci[oó]n|luz\s+verde)\b",
        r"\b(?:puedo|podemos)\s+(?:proceder|avanzar|ejecutar|hacerlo)\??",
    )
)


def autonomy_warning(
    to_agent: str,
    message: str,
    approval_gate: str | None = None,
) -> str | None:
    """Return a warning for permission-seeking language outside a real gate."""

    if to_agent.casefold() != "william" or approval_gate:
        return None
    normalized = " ".join(message.split())
    if any(pattern.search(normalized) for pattern in _PERMISSION_REQUESTS):
        return (
            "permission request detected without --approval-gate; "
            "execute reversible in-scope work and report evidence"
        )
    return None
