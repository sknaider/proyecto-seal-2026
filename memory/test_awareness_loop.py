from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from awareness_loop import build_parser, evaluate_awareness_loop_contract, run_fixture_loop, run_payload_loop


def test_fixture_loop_dry_run_no_ticks() -> None:
    run = asyncio.run(run_fixture_loop(agent="ADA", count=5, dry_run=True, enable_reflex=True))

    assert run.processed == 5
    assert run.persisted is False
    assert all(item.tick is None for item in run.items)
    assert run.reflex_executions >= 1
    assert run.boundary == "manual_shadow_loop_no_daemon_no_publish"


def test_payload_loop_public_without_ada_is_silent() -> None:
    run = asyncio.run(
        run_payload_loop(
            [{"id": 1, "from": "William", "to": "equipo", "channel": "web_chat", "content": "chicos revisen"}],
            agent="ADA",
            dry_run=True,
        )
    )

    assert run.processed == 1
    assert run.action_counts == {"ignore": 1}
    assert run.items[0].decision["actions"] == ["silent"]


def test_payload_loop_dm_can_use_local_proposal_without_persisting() -> None:
    run = asyncio.run(
        run_payload_loop(
            [{"id": 2, "from": "William", "to": "ADA", "channel": "dm:ada:william", "content": "como estas"}],
            agent="ADA",
            dry_run=True,
            enable_local=False,
        )
    )

    assert run.processed == 1
    assert run.action_counts == {"local_reflect": 1}
    assert run.local_proposals == 0


def test_awareness_loop_contract_passes_and_cleans_up() -> None:
    result = evaluate_awareness_loop_contract()

    assert result["passed"] == result["total"]
    assert result["cleanup_deleted"] >= 11


def test_cli_parser_accepts_fixture_payload_contract() -> None:
    parser = build_parser()

    fixture = parser.parse_args(["fixture", "--count", "3"])
    payload = parser.parse_args(["--persist", "payload", "--json", "{}"])
    contract = parser.parse_args(["contract"])

    assert fixture.command == "fixture"
    assert fixture.count == 3
    assert payload.persist is True
    assert payload.command == "payload"
    assert contract.command == "contract"

