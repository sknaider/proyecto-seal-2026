#!/usr/bin/env python3
"""SOUL Executive Event Router — dry-run classifier.

This is intentionally read-only. It classifies recent chat events into routing
actions so we can tune rules before any daemon starts enforcing them.
"""

from __future__ import annotations
from seal_secrets import pg_dsn

import argparse
import asyncio
import json
import os
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg

DB_URL = os.environ.get(
    "SEAL_PG_DSN",
    pg_dsn(required=True),
)
REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "agents/ADA"
AGENTS = ("ADA", "ALICE", "JARVIS", "NEXUS", "DUM")
SYSTEM_TYPES = {"heartbeat", "awareness", "system", "hb", "status", "system_alive", "nerves_fire"}
DIAGNOSTIC_CHANNELS = {"seal_diagnostic", "diagnostic", "diagnostics"}


@dataclass
class RouteDecision:
    event_id: int
    sender: str
    channel: str
    message_type: str
    action: str
    target_agent: str | None
    runtime: str | None
    budget_class: str
    confidence: float
    reason: str
    created_at: str
    content_preview: str


def clean(text: str | None) -> str:
    return (text or "").strip()


def runtime_for_agent(agent: str | None) -> str | None:
    if not agent:
        return None
    return "codex" if agent == "ADA" else "claude"


def mentioned_agents(text: str) -> list[str]:
    upper = text.upper()
    hits = []
    for agent in AGENTS:
        if re.search(rf"(^|[^A-Z])@?{agent}([^A-Z]|$)", upper):
            hits.append(agent)
    return hits


def metadata_to(metadata: Any) -> str | None:
    if not metadata:
        return None
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            return None
    if isinstance(metadata, dict):
        val = metadata.get("to")
        return str(val) if val else None
    return None


def parse_created_at(value: str) -> datetime | None:
    if not value or value == "None":
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def is_low_value(content: str, channel: str, message_type: str) -> bool:
    if message_type in SYSTEM_TYPES:
        return True
    if channel in DIAGNOSTIC_CHANNELS and re.search(r"\b(HB|heartbeat|alive|ok)\b", content, re.I):
        return True
    return bool(re.search(r"\bheartbeat\b|\[HB\]|\balive\b|status\s*ok|catchup", content, re.I))


def is_research_loop(content: str) -> bool:
    return bool(re.search(r"\[RESEARCH LOOP\]|si no hay trabajo urgente.*investiga mejoras", content, re.I))


def is_code_task(content: str) -> bool:
    return bool(re.search(
        r"\b(arregla|corrige|implementa|repara|testea|build|lint|refactor|repo|codigo|código|frontend|backend|daemon|servicio|bug|fix|spec)\b",
        content,
        re.I,
    ))


def is_security_or_runtime(content: str) -> bool:
    return bool(re.search(
        r"\b(seguridad|security|vulnerab|audit|auditor|riesgo|critico|crítico|caido|caído|down|error|traceback|servicio|systemd|daemon|gpu|db|postgres|password|token|oauth|login|relogin|matar|kill|pkill|rm -rf|drop|delete)\b",
        content,
        re.I,
    ))


def is_strategy(content: str) -> bool:
    return bool(re.search(
        r"\b(arquitectura|estrategia|plan|diseño|spec|roadmap|mejoras|agi|producto|mercado|decide|prioriza)\b",
        content,
        re.I,
    ))


def is_ui(content: str) -> bool:
    return bool(re.search(r"\b(ui|frontend|soul app|app 2|vista|bot[oó]n|dashboard|react|vite|tsx|pantalla)\b", content, re.I))


def is_team_coordination(content: str) -> bool:
    return bool(re.search(
        r"\b(equipo|cada uno|cada agente|hermanos|no se pisen|sin pisarse|alinead[oa]s?|coordinen|conversen|contin[uú]en|libre albedr[ií]o|avancen)\b",
        content,
        re.I,
    ))


def is_progress_or_status_question(content: str) -> bool:
    return bool(re.search(
        r"\b(c[oó]mo van|como van|qu[eé] novedades|que novedades|qu[eé]\s+.*realizaron|ya terminaron|terminaron|expl[ií]came.*trabaj[oó]|compruebo el trabajo)\b",
        content,
        re.I,
    ))


