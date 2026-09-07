from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from local_runtime_service import (
    ALLOWED_ACTIONS,
    LocalRuntimeConfig,
    build_parser,
    classify,
    evaluate_local_runtime_contract,
    parse_runtime_json,
    reflect,
    runtime_health,
)


def test_health_accepts_openai_compatible_models_payload() -> None:
    config = LocalRuntimeConfig(endpoint="http://local", model="gemma4-dum")

    def transport(url: str, payload: dict | None, timeout_seconds: float) -> dict:
        return {"data": [{"id": "gemma4-dum"}]}

    health = runtime_health(config, transport)

    assert health["ok"] is True
    assert health["model"] == "gemma4-dum"
    assert health["provider"] == "llama_cpp"


def test_parse_runtime_json_strips_markdown_fence_and_clamps_action() -> None:
    parsed = parse_runtime_json('```json\n{"action":"reflex_action","confidence":2}\n```', "classify")
    bad = parse_runtime_json('{"action":"delete_everything","confidence":0.9}', "classify")

    assert parsed["action"] == "reflex_action"
    assert parsed["confidence"] == 1.0
    assert bad["action"] == "ask_william"
    assert bad["recommended_action"] == "ask_william"


def test_classify_and_reflect_use_fake_transport_schema() -> None:
    config = LocalRuntimeConfig(endpoint="http://local", model="gemma4-dum")

    def transport(url: str, payload: dict | None, timeout_seconds: float) -> dict:
        assert payload is not None
        return {
            "model": "gemma4-dum",
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "action": "reflex_action",
                                "confidence": 0.9,
                                "summary": "service down",
                                "risk": "high",
                                "recommended_action": "capture_evidence",
                            }
                        )
                    }
                }
            ],
        }

    classified = classify("service failed", config, transport)
    reflected = reflect("William asked to continue", config, transport)

    assert classified.ok is True
    assert classified.output["action"] in ALLOWED_ACTIONS
    assert reflected.ok is True
    assert reflected.output["recommended_action"] == "capture_evidence"
    assert classified.boundary == "proposal_only_no_side_effects"


def test_fallback_blocks_destructive_when_runtime_down() -> None:
    config = LocalRuntimeConfig(endpoint="http://local", model="gemma4-dum")

    def failing_transport(url: str, payload: dict | None, timeout_seconds: float) -> dict:
        raise RuntimeError("down")

    result = classify("DROP TABLE soul_v3.memories", config, failing_transport)

    assert result.ok is False
    assert result.degraded is True
    assert result.output["recommended_action"] == "block_execution"
    assert result.output["risk"] == "critical"


def test_contract_with_real_health_shape_and_fake_generation() -> None:
    config = LocalRuntimeConfig(endpoint="http://local", model="gemma4-dum")

    # The function's generation path is fake; live health is exercised in the spine suite.
    result = evaluate_local_runtime_contract(config)

    assert result["total"] >= 7
    assert "checks" in result


def test_cli_parser_accepts_health_tasks_and_contract() -> None:
    parser = build_parser()

    health = parser.parse_args(["health"])
    classify_args = parser.parse_args(["classify", "--context", "x"])
    summarize_args = parser.parse_args(["summarize", "--context", "x"])
    reflect_args = parser.parse_args(["reflect", "--context", "x"])
    contract = parser.parse_args(["contract"])

    assert health.command == "health"
    assert classify_args.command == "classify"
    assert summarize_args.command == "summarize"
    assert reflect_args.command == "reflect"
    assert contract.command == "contract"
