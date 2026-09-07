from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "corpus_rsi"


def _load(name: str):
    path = CORPUS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"rsi_{name}_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_shield_trace_reproduces_frozen_bytes_and_non_vacuous_controls(tmp_path: Path) -> None:
    module = _load("shield_deterministic_trace")
    reproduced = tmp_path / "shield.json"

    artifact = module.run(str(reproduced))
    frozen = CORPUS / "shield_deterministic_trace.json"

    assert _sha256(reproduced) == _sha256(frozen)
    assert artifact["detection_summary"] == {"total": 39, "flagged": 35, "evaded": 4}
    manifest = artifact["corpus_manifest"]
    assert len(manifest["specimens"]) == 39
    assert len(manifest["root_sha256"]) == 64
    assert len({item["specimen"] for item in manifest["specimens"]}) == 39
    assert all(len(item["sha256"]) == 64 for item in manifest["specimens"])
    controls = artifact["controls"]["non_vacuous_check"]
    assert controls["assert_positive_is_high"] is True
    assert controls["assert_negative_is_low"] is True
    assert controls["flags_differ"] is True
    assert controls["positive_fidelity"] is True
    assert controls["negative_fidelity"] is True
    assert len(artifact["evasions"]) == 4
    for evasion in artifact["evasions"]:
        trace = evasion["worst_chunk_trace"]
        assert trace["payload"].startswith("REDACTED")
        assert "raw" not in trace
        assert "defang_final" not in trace


def test_t5_probe_reproduces_frozen_bytes_and_separates_egress(tmp_path: Path) -> None:
    module = _load("t5_extraction_probe")
    reproduced = tmp_path / "t5.json"

    artifact = module.run(str(reproduced))
    frozen = CORPUS / "t5_extraction_probe.json"

    assert _sha256(reproduced) == _sha256(frozen)
    assert artifact["part1_shield_vs_t5"]["shield_misses_all_t5"] is True
    assert all(
        row["risk"] == "low"
        for row in artifact["part1_shield_vs_t5"]["per_vector"].values()
    )
    egress = artifact["part2_egress_control"]
    assert egress["positive_blocks_cross_owner_leak"] is True
    assert egress["negative_allows_own_or_shared"] is True
    assert egress["non_vacuous_decisions_differ"] is True
    assert {row["decision"] for row in egress["per_memory"]} == {"allow", "block"}
    assert artifact["verdict"]["reference_egress_control_separates_leak"] is True


def test_frozen_json_contains_no_runtime_error_marker() -> None:
    for name in ("shield_deterministic_trace", "t5_extraction_probe"):
        payload = json.loads((CORPUS / f"{name}.json").read_text(encoding="utf-8"))
        rendered = json.dumps(payload, ensure_ascii=False).lower()
        assert "traceback" not in rendered
        assert "filenotfounderror" not in rendered
