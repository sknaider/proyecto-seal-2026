#!/usr/bin/env python3
"""
Sprint 4 / Mitigación G — Post-edit back-translation integrity check.

Hook PostToolUse para Edit, MultiEdit, Write.
Compara new_string (lo que el agente declaró escribir) con las líneas
realmente añadidas según git diff. Si divergen >30% → WARN + audit log.

Detecta casos donde el contenido en disco difiere de lo que Claude
creyó estar escribiendo (corrupción silenciosa, encoding issues, hooks
intermedios que modificaron el contenido, etc.).

Contract (Claude Code hooks):
    stdin  → JSON con {"tool_name": str, "tool_input": dict, "agent": str?}
    stdout → JSON {"backtranslate": str, "similarity": float, ...}
    exit   → 0 siempre
"""
from __future__ import annotations

import difflib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path("/home/dadito/IA/proyecto-seal")
CRITICAL_YAML = REPO_ROOT / "memory" / "critical_paths.yaml"
LOG_FILE = REPO_ROOT / "memory" / "logs" / "post_edit_backtranslate.jsonl"
ALERT_ENDPOINT = "http://localhost:8765/api/agents/send"

DIVERGENCE_THRESHOLD = 0.30  # >30% divergence → warn
MIN_LINES_TO_CHECK = 3       # skip check if new_string has <3 lines (too small to be meaningful)

LOG_FILE.parent.mkdir(parents=True, exist_ok=True)


def emit(result: dict) -> None:
    print(json.dumps(result, ensure_ascii=False))
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")
    sys.exit(0)


def is_critical(file_path: str) -> bool:
    import fnmatch
    fp = os.path.abspath(file_path)
    if not CRITICAL_YAML.exists():
        return False
    paths, excludes, section = [], [], None
    for line in CRITICAL_YAML.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("paths:"):
            section = "p"
            continue
        if s.startswith("exclude:"):
            section = "x"
            continue
        if section and s.startswith("- "):
            v = s[2:].strip().strip('"').strip("'")
            (paths if section == "p" else excludes).append(v)
        elif section and s and not s.startswith("#") and not s.startswith("-"):
            section = None
    for pat in excludes:
        if fnmatch.fnmatch(fp, pat):
            return False
    for pat in paths:
        if fp == pat or fnmatch.fnmatch(fp, pat):
            return True
    return False


