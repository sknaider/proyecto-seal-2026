#!/usr/bin/env python3
"""Manual shadow loop for SEAL continuous awareness.

Fase 4 wires collector -> governor -> ledger -> optional local proposal. It is a
CLI/manual loop only: no systemd unit, no daemon, no webchat publishing, no code
edits, no service restart.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from attention_governor import decide_attention
from awareness_collector import normalize_payload, shadow_fixture_events
from awareness_ledger import cleanup_awareness_agent, connect_db, record_awareness_tick
from awareness_reflexes import execute_reflex
from awareness_types import AttentionDecision, AwarenessEvent
from local_runtime_service import LocalRuntimeConfig, LocalRuntimeResult, reflect


@dataclass(frozen=True)
class AwarenessLoopItem:
    event: dict[str, Any]
    decision: dict[str, Any]
    tick: dict[str, Any] | None = None
    local_proposal: dict[str, Any] | None = None
    reflex_execution: dict[str, Any] | None = None


@dataclass(frozen=True)
class AwarenessLoopRun:
    agent: str
    mode: str
    dry_run: bool
    persisted: bool
    processed: int
    action_counts: dict[str, int]
    local_proposals: int
    reflex_executions: int
    blocked: int
    items: list[AwarenessLoopItem] = field(default_factory=list)
    boundary: str = "manual_shadow_loop_no_daemon_no_publish"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["items"] = [asdict(item) for item in self.items]
        return payload


def _should_request_local_proposal(decision: AttentionDecision, enable_local: bool) -> bool:
    return enable_local and decision.action == "local_reflect"


def _should_execute_reflex(decision: AttentionDecision, enable_reflex: bool) -> bool:
    return enable_reflex and (decision.action == "reflex_action" or bool(decision.blocked))


def _context_for_local(event: AwarenessEvent, decision: AttentionDecision) -> str:
    return json.dumps(
        {
            "event": event.to_dict(),
            "decision": decision.to_dict(),
            "instruction": "Return proposal-only JSON. Do not claim execution.",
        },
        ensure_ascii=False,
        sort_keys=True,
    )


async def run_awareness_loop(
    events: Iterable[AwarenessEvent],
    *,
    agent: str = "ADA",
    dry_run: bool = True,
    enable_local: bool = False,
    enable_reflex: bool = False,
    local_config: LocalRuntimeConfig = LocalRuntimeConfig(),
) -> AwarenessLoopRun:
    action_counts: dict[str, int] = {}
    items: list[AwarenessLoopItem] = []
    local_count = 0
    reflex_count = 0
    blocked_count = 0
    conn = None if dry_run else await connect_db()
    try:
        for event in events:
            decision = decide_attention(event)
            action_counts[decision.action] = action_counts.get(decision.action, 0) + 1
            blocked_count += int(decision.blocked)

            tick_payload = None
            if conn is not None:
                record = await record_awareness_tick(conn, event, decision, outcome="manual_shadow_loop")
                tick_payload = record.to_dict()

            local_payload = None
            if _should_request_local_proposal(decision, enable_local):
                proposal: LocalRuntimeResult = reflect(_context_for_local(event, decision), local_config)
                local_payload = proposal.to_dict()
                local_count += 1

            reflex_payload = None
            if _should_execute_reflex(decision, enable_reflex):
                execution = execute_reflex(event, decision)
                reflex_payload = execution.to_dict()
                reflex_count += 1

            items.append(
                AwarenessLoopItem(
                    event=event.to_dict(),
                    decision=decision.to_dict(),
                    tick=tick_payload,
                    local_proposal=local_payload,
                    reflex_execution=reflex_payload,
                )
            )
    finally:
        if conn is not None:
            await conn.close()

    return AwarenessLoopRun(
        agent=agent.upper(),
        mode="shadow",
        dry_run=dry_run,
        persisted=not dry_run,
        processed=len(items),
        action_counts=action_counts,
        local_proposals=local_count,
        reflex_executions=reflex_count,
        blocked=blocked_count,
        items=items,
    )


async def run_fixture_loop(
    *,
    agent: str = "ADA",
    count: int = 10,
    dry_run: bool = True,
    enable_local: bool = False,
    enable_reflex: bool = False,
) -> AwarenessLoopRun:
    return await run_awareness_loop(
        shadow_fixture_events(agent, count),
        agent=agent,
        dry_run=dry_run,
        enable_local=enable_local,
        enable_reflex=enable_reflex,
    )


async def run_payload_loop(
    payloads: list[dict[str, Any]],
    *,
    agent: str = "ADA",
    dry_run: bool = True,
    enable_local: bool = False,
    enable_reflex: bool = False,
) -> AwarenessLoopRun:
    events = [normalize_payload(payload, agent) for payload in payloads]
    return await run_awareness_loop(
        events,
        agent=agent,
        dry_run=dry_run,
        enable_local=enable_local,
        enable_reflex=enable_reflex,
    )


async def evaluate_awareness_loop_contract_async() -> dict[str, Any]:
    temp_agent = "ADA_LOOP_TEST"
    dry = await run_fixture_loop(agent=temp_agent, count=10, dry_run=True, enable_local=False, enable_reflex=True)
    persisted = await run_fixture_loop(agent=temp_agent, count=10, dry_run=False, enable_local=False, enable_reflex=True)

    conn = await connect_db()
    try:
        cleanup_deleted = await cleanup_awareness_agent(conn, temp_agent)
    finally:
        await conn.close()
    checks = {
        "dry_run_processed_10": dry.processed == 10 and not dry.persisted,
        "dry_run_no_ticks": all(item.tick is None for item in dry.items),
        "persisted_processed_10": persisted.processed == 10 and persisted.persisted,
        "persisted_ticks_written": all(item.tick and item.tick.get("db_id") for item in persisted.items),
        "reflexes_are_limited": persisted.reflex_executions >= 2
        and all(
            not item.reflex_execution or item.reflex_execution["boundary"] == "limited_reflex_executor_no_mutation"
            for item in persisted.items
        ),
        "action_mix_present": {"ignore", "wake_codex", "local_reflect", "reflex_action"}.issubset(set(persisted.action_counts)),
        "blocked_events_counted": persisted.blocked >= 2,
        "cleanup_removed_rows": cleanup_deleted >= 11,
        "boundary_shadow_only": dry.boundary == "manual_shadow_loop_no_daemon_no_publish",
    }
    return {
        "checks": checks,
        "passed": sum(1 for ok in checks.values() if ok),
        "total": len(checks),
        "dry_run": dry.to_dict(),
        "persisted": persisted.to_dict(),
        "cleanup_deleted": cleanup_deleted,
    }


def evaluate_awareness_loop_contract() -> dict[str, Any]:
    return asyncio.run(evaluate_awareness_loop_contract_async())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SEAL manual awareness shadow loop")
    parser.add_argument("--agent", default="ADA")
    parser.add_argument("--persist", action="store_true")
    parser.add_argument("--enable-local", action="store_true")
    parser.add_argument("--enable-reflex", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    fixture = sub.add_parser("fixture")
    fixture.add_argument("--count", type=int, default=10)
    payload = sub.add_parser("payload")
    payload.add_argument("--json", required=True, help="Single event object or list of event objects")
    sub.add_parser("contract")
    return parser


def _json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


async def main_async(args: argparse.Namespace) -> int:
    if args.command == "fixture":
        run = await run_fixture_loop(
            agent=args.agent,
            count=args.count,
            dry_run=not args.persist,
            enable_local=args.enable_local,
            enable_reflex=args.enable_reflex,
        )
        print(json.dumps(run.to_dict(), indent=2, default=_json_default, ensure_ascii=False))
        return 0
    if args.command == "payload":
        raw = json.loads(args.json)
        payloads = raw if isinstance(raw, list) else [raw]
        run = await run_payload_loop(
            payloads,
            agent=args.agent,
            dry_run=not args.persist,
            enable_local=args.enable_local,
            enable_reflex=args.enable_reflex,
        )
        print(json.dumps(run.to_dict(), indent=2, default=_json_default, ensure_ascii=False))
        return 0
    if args.command == "contract":
        payload = evaluate_awareness_loop_contract()
        print(json.dumps(payload, indent=2, default=_json_default, ensure_ascii=False))
        return 0 if payload["passed"] == payload["total"] else 2
    raise AssertionError(f"Unhandled command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
