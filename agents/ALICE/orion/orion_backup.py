#!/usr/bin/env python3
"""Backup autocontenido del schema orion_exam (software de exámenes de Henry).
pg_dump no está en el host, así que uso COPY CSV nativo vía asyncpg (maneja
uuid/jsonb/timestamptz correctamente). Corre por systemd timer (orion-backup.timer).

Cada backup = un directorio con timestamp y un CSV por tabla. Guarda los últimos KEEP.
Restaurar (a schema vacío):  orion_backup.py restore <dir_del_snapshot>
"""
import asyncio, asyncpg, sys, pathlib, shutil, os
from datetime import datetime, timezone

DSN = os.environ.get("ORION_EXAM_DSN")  # FAIL-CLOSED: sin env no corremos (nunca superusuario seal)
if not DSN:
    raise SystemExit("ORION_EXAM_DSN no definido — abortando backup (fail-closed, no uso el superusuario seal)")
SCHEMA = "orion_exam"
BACKUP_DIR = pathlib.Path("/home/dadito/IA/proyecto-seal/backups/orion_exam")
KEEP = 96  # ~2 días si corre cada 30 min
# orden FK-seguro (padres primero) para restaurar
TABLES = ["users", "courses", "enrollments", "questions", "question_options",
          "exams", "exam_questions", "exam_sessions", "answers", "anticopy_events",
          "course_content", "tasks", "task_submissions"]


async def backup(*, prune: bool = True):
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    snap = BACKUP_DIR / f"orion_exam_{ts}"
    snap.mkdir()
    c = await asyncpg.connect(DSN)
    counts = {}
    for t in TABLES:
        out = snap / f"{t}.csv"
        await c.copy_from_query(f"SELECT * FROM {SCHEMA}.{t}", output=str(out), format="csv", header=True)
        counts[t] = await c.fetchval(f"SELECT count(*) FROM {SCHEMA}.{t}")
    await c.close()
    dirs = sorted(d for d in BACKUP_DIR.glob("orion_exam_*") if d.is_dir())
    if prune:
        for old in dirs[:-KEEP]:
            shutil.rmtree(old, ignore_errors=True)
    print(
        f"[orion_backup] {snap.name} | {counts} | snapshots={len(dirs)} "
        f"| prune={'on' if prune else 'off'}"
    )


async def restore(dirp):
    d = pathlib.Path(dirp)
    c = await asyncpg.connect(DSN)
    for t in TABLES:
        f = d / f"{t}.csv"
        if not f.exists():
            continue
        try:
            await c.copy_to_table(t, schema_name=SCHEMA, source=str(f), format="csv", header=True)
            print(f"  restaurada {t}")
        except Exception as e:
            print(f"  ERROR restaurando {t}: {e}")
    await c.close()
    print(f"[orion_backup] restore desde {dirp} terminado")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "restore":
        asyncio.run(restore(sys.argv[2]))
    else:
        asyncio.run(backup(prune="--no-prune" not in sys.argv[1:]))
