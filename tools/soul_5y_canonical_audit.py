#!/usr/bin/env python3
"""Audit a frozen SOUL five-year SQLite corpus without mutating it."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import platform
import sqlite3
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

from soul_framework import Soul, __version__ as soul_version

from fable.reviews.soul_core_scale_harness import TARGETS


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def logical_hash(path: Path) -> tuple[str, int, str, str, str, int]:
    digest = hashlib.sha256()
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT COUNT(*), MIN(created_at), MAX(created_at) FROM memories"
        ).fetchone()
        newest_row = conn.execute(
            "SELECT content FROM memories ORDER BY created_at DESC, id DESC LIMIT 1"
        ).fetchone()
        target_presence = sum(
            bool(conn.execute("SELECT 1 FROM memories WHERE content = ?", (target,)).fetchone())
            for target, _query in TARGETS
        )
        columns = [item[1] for item in conn.execute("PRAGMA table_info(memories)")]
        select = ",".join('"' + name.replace('"', '""') + '"' for name in columns)
        cursor = conn.execute(f"SELECT {select} FROM memories ORDER BY id")
        for record in cursor:
            serial = []
            for value in record:
                if isinstance(value, bytes):
                    serial.append({"blob_sha256": hashlib.sha256(value).hexdigest(), "length": len(value)})
                else:
                    serial.append(value)
            digest.update(json.dumps(serial, ensure_ascii=False, separators=(",", ":")).encode())
            digest.update(b"\n")
        return (
            digest.hexdigest(), int(row[0]), str(row[1]), str(row[2]),
            str(newest_row[0]), target_presence,
        )
    finally:
        conn.close()


async def audit(path: Path, expected: int, repeats: int) -> dict:
    logical, raw_count, oldest, newest, positive_canary, target_presence = logical_hash(path)
    target_exact_hits = 0
    semantic_hits = 0
    negative_exact_hits = 0
    latencies = []
    async with await Soul.create("ScaleSubject", backend="sqlite", backend_url=str(path)) as soul:
        reopened_count = await soul.memory.count()
        for target, query in TARGETS:
            exact = await soul.memory.search(target, limit=5)
            target_exact_hits += int(any(item.memory.content == target for item in exact))
            semantic = await soul.memory.search(query, limit=5)
            semantic_hits += int(any(item.memory.content == target for item in semantic))
        absent = "CONTROL_NEGATIVO_9f7c8b41_este_recuerdo_no_existe"
        negative = await soul.memory.search(absent, limit=5)
        negative_exact_hits = sum(item.memory.content == absent for item in negative)
        positive = await soul.memory.search(positive_canary, limit=5)
        positive_canary_hits = sum(item.memory.content == positive_canary for item in positive)
        for index in range(repeats + 2):
            start = time.perf_counter()
            await soul.memory.search(f"consulta baseline lineal cinco anos {index % 3}", limit=5)
            elapsed = (time.perf_counter() - start) * 1000
            if index >= 2:
                latencies.append(elapsed)
    ordered = sorted(latencies)
    percentile = lambda p: ordered[min(len(ordered) - 1, int((len(ordered) - 1) * p))]
    oldest_dt = datetime.fromisoformat(oldest)
    newest_dt = datetime.fromisoformat(newest)
    return {
        "path": str(path),
        "expected_count": expected,
        "raw_count": raw_count,
        "reopened_count": reopened_count,
        "count_ok": raw_count == reopened_count == expected,
        "size_bytes": path.stat().st_size,
        "file_sha256": sha256_file(path),
        "logical_sha256": logical,
        "oldest_created_at": oldest,
        "newest_created_at": newest,
        "observed_span_days": (newest_dt - oldest_dt).total_seconds() / 86400,
        "ground_truth_targets_present": f"{target_presence}/{len(TARGETS)}",
        "positive_control_recent_exact_canary_hits_at_5": positive_canary_hits,
        "positive_control_ok": positive_canary_hits == 1,
        "target_exact_query_recall_at_5": f"{target_exact_hits}/{len(TARGETS)}",
        "semantic_recall_at_5": f"{semantic_hits}/{len(TARGETS)}",
        "negative_control_exact_absent_hits": negative_exact_hits,
        "negative_control_ok": negative_exact_hits == 0,
        "linear_search_ms": {
            "repeats": repeats,
            "mean": statistics.mean(latencies),
            "p50": statistics.median(latencies),
            "p95": percentile(0.95),
            "p99": percentile(0.99),
            "min": min(latencies),
            "max": max(latencies),
        },
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", action="append", nargs=2, metavar=("PATH", "EXPECTED"), required=True)
    parser.add_argument("--repeats", type=int, default=30)
    args = parser.parse_args()
    results = []
    for raw_path, raw_expected in args.db:
        results.append(await audit(Path(raw_path), int(raw_expected), args.repeats))
    print(json.dumps({
        "tool": "tools/soul_5y_canonical_audit.py",
        "python": sys.version,
        "platform": platform.platform(),
        "soul_framework": soul_version,
        "results": results,
    }, ensure_ascii=False, indent=2))
    return 0 if all(r["count_ok"] and r["positive_control_ok"] and r["negative_control_ok"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
