#!/usr/bin/env python3
"""
Sprint 2 / Mitigación B — Post-edit diff integrity check.

Hook PostToolUse para Edit, MultiEdit, Write.
Después de cada edición en un archivo crítico, calcula el diff real y detecta
cambios anómalos (ej: "fix de 1 línea" que borra el 60% del archivo).

Modo Sprint 2: WARN-ONLY — nunca bloquea, solo alerta y audita.
Modo Sprint 3+: puede activarse BLOCK si se pasa --strict en argv.

Contract (Claude Code hooks):
    stdin  → JSON con {"tool_name": str, "tool_input": dict, "agent": str?}
    stdout → JSON {"diffcheck": str, "lines_added": int, "lines_removed": int, ...}
    exit   → 0 siempre
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from hashlib import sha256
from pathlib import Path

REPO_ROOT = Path("/home/dadito/IA/proyecto-seal")
CRITICAL_YAML = REPO_ROOT / "memory" / "critical_paths.yaml"
LOG_FILE = REPO_ROOT / "memory" / "logs" / "post_edit_diffcheck.jsonl"
HASH_DIR = Path("/tmp/seal_hashes")
ALERT_ENDPOINT = "http://localhost:8765/api/agents/send"

# Umbral: si la fracción de líneas eliminadas / líneas originales supera esto → WARN
DELETION_RATIO_WARN = 0.50   # 50% del archivo borrado
DELETION_ABS_WARN   = 20     # o ≥20 líneas borradas absolutas

LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
HASH_DIR.mkdir(parents=True, exist_ok=True)


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


def get_file_hash(file_path: str) -> str:
    try:
        return sha256(Path(file_path).read_bytes()).hexdigest()[:16]
    except Exception:
        return ""


def load_pre_hash(file_path: str) -> str:
    key = sha256(file_path.encode()).hexdigest()[:16]
    hash_file = HASH_DIR / f"{key}.pre"
    try:
        return hash_file.read_text().strip()
    except Exception:
        return ""


def get_git_diff_stats(file_path: str) -> tuple[int, int, str]:
    """Returns (lines_added, lines_removed, git_root)."""
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
        added = sum(1 for l in diff_out.splitlines() if l.startswith("+") and not l.startswith("+++"))
        removed = sum(1 for l in diff_out.splitlines() if l.startswith("-") and not l.startswith("---"))
        return added, removed, git_root
    except Exception:
        return 0, 0, ""


def get_file_line_count(file_path: str) -> int:
    try:
        return len(Path(file_path).read_text(encoding="utf-8", errors="replace").splitlines())
    except Exception:
        return 0


ALERT_COOLDOWN_S = 60  # No repetir alerta del mismo archivo en <60s
_ALERT_STAMP_DIR = Path("/tmp/seal_alert_stamps")


def post_alert(agent: str, file_path: str, added: int, removed: int, ratio: float) -> None:
    # Dedup: skip si ya alertamos por este archivo en los últimos ALERT_COOLDOWN_S
    if os.environ.get("SEAL_HOOK_TEST"):
        return
    try:
        _ALERT_STAMP_DIR.mkdir(parents=True, exist_ok=True)
        key = sha256(f"diffcheck:{file_path}".encode()).hexdigest()[:16]
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
        msg = (
            f"[DIFFCHECK WARN] {agent} editó {Path(file_path).name} — "
            f"+{added}/{removed}- líneas ({ratio:.0%} del archivo eliminado). "
            f"Verificar que el cambio es intencional."
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
        emit({"diffcheck": "empty_payload"})

    try:
        d = json.loads(raw)
    except Exception:
        emit({"diffcheck": "unparseable_payload"})

    tool = d.get("tool_name") or d.get("tool") or ""
    if tool not in ("Edit", "MultiEdit", "Write"):
        emit({"diffcheck": "tool_not_guarded", "tool": tool})

    ti = d.get("tool_input") or d.get("input") or {}
    file_path = ti.get("file_path") or ti.get("path") or ""
    agent = d.get("agent") or os.environ.get("SEAL_AGENT", "unknown")
    trace_id = d.get("trace_id", "-")

    if not file_path:
        emit({"diffcheck": "no_file_path"})

    if not is_critical(file_path):
        emit({"diffcheck": "path_not_critical", "file": file_path})

    # Mitigation F — hash comparison
    pre_hash = load_pre_hash(file_path)
    post_hash = get_file_hash(file_path)
    hash_changed = pre_hash != post_hash if pre_hash else None

    # Mitigation B — diff stats
    added, removed, git_root = get_git_diff_stats(file_path)
    total_lines = get_file_line_count(file_path)

    ratio = removed / max(total_lines + removed, 1)
    is_anomalous = (ratio >= DELETION_RATIO_WARN) or (removed >= DELETION_ABS_WARN)

    result = {
        "diffcheck": "anomaly_warn" if is_anomalous else "ok",
        "file": file_path,
        "agent": agent,
        "trace_id": trace_id,
        "lines_added": added,
        "lines_removed": removed,
        "file_total_lines": total_lines,
        "deletion_ratio": round(ratio, 3),
        "hash_pre": pre_hash,
        "hash_post": post_hash,
        "hash_changed": hash_changed,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    if is_anomalous:
        post_alert(agent, file_path, added, removed, ratio)

    emit(result)


if __name__ == "__main__":
    main()
