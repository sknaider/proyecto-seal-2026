"""ALICE orchestrator — team-coordination engine (v0.2 addition).

Receives an objective from William, decomposes into tasks, dispatches each
to the right agent (JARVIS strategy, ADA infra, NEXUS execution, DUM monitor),
tracks progress, consolidates results.

Designed for: William ausente >1min → ALICE coordinates.
Never invades roles — only routes work and consolidates.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

_BASE = Path(__file__).parent.parent
PLAN_LOG = _BASE / "state" / "orchestration_plans.jsonl"


AgentName = Literal["JARVIS", "ADA", "NEXUS", "DUM", "ALICE"]
TaskStatus = Literal["pending", "dispatched", "in_progress", "completed", "failed", "blocked"]


@dataclass
class Task:
    id: str
    title: str
    owner: AgentName
    rationale: str
    inputs: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    status: TaskStatus = "pending"
    result: str | None = None
    sla_seconds: int = 600


@dataclass
class Plan:
    id: str
    objective: str
    tasks: list[Task]
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "objective": self.objective,
            "created_at": self.created_at,
            "tasks": [t.__dict__ for t in self.tasks],
        }


def _persist(entry: dict) -> None:
    PLAN_LOG.parent.mkdir(parents=True, exist_ok=True)
    with PLAN_LOG.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def new_plan(objective: str) -> Plan:
    return Plan(id=uuid.uuid4().hex[:12], objective=objective, tasks=[])


def add_task(
    plan: Plan,
    title: str,
    owner: AgentName,
    rationale: str,
    inputs: dict[str, Any] | None = None,
    depends_on: list[str] | None = None,
    sla_seconds: int = 600,
) -> Task:
    t = Task(
        id=uuid.uuid4().hex[:8],
        title=title,
        owner=owner,
        rationale=rationale,
        inputs=inputs or {},
        depends_on=depends_on or [],
        sla_seconds=sla_seconds,
    )
    plan.tasks.append(t)
    return t


def ready_tasks(plan: Plan) -> list[Task]:
    done_ids = {t.id for t in plan.tasks if t.status == "completed"}
    return [
        t for t in plan.tasks
        if t.status == "pending" and all(d in done_ids for d in t.depends_on)
    ]


def mark(plan: Plan, task_id: str, status: TaskStatus, result: str | None = None) -> bool:
    for t in plan.tasks:
        if t.id == task_id:
            t.status = status
            if result is not None:
                t.result = result
            _persist({"plan_id": plan.id, "task_id": task_id, "status": status, "result": result, "ts": datetime.now(timezone.utc).isoformat()})
            return True
    return False


def progress(plan: Plan) -> dict:
    total = len(plan.tasks)
    by_status: dict[str, int] = {}
    for t in plan.tasks:
        by_status[t.status] = by_status.get(t.status, 0) + 1
    completed = by_status.get("completed", 0)
    return {
        "plan_id": plan.id,
        "objective": plan.objective,
        "total": total,
        "by_status": by_status,
        "completion_pct": round(100 * completed / total, 1) if total else 0.0,
        "ready_now": [t.id for t in ready_tasks(plan)],
    }


def consolidate(plan: Plan) -> str:
    parts = [f"Objetivo: {plan.objective}", ""]
    for t in plan.tasks:
        prefix = {"completed": "✅", "failed": "❌", "in_progress": "⏳", "blocked": "🚫", "dispatched": "📤", "pending": "⚪"}.get(t.status, "?")
        parts.append(f"{prefix} [{t.owner}] {t.title}")
        if t.result:
            parts.append(f"   → {t.result[:200]}")
    return "\n".join(parts)
