from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from awareness_247_process import collect_live_payloads, run_process_once


def test_collect_live_payloads_has_five_family_inputs() -> None:
    payloads = collect_live_payloads("ADA")

    assert len(payloads) >= 8
    assert any(payload.get("kind") == "service_status" for payload in payloads)
    assert any(payload.get("kind") == "git_status" for payload in payloads)
    assert any(payload.get("kind") == "test_result" for payload in payloads)


def test_process_once_dry_run_covers_all_phases() -> None:
    result = asyncio.run(
        run_process_once(
            "ADA_247_DRY_TEST",
            persist=False,
            bootstrap_learning=False,
            enable_local=False,
        )
    )

    assert set(result.phase_scores) == {
        "awareness_24_7",
        "local_runtime",
        "learning_loop",
        "latent_graphmem_phase2",
        "production_clean",
    }
    assert result.phase_scores["awareness_24_7"] >= 90
