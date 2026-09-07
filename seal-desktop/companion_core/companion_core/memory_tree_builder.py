"""
memory_tree_builder.py — SEAL App hourly→daily→monthly→yearly distillation.

Walks the local `memories` SQLite table and produces summaries at each level,
persisted to `memory_tree`. Each level summarizes the level below (rolling up).

CLI:
    python -m companion_core.memory_tree_builder --level day --agent SOUL
    python -m companion_core.memory_tree_builder --level all --agent SOUL
    python -m companion_core.memory_tree_builder --level day --agent SOUL --dry-run

Inspired by OpenHuman's Memory Tree pattern but written clean-room.
Local-first: uses local LLM (Ollama / llama-server) for summarization.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from companion_core.db import init_db, close_db, get_db

LIMA_TZ = ZoneInfo("America/Lima")

LEVEL_ORDER = ["hour", "day", "month", "year"]
LEVEL_CHILD = {"day": "hour", "month": "day", "year": "month"}


def _bucket_bounds(level: str, ref: datetime) -> tuple[str, str]:
    """Compute ISO bucket_start/bucket_end for a given level around `ref` (Lima TZ)."""
    if level == "hour":
        start = ref.replace(minute=0, second=0, microsecond=0)
        end = start.replace(minute=59, second=59)
    elif level == "day":
        start = ref.replace(hour=0, minute=0, second=0, microsecond=0)
        end = ref.replace(hour=23, minute=59, second=59)
    elif level == "month":
        start = ref.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        # crude end: last day of month
        if ref.month == 12:
            end = start.replace(year=ref.year + 1, month=1) - _delta_sec(1)
        else:
            end = start.replace(month=ref.month + 1) - _delta_sec(1)
    elif level == "year":
        start = ref.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end = start.replace(year=ref.year + 1) - _delta_sec(1)
    else:
        raise ValueError(f"unknown level: {level}")
    return start.isoformat(), end.isoformat()


def _delta_sec(seconds: int):
    from datetime import timedelta
    return timedelta(seconds=seconds)


def _parse_sqlite_utc(value: str) -> datetime:
    """SQLite stores naive UTC timestamps as TEXT; convert them to Lima time."""
    text = str(value).replace("T", " ").split(".")[0]
    dt = datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
    return dt.replace(tzinfo=timezone.utc).astimezone(LIMA_TZ)


def _dedupe_bucket_refs(refs: list[datetime], level: str) -> list[datetime]:
    seen: dict[tuple[int, int, int, int], datetime] = {}
    for ref in refs:
        if level == "hour":
            key = (ref.year, ref.month, ref.day, ref.hour)
            normalized = ref.replace(minute=0, second=0, microsecond=0)
        elif level == "day":
            key = (ref.year, ref.month, ref.day, 0)
            normalized = ref.replace(hour=0, minute=0, second=0, microsecond=0)
        elif level == "month":
            key = (ref.year, ref.month, 1, 0)
            normalized = ref.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        elif level == "year":
            key = (ref.year, 1, 1, 0)
            normalized = ref.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            raise ValueError(f"unknown level: {level}")
        seen[key] = normalized
    return [seen[k] for k in sorted(seen)]


async def _summarize_text(items: list[str], context_label: str) -> str:
    """Try local LLM; fall back to concatenation if no LLM available."""
    if not items:
        return f"(sin actividad en {context_label})"
    joined = "\n".join(f"- {it[:200]}" for it in items[:30])
    prompt = (
        f"Resume estos eventos del {context_label} en 2-3 oraciones en español, "
        f"primera persona, tono natural. Sin bullets:\n\n{joined}"
    )
    try:
        from companion_core.dream_cycle import _llm_call
        text, _ = await _llm_call(prompt)
        if text and len(text.strip()) > 20:
            return text.strip()
    except Exception:
        pass
    return f"{context_label}: {len(items)} eventos. " + "; ".join(it[:60] for it in items[:3])


async def _build_hour_bucket(agent: str, ref: datetime, dry_run: bool = False) -> dict:
    """Bucket the past hour of memories into a single summary row.

    SQLite default `datetime('now')` is UTC without TZ, while ref is in Lima.
    We compute bucket bounds in UTC for the SQL comparison, store Lima ISO for the API.
    """
    bucket_start_lima, bucket_end_lima = _bucket_bounds("hour", ref)
    # SQLite stores TEXT timestamps in UTC like '2026-05-23 02:39:00'
    from datetime import timezone
    ref_utc = ref.astimezone(timezone.utc)
    utc_start = ref_utc.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
    utc_end = ref_utc.replace(minute=59, second=59).strftime("%Y-%m-%d %H:%M:%S")
    db = get_db()
    rows = await db.execute_fetchall(
        """SELECT id, content FROM memories
           WHERE agent = ?
             AND created_at >= ?
             AND created_at <= ?
             AND importance >= 4
           ORDER BY importance DESC
           LIMIT 30""",
        (agent, utc_start, utc_end),
    )
    bucket_start, bucket_end = bucket_start_lima, bucket_end_lima
    if not rows:
        return {"level": "hour", "agent": agent, "bucket_start": bucket_start, "rows_in_bucket": 0, "skipped": True}
    items = [str(r["content"]) for r in rows]
    summary = await _summarize_text(items, f"hora {ref.strftime('%H:00')}")
    child_ids = [r["id"] for r in rows]

    if not dry_run:
        await db.execute(
            """INSERT INTO memory_tree (agent, level, bucket_start, bucket_end, summary, child_ids)
               VALUES (?, 'hour', ?, ?, ?, ?)
               ON CONFLICT(agent, level, bucket_start) DO UPDATE SET
                   summary=excluded.summary,
                   child_ids=excluded.child_ids,
                   bucket_end=excluded.bucket_end""",
            (agent, bucket_start, bucket_end, summary, json.dumps(child_ids)),
        )
        await db.commit()
    return {"level": "hour", "agent": agent, "bucket_start": bucket_start,
            "rows_in_bucket": len(rows), "summary_chars": len(summary),
            "dry_run": dry_run}


async def _build_rollup(agent: str, level: str, ref: datetime, dry_run: bool = False) -> dict:
    """Roll up child level (e.g., day rolls up hour) into a higher-level bucket."""
    child = LEVEL_CHILD.get(level)
    if not child:
        return {"error": f"no rollup defined for level={level}"}
    bucket_start, bucket_end = _bucket_bounds(level, ref)
    db = get_db()
    rows = await db.execute_fetchall(
        """SELECT id, summary FROM memory_tree
           WHERE agent = ? AND level = ?
             AND bucket_start >= ? AND bucket_start <= ?
           ORDER BY bucket_start ASC""",
        (agent, child, bucket_start, bucket_end),
    )
    if not rows:
        return {"level": level, "agent": agent, "bucket_start": bucket_start,
                "rows_in_bucket": 0, "skipped": True}
    items = [str(r["summary"]) for r in rows]
    label_es = {"day": "día", "month": "mes", "year": "año"}.get(level, level)
    summary = await _summarize_text(items, label_es)
    child_ids = [r["id"] for r in rows]

    if not dry_run:
        await db.execute(
            """INSERT INTO memory_tree (agent, level, bucket_start, bucket_end, summary, child_ids)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(agent, level, bucket_start) DO UPDATE SET
                   summary=excluded.summary,
                   child_ids=excluded.child_ids,
                   bucket_end=excluded.bucket_end""",
            (agent, level, bucket_start, bucket_end, summary, json.dumps(child_ids)),
        )
        await db.commit()
    return {"level": level, "agent": agent, "bucket_start": bucket_start,
            "rows_in_bucket": len(rows), "summary_chars": len(summary),
            "dry_run": dry_run}


async def build_level(agent: str, level: str, ref: Optional[datetime] = None,
                      dry_run: bool = False) -> dict:
    """Build a single level bucket relative to `ref` (default: now Lima)."""
    if ref is None:
        ref = datetime.now(LIMA_TZ)
    if level not in LEVEL_ORDER:
        raise ValueError(f"unknown level: {level}")
    if level == "hour":
        return await _build_hour_bucket(agent, ref, dry_run=dry_run)
    return await _build_rollup(agent, level, ref, dry_run=dry_run)


async def build_all(agent: str, ref: Optional[datetime] = None, dry_run: bool = False) -> list[dict]:
    """Build all 4 levels in order (hour → day → month → year).

    When no explicit ref is supplied, rebuild every historical bucket that has
    local memories. This makes the UI's "Reconstruir" action useful after the
    app has already collected memories.
    """
    if ref is None:
        db = get_db()
        rows = await db.execute_fetchall(
            """SELECT created_at FROM memories
               WHERE agent = ? AND importance >= 4
               ORDER BY created_at ASC""",
            (agent,),
        )
        refs = [_parse_sqlite_utc(r["created_at"]) for r in rows]
        if refs:
            results: list[dict] = []
            for level in LEVEL_ORDER:
                for level_ref in _dedupe_bucket_refs(refs, level):
                    results.append(await build_level(agent, level, ref=level_ref, dry_run=dry_run))
            return results
        ref = datetime.now(LIMA_TZ)

    results = []
    for level in LEVEL_ORDER:
        results.append(await build_level(agent, level, ref=ref, dry_run=dry_run))
    return results


# ─── CLI ─────────────────────────────────────────────────────────────────────

async def _cli_main(args: argparse.Namespace) -> None:
    await init_db()
    try:
        if args.level == "all":
            out = await build_all(args.agent, dry_run=args.dry_run)
        else:
            out = await build_level(args.agent, args.level, dry_run=args.dry_run)
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    finally:
        await close_db()


def main() -> None:
    p = argparse.ArgumentParser(description="SEAL App Memory Tree builder (h→d→m→y)")
    p.add_argument("--agent", default="SOUL")
    p.add_argument("--level", default="day", choices=[*LEVEL_ORDER, "all"])
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    asyncio.run(_cli_main(args))


if __name__ == "__main__":
    main()