def is_model_or_capability_question(content: str) -> bool:
    return bool(re.search(r"\b(codex|anthropic|antropic|modelo|capacidad|eficiencia|super poderes|superpoderes)\b", content, re.I))


def route_event(row: dict[str, Any]) -> RouteDecision:
    event_id = int(row["id"])
    sender = str(row.get("sender_name") or "unknown")
    channel = str(row.get("channel") or "")
    message_type = str(row.get("message_type") or "")
    content = clean(row.get("content"))
    created_at = row.get("created_at")
    created = created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at)
    to = metadata_to(row.get("metadata"))
    mentions = mentioned_agents(content)
    preview = content.replace("\n", " ")[:180]

    if is_research_loop(content):
        return RouteDecision(event_id, sender, channel, message_type, "persist_only", None, None, "zero", 0.98, "scheduled research loop prompt; persist without wakeup", created, preview)

    if is_low_value(content, channel, message_type):
        return RouteDecision(event_id, sender, channel, message_type, "persist_only", None, None, "zero", 0.98, "low-value diagnostic/system event", created, preview)

    if channel.startswith("dm:"):
        if is_low_value(content, channel, message_type):
            return RouteDecision(event_id, sender, channel, message_type, "persist_only", None, None, "zero", 0.98, "low-value private diagnostic/nerves event", created, preview)
        target = None
        for agent in AGENTS:
            if agent.lower() in channel.lower():
                target = agent
                break
        return RouteDecision(event_id, sender, channel, message_type, "wake_agent", target, runtime_for_agent(target), "normal", 0.9, "private DM for agent", created, preview)

    if sender.upper() == "WILLIAM":
        target = mentions[0] if mentions else None
        explicit_to = (to or "").upper()
        if not target and explicit_to in AGENTS:
            target = explicit_to

        if target and is_code_task(content):
            runtime = runtime_for_agent(target)
            action = "delegate_codex" if runtime == "codex" else "wake_agent"
            return RouteDecision(event_id, sender, channel, message_type, action, target, runtime, "code_heavy", 0.92, "William direct technical/code request", created, preview)

        if target:
            return RouteDecision(event_id, sender, channel, message_type, "wake_agent", target, runtime_for_agent(target), "normal", 0.9, "William direct agent call", created, preview)

        if is_team_coordination(content) or is_progress_or_status_question(content):
            return RouteDecision(event_id, sender, channel, message_type, "wake_team", None, "mixed", "normal", 0.86, "William team coordination/progress message", created, preview)

        if is_security_or_runtime(content):
            return RouteDecision(event_id, sender, channel, message_type, "wake_agent", "NEXUS", "claude", "deep", 0.82, "William runtime/security question without explicit target", created, preview)

        if is_code_task(content):
            return RouteDecision(event_id, sender, channel, message_type, "delegate_codex", "ADA", "codex", "code_heavy", 0.84, "William implementation request without explicit target", created, preview)

        if is_ui(content):
            return RouteDecision(event_id, sender, channel, message_type, "wake_agent", "ALICE", "claude", "normal", 0.78, "William UI/product question without explicit target", created, preview)

        if is_strategy(content):
            return RouteDecision(event_id, sender, channel, message_type, "wake_agent", "JARVIS", "claude", "deep", 0.78, "William strategy/architecture question without explicit target", created, preview)

        if is_model_or_capability_question(content):
            return RouteDecision(event_id, sender, channel, message_type, "wake_agent", "JARVIS", "claude", "normal", 0.78, "William model/capability question without explicit target", created, preview)

        return RouteDecision(event_id, sender, channel, message_type, "wake_agent", "JARVIS", "claude", "normal", 0.68, "William general team message; JARVIS orchestrates by default", created, preview)

    if sender.upper() in AGENTS:
        # Agent outputs to William should be persisted and visible, not recursively wake others.
        if (to or "").lower() == "william":
            return RouteDecision(event_id, sender, channel, message_type, "persist_only", None, None, "zero", 0.86, "agent response to William", created, preview)
        if message_type == "whisper":
            targets = mentioned_agents(content)
            target = targets[0] if targets else None
            return RouteDecision(event_id, sender, channel, message_type, "wake_agent" if target else "persist_only", target, runtime_for_agent(target), "normal" if target else "zero", 0.74, "agent whisper routed only to mentioned target", created, preview)
        return RouteDecision(event_id, sender, channel, message_type, "persist_only", None, None, "zero", 0.7, "agent chatter should not recursively wake team", created, preview)

    return RouteDecision(event_id, sender, channel, message_type, "persist_only", None, None, "zero", 0.5, "default safe persistence", created, preview)


