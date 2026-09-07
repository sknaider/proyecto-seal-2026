from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).parents[1]
MODULE_PATH = ROOT / "tools" / "audit_soul_skills.py"
SPEC = importlib.util.spec_from_file_location("audit_soul_skills_corpus", MODULE_PATH)
audit_module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = audit_module
SPEC.loader.exec_module(audit_module)


def test_active_soul_skill_surface_has_no_structural_errors() -> None:
    roots = [ROOT / item for item in audit_module.DEFAULT_ROOTS]
    report = audit_module.audit(roots)
    assert report["counts"]["skills"] >= 200
    assert report["counts"]["errors"] == 0, [
        item for item in report["findings"] if item["severity"] == "ERROR"
    ]
    assert report["counts"]["structural_fail"] == 0


def test_true_green_skill_is_canonical_and_has_resources() -> None:
    skill = ROOT / "tools/skills/software-development/verify-true-green-tests"
    assert (skill / "SKILL.md").is_file()
    assert (skill / "agents/openai.yaml").is_file()
    assert (skill / "references/evidence-contract.md").is_file()
    assert (skill / "scripts/validate_evidence_manifest.py").is_file()


def test_active_skill_corpus_has_no_retired_port_or_privileged_dsn() -> None:
    report = audit_module.audit([ROOT / item for item in audit_module.DEFAULT_ROOTS])
    forbidden = {"embedded-privileged-dsn", "legacy-port-8766"}
    found = [item for item in report["findings"] if item["code"] in forbidden]
    assert found == []
