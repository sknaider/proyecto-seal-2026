"""ALICE assumption_tracker — versioned ledger of analytical assumptions.

Every analysis I publish carries explicit, dated, sourced assumptions.
When an assumption is invalidated, every dependent analysis is flagged.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_BASE = Path(__file__).parent.parent
LEDGER_PATH = _BASE / "state" / "assumptions.jsonl"


def _append(entry: dict[str, Any]) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER_PATH.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def record(
    statement: str,
    source: str,
    confidence: float = 0.7,
    used_in: list[str] | None = None,
    expires_at: str | None = None,
) -> str:
    aid = uuid.uuid4().hex[:12]
    _append({
        "_kind": "create",
        "id": aid,
        "statement": statement,
        "source": source,
        "confidence": round(float(confidence), 3),
        "used_in": used_in or [],
        "expires_at": expires_at,
        "status": "active",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return aid


def invalidate(assumption_id: str, reason: str) -> bool:
    _append({
        "_kind": "invalidate",
        "id": assumption_id,
        "reason": reason,
        "invalidated_at": datetime.now(timezone.utc).isoformat(),
    })
    return True


def link(assumption_id: str, analysis_id: str) -> bool:
    _append({
        "_kind": "link",
        "id": assumption_id,
        "analysis_id": analysis_id,
        "ts": datetime.now(timezone.utc).isoformat(),
    })
    return True


def _load_merged() -> dict[str, dict]:
    merged: dict[str, dict] = {}
    if not LEDGER_PATH.exists():
        return merged
    for line in LEDGER_PATH.read_text().splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except Exception:
            continue
        kind = entry.get("_kind")
        aid = entry.get("id")
        if not aid:
            continue
        if kind == "create":
            merged[aid] = entry
        elif kind == "invalidate" and aid in merged:
            merged[aid]["status"] = "invalidated"
            merged[aid]["invalidated_at"] = entry.get("invalidated_at")
            merged[aid]["invalidation_reason"] = entry.get("reason")
        elif kind == "link" and aid in merged:
            merged[aid].setdefault("used_in", []).append(entry.get("analysis_id"))
    return merged


def list_active() -> list[dict]:
    return [a for a in _load_merged().values() if a.get("status") == "active"]


def affected_analyses(assumption_id: str) -> list[str]:
    a = _load_merged().get(assumption_id, {})
    return a.get("used_in", [])


def stats() -> dict:
    merged = _load_merged()
    active = sum(1 for a in merged.values() if a.get("status") == "active")
    return {
        "total": len(merged),
        "active": active,
        "invalidated": len(merged) - active,
    }
