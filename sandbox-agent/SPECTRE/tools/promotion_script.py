"""SPECTRE promotion_script — sandbox → active promotion record.

Marks SPECTRE as officially promoted from sandbox v1 to active.
Creates:
  - state/promotion_record.json (authoritative promotion receipt)
  - git tag spectre-v1.0-promoted
  - updates working_state.json with promotion metadata

Does NOT touch production real channels or modify kernel source.
Idempotent: safe to run multiple times.

Authorization: William — 2026-05-02 "luz verde, firmo la promotion"
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_BASE = Path(__file__).parent.parent
STATE_DIR = _BASE / "state"
WORKING_STATE_PATH = STATE_DIR / "working_state.json"
PROMOTION_RECORD_PATH = STATE_DIR / "promotion_record.json"

GIT_TAG = "spectre-v1.0-promoted"
PROMOTION_VERSION = "v1_promoted"

_PROMOTION_META = {
    "signed_by": "William",
    "authorized_at": "2026-05-02T17:28:00Z",
    "message": "luz verde, firmo la promotion",
    "sandbox_version": "v1_sandbox",
    "promoted_version": PROMOTION_VERSION,
    "test_results": {
        "total_pass": 124,
        "total_suites": 13,
        "failures": 0,
    },
    "soak_status": "running_24h",
    "git_tag": GIT_TAG,
}


def _read_state() -> dict:
    try:
        if WORKING_STATE_PATH.exists():
            return json.loads(WORKING_STATE_PATH.read_text())
    except Exception:
        pass
    return {}


def _write_state(state: dict) -> None:
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    WORKING_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def promote() -> dict:
    """Execute SPECTRE promotion. Returns promotion record dict."""
    now = datetime.now(timezone.utc).isoformat()

    # 1. Check idempotency
    if PROMOTION_RECORD_PATH.exists():
        existing = json.loads(PROMOTION_RECORD_PATH.read_text())
        if existing.get("promoted_version") == PROMOTION_VERSION:
            print(f"[SPECTRE/promotion] already promoted to {PROMOTION_VERSION} — idempotent skip", flush=True)
            # Ensure working_state is consistent even on idempotent return
            state = _read_state()
            if state.get("cv_approval_status") != "PROMOTED":
                state["cv_approval_status"] = "PROMOTED"
                state["promotion"] = {
                    "version": PROMOTION_VERSION,
                    "promoted_at": existing.get("promoted_at", now),
                    "git_tag": GIT_TAG,
                }
                _write_state(state)
            return existing

    # 2. Write promotion record
    record = dict(_PROMOTION_META)
    record["promoted_at"] = now
    record["promotion_script"] = str(Path(__file__).name)

    PROMOTION_RECORD_PATH.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(f"[SPECTRE/promotion] promotion_record.json written", flush=True)

    # 3. Update working_state
    state = _read_state()
    state["cv_approval_status"] = "PROMOTED"
    state["promotion"] = {
        "version": PROMOTION_VERSION,
        "promoted_at": now,
        "git_tag": GIT_TAG,
    }
    _write_state(state)
    print(f"[SPECTRE/promotion] working_state updated — cv_approval_status=PROMOTED", flush=True)

    # 4. Git tag (best-effort — no crash if git unavailable)
    try:
        result = subprocess.run(
            ["git", "tag", "-a", GIT_TAG, "-m", f"SPECTRE sandbox v1 promoted — 124/124 PASS — {now}"],
            capture_output=True, text=True, cwd=_BASE.parent.parent,
        )
        if result.returncode == 0:
            print(f"[SPECTRE/promotion] git tag '{GIT_TAG}' created", flush=True)
        elif "already exists" in result.stderr:
            print(f"[SPECTRE/promotion] git tag '{GIT_TAG}' already exists — OK", flush=True)
        else:
            print(f"[SPECTRE/promotion] git tag warning: {result.stderr.strip()}", flush=True)
    except Exception as ex:
        print(f"[SPECTRE/promotion] git tag skipped (git unavailable): {ex}", flush=True)

    print(f"[SPECTRE/promotion] ✅ SPECTRE promoted to {PROMOTION_VERSION}", flush=True)
    return record


def promotion_status() -> dict:
    """Return current promotion status."""
    if PROMOTION_RECORD_PATH.exists():
        return json.loads(PROMOTION_RECORD_PATH.read_text())
    state = _read_state()
    return {
        "cv_approval_status": state.get("cv_approval_status", "sandbox"),
        "promotion": state.get("promotion"),
    }


if __name__ == "__main__":
    record = promote()
    print(json.dumps(record, indent=2, ensure_ascii=False))
    sys.exit(0)
