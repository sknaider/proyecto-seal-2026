#!/usr/bin/env python3
"""Read-only SOUL Cognitive Core status surface.

v1 intentionally does not write to the database. It assembles a compact
cognitive state from existing SOUL tables so the next implementation phases can
make decisions from evidence instead of chat impressions.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
from seal_secrets import pg_dsn


DEFAULT_LIMIT = 10
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SNAPSHOT_PATH = PROJECT_ROOT / "memory" / "diagnostic" / "soul_cognitive_core_snapshot.json"
DEFAULT_HEALTH_TARGETS = {
    "mcp": "http://127.0.0.1:8771/health",
    "webchat": "http://127.0.0.1:8765/api/health",
    "studio_v2": "http://127.0.0.1:3001/v2",
    "soul_app": "http://127.0.0.1:5173",
}
OPEN_STATUSES = {"pending", "open", "todo", "in_progress", "running", "active", "blocked"}
CLOSED_STATUSES = {"done", "completed", "closed", "cancelled", "canceled", "verified"}


@dataclass(frozen=True)
class CoreGap:
    id: str
    severity: str
    surface: str
    evidence: str
    next_action: str


@dataclass(frozen=True)
class Recommendation:
    priority: int
    owner: str
    action: str
    reason: str
    evidence: str


@dataclass(frozen=True)
class EpistemicItem:
    item_type: str
    subject: str
    claim: str
    confidence: float
    source_ref: str
    agent: str | None
    created_at: Any
    surface: str


def json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_status(status: str | None) -> str:
    return (status or "").strip().lower().replace("-", "_").replace(" ", "_")


def is_open_status(status: str | None) -> bool:
    normalized = normalize_status(status)
    if not normalized:
        return True
    return normalized in OPEN_STATUSES or normalized not in CLOSED_STATUSES


def public_product_surface(text: str) -> str:
    lower = text.lower()
    if "gtl" in lower or "factur" in lower or "sunat" in lower:
        return "GTL_vertical"
    if "seal core" in lower or "admin" in lower or "cockpit" in lower:
        return "SEAL_Core_private"
    if "soul core" in lower or "public" in lower or "rag" in lower or "usuario" in lower:
        return "SOUL_Core_public"
    return "core_shared"


def classify_epistemic_type(text: str, *, source: str = "", confidence: float | None = None) -> str:
    lower = text.lower()
    if any(token in lower for token in ("contradic", "conflict", "no coincide", "inconsisten")):
        return "contradiction"
    if any(token in lower for token in ("regla", "inmutable", "no violar", "siempre", "nunca")):
        return "rule"
    if any(token in lower for token in ("luz verde", "aprob", "decid", "ok ", "adelante")):
        return "decision"
    if any(token in lower for token in ("arregl", "correg", "fix", "patched", "validado", "pytest", "passed")):
        return "fix"
    if any(token in lower for token in ("error", "fallo", "bug", "se cayó", "no conecta", "failed")):
        return "error"
    if any(token in lower for token in ("prefiero", "quiero", "necesito", "me gusta", "no quiero")):
        return "preference"
    if any(token in lower for token in ("creo", "posible", "hipótesis", "hipotesis", "probable", "supongo")):
        return "hypothesis"
    if source == "belief" and confidence is not None:
        return "fact" if confidence >= 0.75 else "hypothesis"
    return "fact"


def compact_claim(text: str, *, max_len: int = 240) -> str:
    normalized = " ".join((text or "").split())
    if len(normalized) <= max_len:
        return normalized
    return normalized[: max_len - 3].rstrip() + "..."


def summarize_tasks(rows: list[asyncpg.Record]) -> dict[str, Any]:
    open_rows = [dict(row) for row in rows if is_open_status(row.get("status"))]
    blocked = [
        row
        for row in open_rows
        if "block" in normalize_status(row.get("status"))
        or "bloque" in (row.get("title") or "").lower()
        or "blocked" in (row.get("description") or "").lower()
    ]
    by_agent: dict[str, int] = {}
    for row in open_rows:
        agent = row.get("agent") or "unknown"
        by_agent[agent] = by_agent.get(agent, 0) + 1
    return {
        "open_count": len(open_rows),
        "blocked_count": len(blocked),
        "by_agent": by_agent,
        "top": open_rows[:DEFAULT_LIMIT],
        "blocked": blocked[:DEFAULT_LIMIT],
    }


def build_objective_summary(tasks: dict[str, Any], work_ledger: dict[str, Any]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for source_name, source in (("agent_tasks", tasks), ("agent_work_ledger", work_ledger)):
        for row in source.get("top", []):
            text = f"{row.get('title') or ''} {row.get('description') or ''}"
            items.append(
                {
                    "source": source_name,
                    "id": row.get("id"),
                    "agent": row.get("agent"),
                    "title": row.get("title"),
                    "status": row.get("status"),
                    "priority": row.get("priority"),
                    "surface": public_product_surface(text),
                    "evidence_present": bool(row.get("evidence")),
                    "created_at": row.get("created_at"),
                    "updated_at": row.get("updated_at"),
                }
            )

    by_surface: dict[str, int] = {}
    missing_evidence = 0
    for item in items:
        by_surface[item["surface"]] = by_surface.get(item["surface"], 0) + 1
        if not item["evidence_present"]:
            missing_evidence += 1

    return {
        "active_count": len(items),
        "by_surface": by_surface,
        "missing_evidence_count": missing_evidence,
        "top": sorted(
            items,
            key=lambda item: ((item.get("priority") or 0), str(item.get("updated_at") or "")),
            reverse=True,
        )[:DEFAULT_LIMIT],
    }


def build_knowledge_profile(
    decisions: list[asyncpg.Record],
    beliefs: list[asyncpg.Record],
    memories: list[asyncpg.Record],
    gaps: list[CoreGap],
    *,
    limit: int,
) -> dict[str, Any]:
    items: list[EpistemicItem] = []

    for row in decisions:
        content = row["content"] or ""
        items.append(
            EpistemicItem(
                item_type=classify_epistemic_type(content, source="chat"),
                subject=f"chat:{row['sender_name']}",
                claim=compact_claim(content),
                confidence=0.86,
                source_ref=f"chat_messages:{row['id']}",
                agent=row["sender_name"],
                created_at=row["created_at"],
                surface=public_product_surface(content),
            )
        )

    for row in beliefs:
        confidence = float(row["confidence"] or 0.5)
        content = row["content"] or ""
        items.append(
            EpistemicItem(
                item_type=classify_epistemic_type(content, source="belief", confidence=confidence),
                subject=row["topic"] or "belief",
                claim=compact_claim(content),
                confidence=confidence,
                source_ref=f"beliefs:{row['id']}",
                agent=row["agent"],
                created_at=row["created_at"],
                surface=public_product_surface(content),
            )
        )

    for row in memories:
        content = row["content"] or ""
        source_ref = f"memories:{row['id']}"
        confidence = min(0.95, 0.45 + (float(row["importance"] or 0) / 20.0))
        items.append(
            EpistemicItem(
                item_type=classify_epistemic_type(content, source="memory"),
                subject=row["category"] or "memory",
                claim=compact_claim(content),
                confidence=confidence,
                source_ref=source_ref,
                agent=row["agent"],
                created_at=row["created_at"],
                surface=public_product_surface(content),
            )
        )

    by_type: dict[str, int] = {}
    by_surface: dict[str, int] = {}
    for item in items:
        by_type[item.item_type] = by_type.get(item.item_type, 0) + 1
        by_surface[item.surface] = by_surface.get(item.surface, 0) + 1

    unknowns = [
        {
            "id": gap.id,
            "severity": gap.severity,
            "surface": gap.surface,
            "evidence": gap.evidence,
            "next_action": gap.next_action,
        }
        for gap in gaps
    ]

    return {
        "item_count": len(items),
        "by_type": by_type,
        "by_surface": by_surface,
        "items": [asdict(item) for item in items[:limit]],
        "unknowns": unknowns,
    }


def derive_gaps(state: dict[str, Any]) -> list[CoreGap]:
    gaps: list[CoreGap] = []

    if state["tasks"]["blocked_count"]:
        gaps.append(
            CoreGap(
                id="blocked_tasks",
                severity="high",
                surface="SEAL_Core_private",
                evidence=f"{state['tasks']['blocked_count']} tarea(s) bloqueada(s)",
                next_action="resolver bloqueos antes de abrir nuevas fases",
            )
        )

    if state["work_ledger"]["blocked_count"]:
        gaps.append(
            CoreGap(
                id="blocked_work_items",
                severity="high",
                surface="SEAL_Core_private",
                evidence=f"{state['work_ledger']['blocked_count']} work item(s) bloqueado(s)",
                next_action="auditar work ledger y cerrar/rehidratar items bloqueados",
            )
        )

    latest_bench = state.get("latest_bench_run")
    if not latest_bench:
        gaps.append(
            CoreGap(
                id="missing_bench",
                severity="medium",
                surface="core_shared",
                evidence="no hay bench_runs disponibles",
                next_action="ejecutar evaluation spine / SEAL-Bench antes de afirmar mejora cognitiva",
            )
        )
    elif latest_bench.get("failed", 0):
        gaps.append(
            CoreGap(
                id="failing_bench",
                severity="high",
                surface="core_shared",
                evidence=f"bench_run #{latest_bench.get('id')} failed={latest_bench.get('failed')}",
                next_action="revisar bench_results fallidos y crear fixes con evidencia",
            )
        )

    if not state["rag"]["has_document_index"]:
        gaps.append(
            CoreGap(
                id="rag_document_index_missing",
                severity="high",
                surface="SOUL_Core_public",
                evidence="no se detectó tabla canónica de documentos/chunks/index RAG",
                next_action="implementar índice de carpetas/docs/media antes de prometer lectura fiable de archivos",
            )
        )

    if state["recent_decisions"]["count"] == 0:
        gaps.append(
            CoreGap(
                id="recent_decisions_empty",
                severity="medium",
                surface="core_shared",
                evidence="no se encontraron decisiones recientes públicas de William/Henry",
                next_action="validar chat ingestion o ampliar ventana/filtros de decisiones",
            )
        )

    objectives = state.get("objectives") or {}
    if objectives.get("missing_evidence_count", 0) > 0:
        gaps.append(
            CoreGap(
                id="objectives_missing_evidence",
                severity="medium",
                surface="SEAL_Core_private",
                evidence=f"{objectives['missing_evidence_count']} objetivo(s)/tarea(s) sin evidencia adjunta",
                next_action="agregar contratos de intención y evidence gate antes de cerrar objetivos",
            )
        )

    down_services = [
        name for name, item in state["services"].items() if item.get("status") not in {"ok", "skipped"}
    ]
    if down_services:
        gaps.append(
            CoreGap(
                id="services_unhealthy",
                severity="high",
                surface="SEAL_Core_private",
                evidence=f"servicios con fallo: {', '.join(down_services)}",
                next_action="revisar systemd/puertos antes de ejecutar fases dependientes",
            )
        )

    return gaps


def recommend_next(gaps: list[CoreGap], state: dict[str, Any]) -> list[Recommendation]:
    recommendations: list[Recommendation] = []
    severity_priority = {"critical": 100, "high": 80, "medium": 50, "low": 20}
    owner_by_surface = {
        "SEAL_Core_private": "ADA+NEXUS",
        "SOUL_Core_public": "ADA+ALICE",
        "GTL_vertical": "ALICE+NEXUS",
        "core_shared": "ADA+JARVIS+NEXUS",
    }

    for gap in gaps:
        recommendations.append(
            Recommendation(
                priority=severity_priority.get(gap.severity, 10),
                owner=owner_by_surface.get(gap.surface, "ADA"),
                action=gap.next_action,
                reason=f"{gap.id} en {gap.surface}",
                evidence=gap.evidence,
            )
        )

    if not any(gap.id == "rag_document_index_missing" for gap in gaps):
        recommendations.append(
            Recommendation(
                priority=45,
                owner="ADA",
                action="implementar Fase 1 del Cognitive Core: status/gaps/recommend-next como API interna",
                reason="el estado read-only ya puede promoverse a contrato consumible por SEAL Core",
                evidence=f"estado generado con {state['tasks']['open_count']} tareas abiertas",
            )
        )

    return sorted(recommendations, key=lambda item: item.priority, reverse=True)


async def fetch_rows(conn: asyncpg.Connection, query: str, *args: Any) -> list[asyncpg.Record]:
    return list(await conn.fetch(query, *args))


async def fetch_latest_bench(conn: asyncpg.Connection) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        """
        SELECT id, run_at, triggered_by, total_tests, passed, failed, score_avg, elapsed_ms, git_commit
        FROM soul_v3.bench_runs
        ORDER BY id DESC
        LIMIT 1
        """
    )
    return dict(row) if row else None


async def fetch_recent_memories(conn: asyncpg.Connection, limit: int) -> list[asyncpg.Record]:
    return await fetch_rows(
        conn,
        """
        SELECT id, agent, category, content, importance, source, created_at, updated_at
        FROM soul_v3.memories
        WHERE invalid_at IS NULL
          AND importance >= 8
        ORDER BY created_at DESC
        LIMIT $1
        """,
        limit,
    )


async def fetch_rag_status(conn: asyncpg.Connection) -> dict[str, Any]:
    rows = await conn.fetch(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema='soul_v3'
          AND (
            table_name ILIKE '%document%'
            OR table_name ILIKE '%chunk%'
            OR table_name ILIKE '%rag%'
            OR table_name ILIKE '%file%'
            OR table_name ILIKE '%media%'
          )
        ORDER BY table_name
        """
    )
    tables = [row["table_name"] for row in rows]
    canonical = [name for name in tables if any(part in name for part in ("document", "chunk", "rag"))]
    return {
        "candidate_tables": tables,
        "has_document_index": bool(canonical),
    }


