#!/usr/bin/env python3
"""Process at most one agent NERVES local tool-less handoff per invocation."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from memory.nerves_agent_mission_core import (
    AgentMissionError,
    ROUTES,
    NervesRouteConfig,
    _secure_json,
    load_delivery,
    stale_claim_mission_ids,
    stale_claim_scan,
)
from memory.nerves_ollama_runtime_adapter import (
    OllamaRuntimeError,
    fail_ollama_claim_validation,
    fail_stale_ollama_claim,
    run_ollama_mission,
)
from memory.nerves_mission_handoff import _load_state


TERMINAL_DELIVERY_STATES = frozenset({"completed", "failed", "abstained"})


def joined_claim_conflict(
    mission_id: str,
    *,
    error: Exception,
    route: NervesRouteConfig,
) -> dict[str, object] | None:
    """Classify an atomic-claim race as a successful idempotent join.

    A timer and a canary may both observe the same pending delivery before
    either claims it.  Losing that race is normal concurrency, not a worker
    failure.  Only the exact claim-conflict error is joinable, and the durable
    delivery must already be claimed or terminal; every other error remains
    fail-closed.
    """
    if str(error) != "handoff_not_claimable":
        return None
    record = load_delivery(mission_id, route=route)
    status = str(record.get("status") or "")
    if status not in TERMINAL_DELIVERY_STATES:
        return None
    claim = record.get("claim")
    if not isinstance(claim, dict) or not str(claim.get("claim_id") or ""):
        return None
    receipt_meta = record.get("receipt")
    receipt_path_raw = record.get("receipt_path")
    if (
        not isinstance(receipt_meta, dict)
        or not isinstance(receipt_path_raw, str)
        or receipt_meta.get("path") != receipt_path_raw
        or not str(receipt_meta.get("sha256") or "")
    ):
        return None
    try:
        receipt, receipt_raw = _secure_json(
            Path(receipt_path_raw), label="joined_terminal_receipt"
        )
    except (AgentMissionError, OSError, ValueError):
        return None
    if (
        hashlib.sha256(receipt_raw).hexdigest()
        != str(receipt_meta["sha256"])
        or receipt.get("mission_id") != mission_id
        or receipt.get("status") != status
        or receipt.get("claim_id") != claim["claim_id"]
        or receipt.get("worker_id") != claim.get("worker_id")
    ):
        return None
    joined: dict[str, object] = {
        "ok": True,
        "mission_id": mission_id,
        "status": "joined",
        "delivery_status": status,
        "claim_id": str(claim["claim_id"]),
        "joined_existing_claim": True,
    }
    joined["receipt_sha256"] = str(receipt_meta["sha256"])
    return joined


def pending_mission_ids(route: NervesRouteConfig) -> list[str]:
    state = _load_state(route.state_path)
    records = [
        record
        for record in state.get("deliveries", {}).values()
        if isinstance(record, dict)
        and record.get("status") in {"pending", "live_notified"}
    ]
    records.sort(key=lambda record: str(record.get("created_at") or ""))
    return [str(record["mission_id"]) for record in records]


def recoverable_claim_mission_ids(route: NervesRouteConfig) -> list[str]:
    """Return locally claimed missions whose immutable response can be adopted.

    The same deterministic worker id re-enters the existing claim.  This does
    not requeue work or regenerate the primary response:
    ``run_ollama_mission`` adopts the private response artifact, performs the
    independent model replay, then re-runs parent validation/receipt commit.
    """
    state = _load_state(route.state_path)
    recoverable: list[tuple[str, str]] = []
    for mission_id, record in state.get("deliveries", {}).items():
        if not isinstance(record, dict) or record.get("status") != "claimed":
            continue
        claim = record.get("claim")
        if (
            not isinstance(claim, dict)
            or not str(claim.get("worker_id") or "").startswith(
                "local-ollama:"
            )
            or record.get("route_sha256") != route.route_sha256
        ):
            continue
        response_path = (
            route.runtime_artifact_dir
            / str(mission_id)
            / "ollama-response.json"
        )
        if response_path.is_file() and not response_path.is_symlink():
            recoverable.append(
                (str(claim.get("claimed_at") or ""), str(mission_id))
            )
    recoverable.sort()
    return [mission_id for _, mission_id in recoverable]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", choices=sorted(ROUTES), default="ADA")
    args = parser.parse_args(argv)
    route = ROUTES[args.agent]
    recoverable = recoverable_claim_mission_ids(route)
    if recoverable:
        mission_id = recoverable[0]
        try:
            result = run_ollama_mission(mission_id, route=route)
        except (AgentMissionError, OllamaRuntimeError, ValueError) as exc:
            joined = joined_claim_conflict(
                mission_id, error=exc, route=route
            )
            if joined is not None:
                print(json.dumps(joined, sort_keys=True))
                return 0
            record = load_delivery(mission_id, route=route)
            if (
                str(exc) == "handoff_not_claimable"
                and record.get("status") == "claimed"
            ):
                print(
                    json.dumps(
                        {
                            "ok": False,
                            "mission_id": mission_id,
                            "status": "claim_in_flight",
                            "error": str(exc),
                        },
                        sort_keys=True,
                    )
                )
                return 1
            if record.get("status") == "claimed":
                result = fail_ollama_claim_validation(
                    mission_id, reason=str(exc), route=route
                )
                print(
                    json.dumps(
                        {
                            "ok": True,
                            "mission_id": result.mission_id,
                            "status": result.status,
                            "receipt_sha256": result.receipt_sha256,
                            "recovered_existing_claim": False,
                            "closed_failed_after_parent_rejection": True,
                            "error": str(exc),
                        },
                        sort_keys=True,
                    )
                )
                return 0
            stale_after_failure = stale_claim_mission_ids(route=route)
            if mission_id in stale_after_failure:
                result = fail_stale_ollama_claim(
                    mission_id, route=route
                )
                print(
                    json.dumps(
                        {
                            "ok": True,
                            "mission_id": result.mission_id,
                            "status": result.status,
                            "receipt_sha256": result.receipt_sha256,
                            "recovered_existing_claim": False,
                            "closed_failed_after_recovery_error": True,
                            "error": str(exc),
                        },
                        sort_keys=True,
                    )
                )
                return 0
            print(
                json.dumps(
                    {
                        "ok": False,
                        "mission_id": mission_id,
                        "error": str(exc),
                        "recovered_existing_claim": True,
                    },
                    sort_keys=True,
                )
            )
            return 1
        print(
            json.dumps(
                {
                    "ok": result.status == "completed",
                    "mission_id": result.mission_id,
                    "status": result.status,
                    "receipt_sha256": result.receipt_sha256,
                    "run_id": result.run_id,
                    "tool_events": [],
                    "recovered_existing_claim": True,
                },
                sort_keys=True,
            )
        )
        return 0
    stale = stale_claim_mission_ids(route=route)
    # Se sigue llamando a `stale_claim_mission_ids` para la DECISIÓN de reaping: es la
    # superficie que los tests monkeypatchean y la que ADA pidió no mover. El barrido
    # resiliente se consulta aparte, sólo para REPORTAR lo que no se pudo evaluar.
    _, unmeasurable = stale_claim_scan(route=route)
    if stale:
        result = fail_stale_ollama_claim(stale[0], route=route)
        print(
            json.dumps(
                {
                    "ok": True,
                    "mission_id": result.mission_id,
                    "status": result.status,
                    "receipt_sha256": result.receipt_sha256,
                    "reaped_stale_claim": True,
                    "unmeasurable_claims": [list(u) for u in unmeasurable],
                },
                sort_keys=True,
            )
        )
        return 0
    pending = pending_mission_ids(route)
    if not pending:
        # `idle` afirma que no hay nada que atender. Con registros no medibles eso es
        # falso: hay algo que NO SE PUDO MIRAR, que es distinto de que no haya nada.
        # Antes esa diferencia no llegaba a ninguna salida; ahora viaja en el JSON.
        idle: dict[str, object] = {"ok": True, "status": "idle", "processed": 0}
        if unmeasurable:
            idle["status"] = "idle_with_unmeasurable"
            idle["unmeasurable_claims"] = [list(u) for u in unmeasurable]
        print(json.dumps(idle, sort_keys=True))
        return 0
    mission_id = pending[0]
    try:
        result = run_ollama_mission(mission_id, route=route)
    except (AgentMissionError, OllamaRuntimeError, ValueError) as exc:
        joined = joined_claim_conflict(
            mission_id, error=exc, route=route
        )
        if joined is not None:
            print(json.dumps(joined, sort_keys=True))
            return 0
        record = load_delivery(mission_id, route=route)
        if (
            str(exc) == "handoff_not_claimable"
            and record.get("status") == "claimed"
        ):
            print(
                json.dumps(
                    {
                        "ok": False,
                        "mission_id": mission_id,
                        "status": "claim_in_flight",
                        "error": str(exc),
                    },
                    sort_keys=True,
                )
            )
            return 1
        if record.get("status") == "claimed":
            result = fail_ollama_claim_validation(
                mission_id, reason=str(exc), route=route
            )
            print(
                json.dumps(
                    {
                        "ok": True,
                        "mission_id": result.mission_id,
                        "status": result.status,
                        "receipt_sha256": result.receipt_sha256,
                        "closed_failed_after_parent_rejection": True,
                        "error": str(exc),
                    },
                    sort_keys=True,
                )
            )
            return 0
        print(
            json.dumps(
                {
                    "ok": False,
                    "mission_id": mission_id,
                    "error": str(exc),
                },
                sort_keys=True,
            )
        )
        return 1
    print(
        json.dumps(
            {
                "ok": result.status == "completed",
                "mission_id": result.mission_id,
                "status": result.status,
                "receipt_sha256": result.receipt_sha256,
                "run_id": result.run_id,
                "tool_events": [],
            },
            sort_keys=True,
        )
    )
    # A terminal failed/abstained receipt is still successfully processed.
    return 0


if __name__ == "__main__":
    sys.exit(main())