async def fetch_chat(hours: int, limit: int) -> list[dict[str, Any]]:
    conn = await asyncpg.connect(DB_URL)
    try:
        rows = await conn.fetch(
            f"""
            SELECT id, sender_name, channel, message_type, content, metadata, created_at
            FROM soul_v3.chat_messages
            WHERE created_at >= now() - interval '{hours} hours'
            ORDER BY created_at ASC
            LIMIT $1
            """,
            limit,
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def ensure_ledger_table(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS soul_v3.router_decision_ledger (
            id BIGSERIAL PRIMARY KEY,
            event_id BIGINT NOT NULL UNIQUE,
            source_table TEXT NOT NULL DEFAULT 'soul_v3.chat_messages',
            sender TEXT NOT NULL,
            channel TEXT NOT NULL,
            message_type TEXT NOT NULL,
            action TEXT NOT NULL,
            target_agent TEXT,
            runtime TEXT,
            budget_class TEXT NOT NULL,
            confidence DOUBLE PRECISION NOT NULL,
            reason TEXT NOT NULL,
            content_preview TEXT NOT NULL,
            event_created_at TIMESTAMPTZ,
            decided_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            dry_run BOOLEAN NOT NULL DEFAULT true
        )
        """
    )
    await conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_router_decision_ledger_decided_at
        ON soul_v3.router_decision_ledger (decided_at DESC)
        """
    )
    await conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_router_decision_ledger_action
        ON soul_v3.router_decision_ledger (action, target_agent)
        """
    )


async def persist_ledger(decisions: list[RouteDecision], dry_run: bool = True) -> int:
    if not decisions:
        return 0

    conn = await asyncpg.connect(DB_URL)
    try:
        await ensure_ledger_table(conn)
        rows = [
            (
                d.event_id,
                d.sender,
                d.channel,
                d.message_type,
                d.action,
                d.target_agent,
                d.runtime,
                d.budget_class,
                d.confidence,
                d.reason,
                d.content_preview,
                parse_created_at(d.created_at),
                dry_run,
            )
            for d in decisions
        ]
        await conn.executemany(
            """
            INSERT INTO soul_v3.router_decision_ledger (
                event_id, sender, channel, message_type, action, target_agent,
                runtime, budget_class, confidence, reason, content_preview,
                event_created_at, dry_run
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::timestamptz, $13)
            ON CONFLICT (event_id) DO UPDATE SET
                sender = EXCLUDED.sender,
                channel = EXCLUDED.channel,
                message_type = EXCLUDED.message_type,
                action = EXCLUDED.action,
                target_agent = EXCLUDED.target_agent,
                runtime = EXCLUDED.runtime,
                budget_class = EXCLUDED.budget_class,
                confidence = EXCLUDED.confidence,
                reason = EXCLUDED.reason,
                content_preview = EXCLUDED.content_preview,
                event_created_at = EXCLUDED.event_created_at,
                decided_at = now(),
                dry_run = EXCLUDED.dry_run
            """,
            rows,
        )
        return len(rows)
    finally:
        await conn.close()


def md_table(rows: list[dict[str, Any]], columns: list[str], limit: int | None = None) -> str:
    rows = rows[:limit] if limit else rows
    if not rows:
        return "_Sin datos._\n"
    header = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    body = ["| " + " | ".join(str(r.get(c, "")) for c in columns) + " |" for r in rows]
    return "\n".join([header, sep, *body]) + "\n"


def write_report(decisions: list[RouteDecision], md_path: Path, hours: int, ledger_persisted: bool = False) -> None:
    total = len(decisions)
    action_counts = Counter(d.action for d in decisions)
    target_counts = Counter(d.target_agent for d in decisions if d.target_agent)
    budget_counts = Counter(d.budget_class for d in decisions)
    runtime_counts = Counter(d.runtime for d in decisions if d.runtime)
    zeroish = action_counts["persist_only"]
    would_wake = total - zeroish
    codex = action_counts["delegate_codex"]

    sample_rows = [
        {
            "id": d.event_id,
            "sender": d.sender,
            "action": d.action,
            "target": d.target_agent or "",
            "budget": d.budget_class,
            "reason": d.reason,
            "preview": d.content_preview.replace("|", "\\|")[:80],
        }
        for d in decisions
        if d.action != "persist_only"
    ]
    low_conf = [
        {
            "id": d.event_id,
            "sender": d.sender,
            "action": d.action,
            "target": d.target_agent or "",
            "confidence": d.confidence,
            "reason": d.reason,
            "preview": d.content_preview.replace("|", "\\|")[:90],
        }
        for d in decisions
        if d.confidence < 0.7
    ]

    lines = [
        "# Executive Event Router — Dry Run Report",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        f"Window: last {hours} hours",
        f"Events classified: {total}",
        "",
        "## Summary",
        "",
        f"- `persist_only`: {zeroish} ({100*zeroish/total:.1f}% if total else 0) — no LLM wakeup.",
        f"- Would wake/delegate: {would_wake} ({100*would_wake/total:.1f}% if total else 0).",
        f"- Would delegate to Codex: {codex}.",
        "",
        "## Action Counts",
        "",
        md_table([{"action": k, "count": v} for k, v in action_counts.most_common()], ["action", "count"]),
        "## Target Counts",
        "",
        md_table([{"target": k, "count": v} for k, v in target_counts.most_common()], ["target", "count"]),
        "## Budget Counts",
        "",
        md_table([{"budget": k, "count": v} for k, v in budget_counts.most_common()], ["budget", "count"]),
        "## Runtime Counts",
        "",
        md_table([{"runtime": k, "count": v} for k, v in runtime_counts.most_common()], ["runtime", "count"]),
        "## Non-persist Decisions",
        "",
        md_table(sample_rows, ["id", "sender", "action", "target", "budget", "reason", "preview"], 50),
        "## Low Confidence Decisions",
        "",
        md_table(low_conf, ["id", "sender", "action", "target", "confidence", "reason", "preview"], 30),
        "## Interpretation",
        "",
        "- This is dry-run only: no daemon enforcement and no wakeups.",
        f"- Ledger persistence: {'enabled' if ledger_persisted else 'disabled'}.",
        "- `persist_only` is the main saving path: it keeps visibility without spending model tokens.",
        "- Low confidence rows should become fixtures before enforcement.",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=6)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--json-out", default="")
    parser.add_argument("--md-out", default="")
    parser.add_argument("--persist-ledger", action="store_true", help="Persist dry-run decisions to soul_v3.router_decision_ledger.")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    json_path = Path(args.json_out) if args.json_out else OUT_DIR / f"executive_router_dryrun_{args.hours}h_{ts}.json"
    md_path = Path(args.md_out) if args.md_out else OUT_DIR / f"report_executive_router_dryrun_{args.hours}h_{ts}.md"

    rows = await fetch_chat(args.hours, args.limit)
    decisions = [route_event(row) for row in rows]
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "hours": args.hours,
        "count": len(decisions),
        "decisions": [asdict(d) for d in decisions],
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    ledger_count = await persist_ledger(decisions, dry_run=True) if args.persist_ledger else 0
    write_report(decisions, md_path, args.hours, ledger_persisted=args.persist_ledger)

    counts = Counter(d.action for d in decisions)
    print(f"JSON: {json_path}")
    print(f"MD:   {md_path}")
    if args.persist_ledger:
        print(f"Ledger rows upserted: {ledger_count}")
    print("Actions:", dict(counts))


if __name__ == "__main__":
    asyncio.run(main())
