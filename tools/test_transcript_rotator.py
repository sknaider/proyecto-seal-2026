"""tests for transcript_rotator.py — NEXUS, 2026-05-02.

R1: rotate_file archives excess lines and keeps last N
R2: archive is valid gzip with correct content
R3: active file is atomically replaced (size reduced)
R4: dry-run returns summary dict but does NOT modify files
R5: rotate_file returns None when lines <= keep_lines (no rotation needed)
R6: _index.json updated after rotation with correct metadata
R7: find_messages_candidates respects size threshold
R8: find_session_candidates respects size threshold AND idle time
R9: rotate_file handles unicode content without corruption
"""
from __future__ import annotations

import gzip
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import transcript_rotator as tr

_pass = 0
_fail = 0


def test(name: str, fn) -> None:
    global _pass, _fail
    num = _pass + _fail + 1
    try:
        fn()
        print(f"  ✅ R{num}: {name}")
        _pass += 1
    except Exception as e:
        print(f"  ❌ R{num}: {name} — {e}")
        _fail += 1


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_jsonl(n_lines: int, prefix: str = "line") -> str:
    return "".join(
        json.dumps({"id": i, "content": f"{prefix} {i} — αβγδ ñoño"}) + "\n"
        for i in range(n_lines)
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

print("\n=== TEST TRANSCRIPT ROTATOR ===")


def r1_rotate_archives_excess_keeps_last_n():
    """rotate_file: 800 lines in, keep=500 → 300 archived, 500 active."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "test.jsonl"
        content = _make_jsonl(800)
        path.write_text(content)

        result = tr.rotate_file(path, keep_lines=500, dry_run=False, verbose=False)

        assert result is not None, "Should have rotated"
        assert result["archived_lines"] == 300, f"Expected 300 archived, got {result['archived_lines']}"
        assert result["kept_lines"] == 500, f"Expected 500 kept, got {result['kept_lines']}"

        active_lines = path.read_text().splitlines()
        assert len(active_lines) == 500, f"Active file has {len(active_lines)} lines, expected 500"
        # Active file must contain the LAST 500 lines (id 300..799)
        first_id = json.loads(active_lines[0])["id"]
        assert first_id == 300, f"First active line should be id=300, got {first_id}"

test("rotate_file: 800→500 keep, 300 archived", r1_rotate_archives_excess_keeps_last_n)


def r2_archive_is_valid_gzip():
    """Archive file is valid gzip containing the correct lines."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "channel.jsonl"
        path.write_text(_make_jsonl(600))

        result = tr.rotate_file(path, keep_lines=500, dry_run=False, verbose=False)
        assert result is not None
        arc_path = Path(result["archive_path"])

        assert arc_path.exists(), f"Archive not found: {arc_path}"
        assert arc_path.suffix == ".gz", "Archive must be .gz"

        with gzip.open(arc_path, "rb") as f:
            archived_content = f.read().decode("utf-8")
        lines = archived_content.splitlines()
        assert len(lines) == 100, f"Expected 100 archived lines, got {len(lines)}"
        # Must be the FIRST 100 lines (id 0..99)
        first_id = json.loads(lines[0])["id"]
        assert first_id == 0, f"First archived line should be id=0, got {first_id}"

test("archive is valid gzip with correct content", r2_archive_is_valid_gzip)


def r3_active_file_replaced_atomically():
    """After rotation, active file is smaller (atomic replace worked)."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "messages.jsonl"
        original_content = _make_jsonl(1000)
        path.write_text(original_content)
        original_size = path.stat().st_size

        result = tr.rotate_file(path, keep_lines=200, dry_run=False, verbose=False)
        assert result is not None

        new_size = path.stat().st_size
        assert new_size < original_size, f"File should be smaller: {new_size} vs {original_size}"
        assert result["new_mb"] < result["original_mb"], "new_mb must be < original_mb"
        assert result["saved_mb"] > 0, "saved_mb must be positive"

        # Verify file is still valid JSON-lines
        for line in path.read_text().splitlines():
            json.loads(line)  # raises if corrupt

test("active file atomically replaced, smaller, valid JSON", r3_active_file_replaced_atomically)


def r4_dry_run_does_not_modify():
    """dry_run=True: returns summary dict but files are NOT modified."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "safe.jsonl"
        content = _make_jsonl(700)
        path.write_text(content)
        original_size = path.stat().st_size

        result = tr.rotate_file(path, keep_lines=500, dry_run=True, verbose=False)

        assert result is not None, "Dry-run should return summary"
        assert result.get("dry_run") is True, "dry_run flag must be set"
        assert result["archived_lines"] == 200
        assert path.stat().st_size == original_size, "File must NOT be modified in dry-run"

        # No archive should exist
        arc_path = Path(result["archive_path"])
        assert not arc_path.exists(), "Archive must NOT be created in dry-run"

test("dry-run: returns summary, files NOT modified", r4_dry_run_does_not_modify)


def r5_skip_when_lines_below_threshold():
    """rotate_file returns None when total_lines <= keep_lines."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "small.jsonl"
        path.write_text(_make_jsonl(300))

        result = tr.rotate_file(path, keep_lines=500, dry_run=False, verbose=False)
        assert result is None, f"Should return None for small file, got {result}"

test("skip rotation when lines <= keep_lines", r5_skip_when_lines_below_threshold)


def r6_index_updated_after_rotation():
    """_index.json is created/updated after rotation with correct metadata."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "indexed.jsonl"
        path.write_text(_make_jsonl(600))

        result = tr.rotate_file(path, keep_lines=500, dry_run=False, verbose=False)
        assert result is not None

        # Index is at base_dir/_archive/_index.json
        index_path = Path(d) / tr.ARCHIVE_DIR_NAME / tr.INDEX_FILENAME
        assert index_path.exists(), f"Index not found: {index_path}"

        index = json.loads(index_path.read_text())
        assert len(index) == 1, f"Expected 1 index entry, got {len(index)}"
        entry = index[0]
        assert "file" in entry
        assert "archived_lines" in entry
        assert "archive_path" in entry
        assert entry["archived_lines"] == 100

        # Second rotation appends to index
        path.write_text(_make_jsonl(600))
        tr.rotate_file(path, keep_lines=500, dry_run=False, verbose=False)
        index2 = json.loads(index_path.read_text())
        assert len(index2) == 2, f"Expected 2 entries after 2nd rotation, got {len(index2)}"

test("_index.json created/updated with correct metadata", r6_index_updated_after_rotation)


def r7_find_messages_candidates_threshold():
    """find_messages_candidates only includes files above threshold."""
    # Patch MESSAGES_DIR temporarily
    original_dir = tr.MESSAGES_DIR
    with tempfile.TemporaryDirectory() as d:
        tr.MESSAGES_DIR = Path(d)
        try:
            big = Path(d) / "big.jsonl"
            small = Path(d) / "small.jsonl"
            # big > 2MB threshold (2.1MB)
            big.write_bytes(b"x" * int(2.1 * 1024 * 1024))
            # small < 2MB threshold (0.5MB)
            small.write_bytes(b"x" * int(0.5 * 1024 * 1024))

            candidates = tr.find_messages_candidates(threshold_mb=2.0)
            names = [p.name for p in candidates]
            assert "big.jsonl" in names, f"big.jsonl missing from candidates: {names}"
            assert "small.jsonl" not in names, f"small.jsonl should not be a candidate: {names}"
        finally:
            tr.MESSAGES_DIR = original_dir

test("find_messages_candidates respects size threshold", r7_find_messages_candidates_threshold)


def r8_find_session_candidates_idle_filter():
    """find_session_candidates: large+idle → candidate; large+recent → skip."""
    original_root = tr.CLAUDE_PROJECTS_ROOT
    with tempfile.TemporaryDirectory() as d:
        tr.CLAUDE_PROJECTS_ROOT = Path(d)
        try:
            proj = Path(d) / "test-project"
            proj.mkdir()

            idle_file = proj / "idle_session.jsonl"
            recent_file = proj / "active_session.jsonl"

            big_content = b"x" * int(6 * 1024 * 1024)  # 6MB > 5MB threshold
            idle_file.write_bytes(big_content)
            recent_file.write_bytes(big_content)

            # Make idle_file old (5h ago = past the 4h cutoff)
            old_time = time.time() - (5 * 3600)
            os.utime(idle_file, (old_time, old_time))
            # recent_file mtime = now (default, within idle window)

            candidates = tr.find_session_candidates(threshold_mb=5.0, idle_hours=4.0)
            names = [p.name for p in candidates]
            assert "idle_session.jsonl" in names, f"idle_session missing: {names}"
            assert "active_session.jsonl" not in names, f"active_session should be excluded: {names}"
        finally:
            tr.CLAUDE_PROJECTS_ROOT = original_root

test("find_session_candidates: idle→included, recent→excluded", r8_find_session_candidates_idle_filter)


def r9_unicode_content_preserved():
    """Unicode content (ñ, α, emoji) is preserved correctly through rotation."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "unicode.jsonl"
        lines = []
        for i in range(600):
            lines.append(json.dumps({"id": i, "msg": f"línea {i} — αβγδ 🤖 ñoño"}, ensure_ascii=False) + "\n")
        path.write_text("".join(lines), encoding="utf-8")

        result = tr.rotate_file(path, keep_lines=500, dry_run=False, verbose=False)
        assert result is not None

        # Check active file preserves unicode
        active_text = path.read_text(encoding="utf-8")
        assert "αβγδ" in active_text, "Greek chars missing from active file"
        assert "ñoño" in active_text, "Spanish chars missing from active file"
        assert "🤖" in active_text, "Emoji missing from active file"

        # Check archive preserves unicode
        with gzip.open(result["archive_path"], "rb") as f:
            archived = f.read().decode("utf-8")
        assert "αβγδ" in archived, "Greek chars missing from archive"
        assert "ñoño" in archived, "Spanish chars missing from archive"

test("unicode content preserved in active file and archive", r9_unicode_content_preserved)


# ── Summary ───────────────────────────────────────────────────────────────────

print(f"\n{'='*50}")
print(f"Transcript Rotator Tests: {_pass}/{_pass + _fail} PASS")
if _fail:
    sys.exit(1)
