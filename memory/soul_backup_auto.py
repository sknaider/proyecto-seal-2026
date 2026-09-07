#!/usr/bin/env python3
"""SEAL Soul Auto-Backup — pg_dump + Qdrant snapshots programados.

Ejecutar diariamente via systemd timer (02:00).
Retención: 7 backups más recientes por tipo, resto eliminado.

Destino: /home/dadito/IA/proyecto-seal/backups/
  postgresql/  → dump .sql.gz por fecha
  qdrant/      → snapshots por colección
"""

import asyncio
import gzip
import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

DB_CONTAINER = "seal-memory-db"
DB_USER = "seal"
DB_NAME = "seal_memory"
QDRANT_URL = "http://localhost:6333"
BACKUP_ROOT = Path("/home/dadito/IA/proyecto-seal/backups")
RETENTION = 7
LOG_PATH = BACKUP_ROOT / "backup_auto_log.jsonl"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("seal_backup_auto")


def pg_dump() -> dict:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = BACKUP_ROOT / "postgresql"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"seal_memory_{ts}.sql.gz"

    cmd = [
        "docker", "exec", DB_CONTAINER,
        "pg_dump", "-U", DB_USER, "-d", DB_NAME,
        "--no-password", "--no-owner", "--no-privileges",
        "--clean", "--if-exists",
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=180)
    if result.returncode != 0:
        raise RuntimeError(f"pg_dump failed: {result.stderr.decode()[:300]}")

    with gzip.open(out_file, "wb") as f:
        f.write(result.stdout)

    size_mb = out_file.stat().st_size / 1_048_576
    log.info(f"PostgreSQL dump: {out_file.name} ({size_mb:.1f} MB)")
    return {"file": out_file.name, "size_mb": round(size_mb, 1)}


async def qdrant_snapshots(session: aiohttp.ClientSession) -> dict:
    out_dir = BACKUP_ROOT / "qdrant"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    async with session.get(f"{QDRANT_URL}/collections") as r:
        data = await r.json()
    collections = [c["name"] for c in data.get("result", {}).get("collections", [])]

    results = {}
    for col in collections:
        async with session.post(f"{QDRANT_URL}/collections/{col}/snapshots") as r:
            snap_data = await r.json()
        snap_name = snap_data["result"]["name"]

        local_path = out_dir / f"{col}_{ts}.snapshot"
        async with session.get(
            f"{QDRANT_URL}/collections/{col}/snapshots/{snap_name}"
        ) as r:
            with open(local_path, "wb") as f:
                async for chunk in r.content.iter_chunked(65536):
                    f.write(chunk)

        await session.delete(f"{QDRANT_URL}/collections/{col}/snapshots/{snap_name}")

        size_mb = local_path.stat().st_size / 1_048_576
        results[col] = {"file": local_path.name, "size_mb": round(size_mb, 1)}
        log.info(f"Qdrant snapshot: {col} ({size_mb:.1f} MB)")

    return results


def rotate(subdir: str) -> int:
    d = BACKUP_ROOT / subdir
    if not d.exists():
        return 0
    files = sorted(d.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)
    deleted = 0
    for old in files[RETENTION:]:
        old.unlink()
        deleted += 1
        log.info(f"Rotated: {old.name}")
    return deleted


async def run_backup():
    report = {"ts": datetime.now(timezone.utc).isoformat(), "steps": {}}

    try:
        report["steps"]["postgresql"] = pg_dump()

        async with aiohttp.ClientSession() as session:
            report["steps"]["qdrant"] = await qdrant_snapshots(session)

        rotated = rotate("postgresql") + rotate("qdrant")
        report["steps"]["rotated"] = rotated
        report["status"] = "ok"
        log.info("Backup auto completado OK")

    except Exception as e:
        report["status"] = "error"
        report["error"] = str(e)
        log.error(f"Backup FALLÓ: {e}")
        raise

    finally:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(report, default=str) + "\n")

    return report


if __name__ == "__main__":
    report = asyncio.run(run_backup())
    print(json.dumps(report, indent=2, default=str))
    sys.exit(0 if report["status"] == "ok" else 1)