def get_git_added_lines(file_path: str) -> list[str]:
    """Extract lines actually added according to git diff (without leading +)."""
    try:
        fp = Path(file_path)
        git_root = subprocess.check_output(
            ["git", "-C", str(fp.parent), "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL, text=True
        ).strip()
        diff_out = subprocess.check_output(
            ["git", "-C", git_root, "diff", "--", file_path],
            stderr=subprocess.DEVNULL, text=True
        )
        return [
            line[1:]  # strip leading '+'
            for line in diff_out.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        ]
    except Exception:
        return []


def normalize_lines(text: str) -> list[str]:
    """Normalize: strip trailing whitespace, lowercase, drop blank lines."""
    return [
        line.rstrip().lower()
        for line in text.splitlines()
        if line.strip()
    ]


def compute_similarity(declared: list[str], actual: list[str]) -> float:
    """
    Compute similarity ratio between declared (new_string) and actual (git diff added).
    Uses SequenceMatcher on joined normalized content.
    Returns 0.0–1.0 (1.0 = identical).
    """
    if not declared and not actual:
        return 1.0
    if not declared or not actual:
        return 0.0
    a = "\n".join(declared)
    b = "\n".join(actual)
    return difflib.SequenceMatcher(None, a, b).ratio()


_ALERT_STAMP_DIR = Path("/tmp/seal_alert_stamps")
ALERT_COOLDOWN_S = 60


def post_alert(agent: str, file_path: str, similarity: float, declared_lines: int, actual_lines: int) -> None:
    if os.environ.get("SEAL_HOOK_TEST"):
        return
    try:
        from hashlib import sha256 as _sha256
        _ALERT_STAMP_DIR.mkdir(parents=True, exist_ok=True)
        key = _sha256(f"backtranslate:{agent}:{file_path}".encode()).hexdigest()[:16]
        stamp_file = _ALERT_STAMP_DIR / f"{key}.stamp"
        now = time.time()
        if stamp_file.exists():
            last = float(stamp_file.read_text().strip() or "0")
            if now - last < ALERT_COOLDOWN_S:
                return
        stamp_file.write_text(str(now))
    except Exception:
        pass
    try:
        import urllib.request
        divergence = 1.0 - similarity
        msg = (
            f"[BACKTRANSLATE WARN] {agent} editó {Path(file_path).name} — "
            f"divergencia {divergence:.0%} entre new_string ({declared_lines} líneas) "
            f"y git diff ({actual_lines} líneas añadidas). "
            f"El contenido en disco puede diferir de lo que Claude creyó escribir."
        )
        payload = json.dumps({
            "from": agent,
            "to": "William",
            "type": "alert",
            "channel": "web_chat",
            "message": msg,
        }, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            ALERT_ENDPOINT,
            data=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST"
        )
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass


def main() -> None:
    raw = sys.stdin.read()
    if not raw.strip():
        emit({"backtranslate": "empty_payload"})

    try:
        d = json.loads(raw)
    except Exception:
        emit({"backtranslate": "unparseable_payload"})

    tool = d.get("tool_name") or d.get("tool") or ""
    if tool not in ("Edit", "MultiEdit", "Write"):
        emit({"backtranslate": "tool_not_guarded", "tool": tool})

    ti = d.get("tool_input") or d.get("input") or {}
    file_path = ti.get("file_path") or ti.get("path") or ""
    agent = d.get("agent") or os.environ.get("SEAL_AGENT", "unknown")
    trace_id = d.get("trace_id", "-")

    if not file_path:
        emit({"backtranslate": "no_file_path"})

    if not is_critical(file_path):
        emit({"backtranslate": "path_not_critical", "file": file_path})

    # Write tool: content = full file → similarity with git diff unreliable for new files.
    # Skip backtranslation for Write (diffcheck covers size anomalies instead).
    if tool == "Write":
        emit({"backtranslate": "skipped_write_tool", "file": file_path, "agent": agent})

    # For MultiEdit, check each edit; for Edit, use new_string directly
    if tool == "MultiEdit":
        edits = ti.get("edits") or []
        declared_text = "\n".join(e.get("new_string", "") for e in edits)
    else:
        declared_text = ti.get("new_string") or ""

    declared_lines = normalize_lines(declared_text)

    # Skip check if too small to be meaningful
    if len(declared_lines) < MIN_LINES_TO_CHECK:
        emit({
            "backtranslate": "skipped_too_small",
            "file": file_path,
            "declared_lines": len(declared_lines),
            "min_lines": MIN_LINES_TO_CHECK,
        })

    actual_lines = get_git_added_lines(file_path)
    actual_normalized = normalize_lines("\n".join(actual_lines))

    similarity = compute_similarity(declared_lines, actual_normalized)
    divergence = 1.0 - similarity
    is_anomalous = divergence > DIVERGENCE_THRESHOLD

    result = {
        "backtranslate": "anomaly_warn" if is_anomalous else "ok",
        "file": file_path,
        "agent": agent,
        "trace_id": trace_id,
        "similarity": round(similarity, 3),
        "divergence": round(divergence, 3),
        "declared_lines": len(declared_lines),
        "actual_lines_added": len(actual_normalized),
        "threshold": DIVERGENCE_THRESHOLD,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    if is_anomalous:
        post_alert(agent, file_path, similarity, len(declared_lines), len(actual_normalized))

    emit(result)


if __name__ == "__main__":
    main()
