#!/usr/bin/env python3
"""
lock_recovery.py — SEAL Task Lock Recovery
Inspirado en ClawTeam (win4r/ClawTeam-OpenClaw).

Detecta tareas con status=in_progress cuyo locked_at expiró y las resetea a pending.
Ejecutar al inicio de cada sesión de agente.

Uso:
    python3 lock_recovery.py                     # escanea y recupera (default)
    python3 lock_recovery.py --dry-run           # solo muestra sin modificar
    python3 lock_recovery.py --agent ADA         # solo tareas de ADA
    python3 lock_recovery.py --add "descripción" --agent ADA  # crear tarea
    python3 lock_recovery.py --list              # listar todas las tareas
    python3 lock_recovery.py --complete TASK_ID  # marcar completada
"""
from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path

TASK_FILE = Path(__file__).parent / "task_list.json"
LOCK_TIMEOUT_SECONDS = 300  # 5 minutos — tarea expirada si locked_at > 5min atrás


def load_tasks() -> dict:
    if not TASK_FILE.exists():
        return {"version": "1", "tasks": []}
    return json.loads(TASK_FILE.read_text())


def save_tasks(data: dict) -> None:
    TASK_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def recover_locks(agent: str | None = None, dry_run: bool = False) -> list[dict]:
    """
    Scan for in_progress tasks with expired locks. Reset to pending.
    Returns list of recovered tasks.
    """
    data = load_tasks()
    now = time.time()
    recovered = []

    for task in data["tasks"]:
        if task.get("status") != "in_progress":
            continue
        if agent and task.get("agent", "").upper() != agent.upper():
            continue

        locked_at = task.get("locked_at")
        if locked_at is None:
            continue

        elapsed = now - locked_at
        if elapsed > LOCK_TIMEOUT_SECONDS:
            recovered.append({**task})
            if not dry_run:
                task["status"] = "pending"
                task["locked_by"] = None
                task["locked_at"] = None
                task["updated_at"] = now
                task.setdefault("recovery_log", []).append({
                    "recovered_at": now,
                    "was_locked_by": recovered[-1].get("locked_by"),
                    "elapsed_seconds": round(elapsed),
                })

    if recovered and not dry_run:
        save_tasks(data)

    return recovered


def add_task(description: str, agent: str, priority: str = "normal") -> dict:
    """Create a new pending task."""
    data = load_tasks()
    now = time.time()
    task = {
        "id": f"task-{uuid.uuid4().hex[:8]}",
        "agent": agent.upper(),
        "description": description,
        "status": "pending",
        "priority": priority,
        "locked_by": None,
        "locked_at": None,
        "created_at": now,
        "updated_at": now,
        "recovery_log": [],
    }
    data["tasks"].append(task)
    save_tasks(data)
    return task


def lock_task(task_id: str, session_id: str) -> bool:
    """Mark task as in_progress with lock. Returns False if already locked."""
    data = load_tasks()
    now = time.time()
    for task in data["tasks"]:
        if task["id"] == task_id:
            if task["status"] == "in_progress":
                return False  # already locked
            task["status"] = "in_progress"
            task["locked_by"] = session_id
            task["locked_at"] = now
            task["updated_at"] = now
            save_tasks(data)
            return True
    return False


def complete_task(task_id: str) -> bool:
    """Mark task as completed."""
    data = load_tasks()
    now = time.time()
    for task in data["tasks"]:
        if task["id"] == task_id:
            task["status"] = "completed"
            task["locked_by"] = None
            task["locked_at"] = None
            task["updated_at"] = now
            save_tasks(data)
            return True
    return False


def list_tasks(agent: str | None = None, status: str | None = None) -> list[dict]:
    data = load_tasks()
    tasks = data["tasks"]
    if agent:
        tasks = [t for t in tasks if t.get("agent", "").upper() == agent.upper()]
    if status:
        tasks = [t for t in tasks if t.get("status") == status]
    return tasks


def main() -> None:
    parser = argparse.ArgumentParser(description="SEAL Task Lock Recovery")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--agent", default=None)
    parser.add_argument("--add", default=None, metavar="DESCRIPTION")
    parser.add_argument("--lock", default=None, metavar="TASK_ID")
    parser.add_argument("--session", default="UNKNOWN_SESSION")
    parser.add_argument("--complete", default=None, metavar="TASK_ID")
    parser.add_argument("--list", action="store_true", dest="list_tasks")
    args = parser.parse_args()

    if args.add:
        if not args.agent:
            print("ERROR: --add requiere --agent"); return
        task = add_task(args.add, args.agent)
        print(f"✅ Tarea creada: {task['id']} — {task['description'][:60]}")
        return

    if args.lock:
        ok = lock_task(args.lock, args.session)
        print(f"{'✅ Bloqueada' if ok else '⚠️  Ya bloqueada'}: {args.lock}")
        return

    if args.complete:
        ok = complete_task(args.complete)
        print(f"{'✅ Completada' if ok else '❌ No encontrada'}: {args.complete}")
        return

    if args.list_tasks:
        tasks = list_tasks(args.agent)
        if not tasks:
            print("Sin tareas.")
            return
        for t in tasks:
            locked = f" [locked by {t['locked_by']}]" if t.get("locked_by") else ""
            print(f"  {t['id']} | {t['status']:12} | {t['agent']:6} | {t['description'][:60]}{locked}")
        return

    # Default: lock recovery
    mode = "DRY RUN" if args.dry_run else "EJECUTANDO"
    recovered = recover_locks(agent=args.agent, dry_run=args.dry_run)

    if recovered:
        print(f"🔓 Lock recovery [{mode}] — {len(recovered)} tarea(s) liberada(s):")
        for t in recovered:
            print(f"   {t['id']} | {t['agent']} | {t['description'][:60]}")
            print(f"   was locked_by: {t.get('locked_by')} | elapsed: {round(time.time() - t.get('locked_at', time.time()))}s")
    else:
        print("✅ Sin locks expirados.")


if __name__ == "__main__":
    main()