def check_url(url: str, timeout: float = 0.8) -> dict[str, Any]:
    try:
        request = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return {"status": "ok", "code": response.status, "url": url}
    except urllib.error.HTTPError as exc:
        if exc.code < 500:
            return {"status": "ok", "code": exc.code, "url": url}
        return {"status": "error", "code": exc.code, "url": url, "error": str(exc)}
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        return {"status": "error", "url": url, "error": str(exc)}


def service_targets_from_env() -> dict[str, str]:
    targets = dict(DEFAULT_HEALTH_TARGETS)
    raw = os.environ.get("SOUL_COGNITIVE_CORE_HEALTH_TARGETS", "").strip()
    if not raw:
        return targets
    for item in raw.split(","):
        if "=" not in item:
            continue
        name, url = item.split("=", 1)
        if name.strip() and url.strip():
            targets[name.strip()] = url.strip()
    return targets


def check_services(skip_network: bool = False) -> dict[str, dict[str, Any]]:
    targets = service_targets_from_env()
    if skip_network:
        return {name: {"status": "skipped", "url": url} for name, url in targets.items()}
    return {name: check_url(url) for name, url in targets.items()}


async def build_state(conn: asyncpg.Connection, *, limit: int, skip_network: bool) -> dict[str, Any]:
    tasks = await fetch_rows(
        conn,
        """
        SELECT id, agent, title, description, status, priority, requested_by, source, source_ref,
               source_chat_id, created_at, updated_at, completed_at, evidence
        FROM soul_v3.agent_tasks
        WHERE completed_at IS NULL
           OR LOWER(COALESCE(status, '')) NOT IN ('done', 'completed', 'closed', 'cancelled', 'canceled', 'verified')
        ORDER BY priority DESC NULLS LAST, updated_at DESC NULLS LAST, created_at DESC NULLS LAST
        LIMIT $1
        """,
        max(limit * 3, DEFAULT_LIMIT),
    )
    work_items = await fetch_rows(
        conn,
        """
        SELECT id, agent, title, description, status, priority, requested_by, source, source_ref,
               source_chat_id, source_task_id, created_at, updated_at, completed_at, evidence
        FROM soul_v3.agent_work_ledger
        WHERE completed_at IS NULL
           OR LOWER(COALESCE(status, '')) NOT IN ('done', 'completed', 'closed', 'cancelled', 'canceled', 'verified')
        ORDER BY priority DESC NULLS LAST, updated_at DESC NULLS LAST, created_at DESC NULLS LAST
        LIMIT $1
        """,
        max(limit * 3, DEFAULT_LIMIT),
    )
    decisions = await fetch_rows(
        conn,
        """
        SELECT id, sender_name, channel, content, created_at
        FROM soul_v3.chat_messages
        WHERE channel='web_chat'
          AND sender_name IN ('William', 'Henry')
          AND created_at > NOW() - INTERVAL '72 hours'
          AND (
            content ILIKE '%aprob%'
            OR content ILIKE '%luz verde%'
            OR content ILIKE '%quiero%'
            OR content ILIKE '%regla%'
            OR content ILIKE '%decisi%'
            OR content ILIKE '%adelante%'
            OR content ILIKE '%ok%'
          )
        ORDER BY id DESC
        LIMIT $1
        """,
        limit,
    )
    beliefs = await fetch_rows(
        conn,
        """
        SELECT id, agent, topic, content, confidence, evidence_count, valid_from, invalid_at, created_at, metadata
        FROM soul_v3.beliefs
        WHERE invalid_at IS NULL
        ORDER BY confidence DESC NULLS LAST, evidence_count DESC NULLS LAST, created_at DESC
        LIMIT $1
        """,
        limit,
    )

    latest_bench = await fetch_latest_bench(conn)
    rag = await fetch_rag_status(conn)
    services = check_services(skip_network=skip_network)
    memories = await fetch_recent_memories(conn, limit)
    task_summary = summarize_tasks(tasks)
    work_summary = summarize_tasks(work_items)
    objectives = build_objective_summary(task_summary, work_summary)

    state = {
        "generated_at": utc_now_iso(),
        "mode": "read_only",
        "product_boundaries": {
            "SOUL_Core_public": "producto/plataforma publica: chat, memoria, RAG, agentes, objetivos, herramientas autorizadas",
            "SEAL_Core_private": "cockpit privado de William: agentes, DB, MCP, bridges, servicios, auditoria, deploy y rollback",
            "GTL_vertical": "app vertical conectada; caso real, no centro de arquitectura",
        },
        "tasks": task_summary,
        "work_ledger": work_summary,
        "objectives": objectives,
        "recent_decisions": {
            "count": len(decisions),
            "items": [dict(row) | {"surface": public_product_surface(row["content"] or "")} for row in decisions],
        },
        "beliefs": {
            "count": len(beliefs),
            "top": [dict(row) for row in beliefs],
        },
        "latest_bench_run": latest_bench,
        "rag": rag,
        "services": services,
    }
    gaps = derive_gaps(state)
    state["gaps"] = [asdict(gap) for gap in gaps]
    state["knowledge"] = build_knowledge_profile(decisions, beliefs, memories, gaps, limit=limit)
    state["recommendations"] = [asdict(item) for item in recommend_next(gaps, state)]
    return state


