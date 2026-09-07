from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kernel_forge import assess_kernel_forge


def test_kernel_forge_keeps_correct_candidate_and_reverts_broken_candidate() -> None:
    assessment = assess_kernel_forge(Path(__file__).resolve().parents[1])

    assert all(assessment.checks.values())
    assert assessment.kept_candidate == "candidate_axpy_listcomp"
    assert "candidate_axpy_broken" in assessment.reverted_candidates
    broken = next(candidate for candidate in assessment.candidates if candidate.name == "candidate_axpy_broken")
    assert broken.correct is False
    assert broken.decision == "revert"


def test_kernel_forge_records_cuda_artifacts_and_toolchain_probe() -> None:
    assessment = assess_kernel_forge(Path(__file__).resolve().parents[1])

    assert assessment.cuda_artifacts["kernel_source_present"] is True
    assert assessment.cuda_artifacts["build_script_present"] is True
    assert set(assessment.toolchain) == {"nvcc_available", "nsight_compute_available"}
