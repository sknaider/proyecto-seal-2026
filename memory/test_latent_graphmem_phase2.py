from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from latent_graphmem_phase2 import assess_latent_graphmem_phase2, query_hash, router_probe


def test_query_hash_is_stable_and_normalized() -> None:
    assert query_hash("  Por Que SOUL  ") == query_hash("por que soul")


def test_router_probe_routes_expected_queries() -> None:
    probe = router_probe()

    assert probe["all_ok"] is True
    assert probe["factual_backend"] == "magma"


def test_phase2_assessment_passes_without_persisting() -> None:
    result = asyncio.run(assess_latent_graphmem_phase2("ADA_PHASE2_TEST", persist=False))

    assert result.passed is True
    assert result.score >= 90
    assert result.checks["router_routes_latent_queries"] is True