async def run(args: argparse.Namespace) -> dict[str, Any]:
    conn = await asyncpg.connect(pg_dsn(required=True))
    try:
        state = await build_state(conn, limit=args.limit, skip_network=args.skip_network)
    finally:
        await conn.close()

    if args.command == "status":
        return state
    if args.command == "gaps":
        return {
            "generated_at": state["generated_at"],
            "mode": state["mode"],
            "gaps": state["gaps"],
        }
    if args.command == "recommend-next":
        return {
            "generated_at": state["generated_at"],
            "mode": state["mode"],
            "recommendations": state["recommendations"],
        }
    if args.command == "knowledge":
        return {
            "generated_at": state["generated_at"],
            "mode": state["mode"],
            "knowledge": state["knowledge"],
        }
    if args.command == "objectives":
        return {
            "generated_at": state["generated_at"],
            "mode": state["mode"],
            "objectives": state["objectives"],
        }
    if args.command == "snapshot":
        path = Path(args.output or DEFAULT_SNAPSHOT_PATH)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=json_default) + "\n")
        return {
            "generated_at": state["generated_at"],
            "mode": state["mode"],
            "snapshot_path": str(path),
            "tasks_open": state["tasks"]["open_count"],
            "work_open": state["work_ledger"]["open_count"],
            "knowledge_items": state["knowledge"]["item_count"],
            "objectives_active": state["objectives"]["active_count"],
            "gaps": [gap["id"] for gap in state["gaps"]],
        }
    raise ValueError(f"unknown command: {args.command}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SOUL Cognitive Core read-only status")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--skip-network", action="store_true", help="do not probe HTTP health targets")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("gaps")
    sub.add_parser("recommend-next")
    sub.add_parser("knowledge")
    sub.add_parser("objectives")
    snapshot = sub.add_parser("snapshot")
    snapshot.add_argument("--output", default=str(DEFAULT_SNAPSHOT_PATH))
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    payload = asyncio.run(run(args))
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
