#!/usr/bin/env python3
"""transcript_rotator.py — SEAL rotación de transcripts (fix arquitectural).

Problema: transcripts/messages .jsonl crecen indefinidamente.
  - william_channel.jsonl ya tiene 5MB, crece a ritmo de ~1MB/día
  - Claude Code session UUIDs llegan a 76MB — ralentizan cada turno
  - ALICE no puede leer archivos > 32MB

Solución: rotar archivos que superen umbral, manteniendo ventana activa.
  - Archivo original → se trunca a las N líneas más recientes
  - Resto → comprimido en _archive/{YYYY}/{MM}/{nombre}_{timestamp}.jsonl.gz
  - Índice → _archive/_index.json con metadata por entrada archivada

Target files (orden de prioridad):
  1. messages/*.jsonl — comunicación del equipo (rotar agresivamente)
  2. ~/.claude/projects/**/*.jsonl — sesiones Claude Code (rotar si > 7 días Y > 5MB)

Uso:
  python3 transcript_rotator.py --scan          # muestra candidatos sin tocar
  python3 transcript_rotator.py --rotate        # rota todos los candidatos
  python3 transcript_rotator.py --file <path>   # rota un archivo específico
  python3 transcript_rotator.py --dry-run       # simula sin escribir

Seguridad:
  - Escritura atómica (write temp → rename) — nunca corrompe archivo activo
  - Backup completo antes de truncar
  - Claude Code sessions: solo si no modificados en últimas 4h (no está en sesión activa)
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

# ── Config ────────────────────────────────────────────────────────────────────

# Messages files: rotate if > 2MB, keep last 500 lines
MESSAGES_SIZE_THRESHOLD_MB = 2.0
MESSAGES_KEEP_LINES = 500

# Claude Code sessions: rotate if > 5MB AND not modified in last 4h
SESSIONS_SIZE_THRESHOLD_MB = 5.0
SESSIONS_IDLE_HOURS = 4  # don't touch sessions modified recently

# Archive structure
ARCHIVE_DIR_NAME = "_archive"
INDEX_FILENAME = "_index.json"

# Paths
SEAL_ROOT = Path(__file__).parent.parent
MESSAGES_DIR = SEAL_ROOT / "messages"
CLAUDE_PROJECTS_ROOT = Path.home() / ".claude" / "projects"


# ── Archive helpers ────────────────────────────────────────────────────────────

def _archive_dir(base_dir: Path, dt: datetime) -> Path:
    return base_dir / ARCHIVE_DIR_NAME / str(dt.year) / f"{dt.month:02d}"


def _archive_name(original: Path, dt: datetime) -> str:
    ts = dt.strftime("%Y%m%d_%H%M%S")
    return f"{original.stem}_{ts}.jsonl.gz"


def _update_index(base_dir: Path, entry: dict[str, Any]) -> None:
    index_path = base_dir / ARCHIVE_DIR_NAME / INDEX_FILENAME
    index: list[dict] = []
    if index_path.exists():
        try:
            index = json.loads(index_path.read_text())
        except Exception:
            index = []
    index.append(entry)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2))


# ── Core rotation logic ───────────────────────────────────────────────────────

def rotate_file(
    path: Path,
    keep_lines: int,
    dry_run: bool = False,
    verbose: bool = True,
) -> dict[str, Any] | None:
    """
    Rotate a single .jsonl file.

    Returns rotation summary dict, or None if skipped.
    Atomic write: never leaves target in corrupted state.
    """
    if not path.exists():
        return None

    original_bytes = path.stat().st_size
    original_mb = original_bytes / (1024 * 1024)

    # Read all lines
    try:
        all_lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    except Exception as e:
        print(f"  [rotator] ERROR reading {path.name}: {e}", file=sys.stderr)
        return None

    total_lines = len(all_lines)
    if total_lines <= keep_lines:
        if verbose:
            print(f"  [rotator] SKIP {path.name} — {total_lines} lines ≤ {keep_lines} keep threshold")
        return None

    archive_lines = all_lines[:-keep_lines]
    active_lines = all_lines[-keep_lines:]
    archived_count = len(archive_lines)

    now = datetime.now(timezone.utc)
    arc_dir = _archive_dir(path.parent, now)
    arc_name = _archive_name(path, now)
    arc_path = arc_dir / arc_name

    if verbose:
        print(f"  [rotator] {path.name}: {original_mb:.1f}MB, {total_lines} lines")
        print(f"    → archive {archived_count} lines → {arc_path}")
        print(f"    → keep {len(active_lines)} lines in active file")

    if dry_run:
        print(f"    [DRY RUN] would write archive + truncate active")
        return {
            "file": str(path),
            "original_mb": round(original_mb, 2),
            "archived_lines": archived_count,
            "kept_lines": len(active_lines),
            "archive_path": str(arc_path),
            "dry_run": True,
        }

    # Write archive (compressed)
    arc_dir.mkdir(parents=True, exist_ok=True)
    archive_content = "".join(archive_lines).encode("utf-8")
    with gzip.open(arc_path, "wb") as f:
        f.write(archive_content)

    # Atomically replace active file with truncated version
    active_content = "".join(active_lines)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(active_content)
        os.replace(tmp_path, path)  # atomic on POSIX
    except Exception as e:
        os.unlink(tmp_path)
        raise RuntimeError(f"Atomic replace failed for {path.name}: {e}") from e

    new_mb = path.stat().st_size / (1024 * 1024)
    saved_mb = original_mb - new_mb

    result = {
        "file": str(path),
        "original_mb": round(original_mb, 2),
        "new_mb": round(new_mb, 3),
        "saved_mb": round(saved_mb, 2),
        "archived_lines": archived_count,
        "kept_lines": len(active_lines),
        "archive_path": str(arc_path),
        "rotated_at": now.isoformat(),
    }

    # Update index
    _update_index(path.parent, result)

    if verbose:
        print(f"    ✅ saved {saved_mb:.1f}MB → new size {new_mb:.2f}MB")

    return result


# ── Candidate discovery ───────────────────────────────────────────────────────

def find_messages_candidates(threshold_mb: float = MESSAGES_SIZE_THRESHOLD_MB) -> list[Path]:
    """Find messages/*.jsonl files above threshold."""
    candidates = []
    if not MESSAGES_DIR.exists():
        return candidates
    for p in MESSAGES_DIR.glob("*.jsonl"):
        size_mb = p.stat().st_size / (1024 * 1024)
        if size_mb >= threshold_mb:
            candidates.append(p)
    return sorted(candidates, key=lambda p: -p.stat().st_size)


def find_session_candidates(
    threshold_mb: float = SESSIONS_SIZE_THRESHOLD_MB,
    idle_hours: float = SESSIONS_IDLE_HOURS,
) -> list[Path]:
    """Find Claude Code session .jsonl files that are large AND idle."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=idle_hours)
    candidates = []

    for project_dir in CLAUDE_PROJECTS_ROOT.iterdir():
        if not project_dir.is_dir():
            continue
        for p in project_dir.glob("*.jsonl"):
            try:
                stat = p.stat()
                size_mb = stat.st_size / (1024 * 1024)
                mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                if size_mb >= threshold_mb and mtime < cutoff:
                    candidates.append(p)
            except Exception:
                continue

    return sorted(candidates, key=lambda p: -p.stat().st_size)


# ── CLI ───────────────────────────────────────────────────────────────────────

def _fmt_mb(path: Path) -> str:
    return f"{path.stat().st_size / (1024*1024):.1f}MB"


def cmd_scan(include_sessions: bool = False) -> None:
    """Show rotation candidates without modifying anything."""
    msg_candidates = find_messages_candidates()
    print(f"\n📋 Messages candidates (>{MESSAGES_SIZE_THRESHOLD_MB}MB):")
    if msg_candidates:
        for p in msg_candidates:
            print(f"  {_fmt_mb(p):>8}  {p.name}")
    else:
        print("  (none above threshold)")

    if include_sessions:
        sess_candidates = find_session_candidates()
        print(f"\n📋 Session candidates (>{SESSIONS_SIZE_THRESHOLD_MB}MB, idle >{SESSIONS_IDLE_HOURS}h):")
        if sess_candidates:
            for p in sess_candidates[:10]:
                print(f"  {_fmt_mb(p):>8}  {p.parent.name}/{p.name[:16]}...")
        else:
            print("  (none above threshold)")


def cmd_rotate(
    include_sessions: bool = False,
    dry_run: bool = False,
    messages_keep: int = MESSAGES_KEEP_LINES,
) -> list[dict]:
    """Rotate all qualifying files."""
    results = []

    print(f"\n🔄 Rotating messages (keep last {messages_keep} lines)...")
    for p in find_messages_candidates():
        r = rotate_file(p, keep_lines=messages_keep, dry_run=dry_run)
        if r:
            results.append(r)

    if include_sessions:
        print(f"\n🔄 Rotating idle sessions...")
        for p in find_session_candidates():
            r = rotate_file(p, keep_lines=200, dry_run=dry_run)
            if r:
                results.append(r)

    total_saved = sum(r.get("saved_mb", 0) for r in results)
    print(f"\n{'='*50}")
    print(f"Rotated: {len(results)} files | Saved: {total_saved:.1f}MB")
    if dry_run:
        print("[DRY RUN — no files modified]")

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="SEAL transcript rotator")
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--scan", action="store_true", help="Show candidates without modifying")
    grp.add_argument("--rotate", action="store_true", help="Rotate all candidates")
    grp.add_argument("--file", type=Path, metavar="PATH", help="Rotate specific file")
    parser.add_argument("--sessions", action="store_true", help="Also include Claude Code session files")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without writing")
    parser.add_argument("--keep", type=int, default=MESSAGES_KEEP_LINES, help="Lines to keep in active file")
    parser.add_argument("--threshold-mb", type=float, default=MESSAGES_SIZE_THRESHOLD_MB)
    args = parser.parse_args()

    if args.scan:
        cmd_scan(include_sessions=args.sessions)
    elif args.rotate:
        cmd_rotate(include_sessions=args.sessions, dry_run=args.dry_run, messages_keep=args.keep)
    elif args.file:
        result = rotate_file(args.file, keep_lines=args.keep, dry_run=args.dry_run)
        if result:
            print(json.dumps(result, indent=2))
        else:
            print(f"No rotation needed or file not found: {args.file}")


if __name__ == "__main__":
    main()
