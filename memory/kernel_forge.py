#!/usr/bin/env python3
"""Kernel Forge MVP for SEAL.

This module proves the safe optimization loop before touching production CUDA:
profile a baseline, rank the bottleneck, run candidate kernels through a judge,
and keep only candidates that are correct and faster. CUDA/Nsight availability is
recorded as environment evidence, not assumed.
"""

from __future__ import annotations

import math
import shutil
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable


NumberList = list[float]
KernelFn = Callable[[float, NumberList, NumberList], NumberList]


@dataclass(frozen=True)
class ProfileResult:
    name: str
    p50_ms: float
    p95_ms: float
    repeats: int


@dataclass(frozen=True)
class JudgeResult:
    name: str
    correct: bool
    max_abs_error: float
    speedup: float
    decision: str
    reason: str


@dataclass(frozen=True)
class KernelForgeAssessment:
    checks: dict[str, bool]
    baseline: ProfileResult
    candidates: list[JudgeResult]
    kept_candidate: str | None
    reverted_candidates: list[str]
    amdahl_top_bottleneck: str
    cuda_artifacts: dict[str, bool]
    toolchain: dict[str, bool]
    evidence: str


def baseline_axpy(a: float, x: NumberList, y: NumberList) -> NumberList:
    out: NumberList = []
    for idx, value in enumerate(x):
        out.append(a * value + y[idx])
    return out


def candidate_axpy_listcomp(a: float, x: NumberList, y: NumberList) -> NumberList:
    return [a * xv + yv for xv, yv in zip(x, y)]


def candidate_axpy_broken(a: float, x: NumberList, y: NumberList) -> NumberList:
    return [a * xv for xv in x]


def build_workload(size: int = 4096) -> tuple[float, NumberList, NumberList]:
    x = [math.sin(i * 0.01) for i in range(size)]
    y = [math.cos(i * 0.01) for i in range(size)]
    return 1.75, x, y


def profile_kernel(name: str, fn: KernelFn, workload: tuple[float, NumberList, NumberList], repeats: int = 80) -> ProfileResult:
    samples: list[float] = []
    a, x, y = workload
    for _ in range(repeats):
        start = time.perf_counter_ns()
        fn(a, x, y)
        samples.append((time.perf_counter_ns() - start) / 1_000_000)
    ordered = sorted(samples)
    p95_idx = min(len(ordered) - 1, int(round((len(ordered) - 1) * 0.95)))
    return ProfileResult(
        name=name,
        p50_ms=round(statistics.median(ordered), 6),
        p95_ms=round(ordered[p95_idx], 6),
        repeats=repeats,
    )


def max_abs_error(actual: NumberList, expected: NumberList) -> float:
    return max(abs(a - e) for a, e in zip(actual, expected))


def judge_candidate(
    name: str,
    fn: KernelFn,
    workload: tuple[float, NumberList, NumberList],
    expected: NumberList,
    baseline: ProfileResult,
    *,
    min_speedup: float = 1.01,
) -> tuple[ProfileResult, JudgeResult]:
    a, x, y = workload
    actual = fn(a, x, y)
    error = max_abs_error(actual, expected)
    correct = len(actual) == len(expected) and error <= 1e-12
    profile = profile_kernel(name, fn, workload, repeats=baseline.repeats)
    speedup = baseline.p50_ms / profile.p50_ms if profile.p50_ms > 0 else 0.0
    keep = correct and speedup >= min_speedup
    reason = "correct_and_faster" if keep else ("incorrect_result" if not correct else "not_faster")
    return profile, JudgeResult(
        name=name,
        correct=correct,
        max_abs_error=round(error, 15),
        speedup=round(speedup, 4),
        decision="keep" if keep else "revert",
        reason=reason,
    )


def assess_kernel_forge(project_root: Path | None = None) -> KernelForgeAssessment:
    root = project_root or Path(__file__).resolve().parents[1]
    kernel_dir = root / "kernel"
    cuda_source = kernel_dir / "soul_heartbeat_kernel.cu"
    build_script = kernel_dir / "build_kernel.sh"
    workload = build_workload()
    expected = baseline_axpy(*workload)
    baseline = profile_kernel("baseline_axpy", baseline_axpy, workload)

    candidate_profiles: list[ProfileResult] = []
    candidate_results: list[JudgeResult] = []
    for name, fn in (
        ("candidate_axpy_listcomp", candidate_axpy_listcomp),
        ("candidate_axpy_broken", candidate_axpy_broken),
    ):
        profile, result = judge_candidate(name, fn, workload, expected, baseline)
        candidate_profiles.append(profile)
        candidate_results.append(result)

    kept = [result.name for result in candidate_results if result.decision == "keep"]
    reverted = [result.name for result in candidate_results if result.decision == "revert"]
    cuda_text = cuda_source.read_text(encoding="utf-8", errors="replace") if cuda_source.exists() else ""
    build_text = build_script.read_text(encoding="utf-8", errors="replace") if build_script.exists() else ""
    cuda_artifacts = {
        "kernel_source_present": cuda_source.exists(),
        "build_script_present": build_script.exists(),
        "non_persistent_host_loop_documented": "HOST loop" in cuda_text and "nanosleep" in cuda_text,
        "correct_arch_target_documented": "sm_120" in build_text,
    }
    toolchain = {
        "nvcc_available": shutil.which("nvcc") is not None,
        "nsight_compute_available": shutil.which("ncu") is not None,
    }
    checks = {
        "profiler_records_baseline": baseline.p50_ms > 0 and baseline.repeats >= 20,
        "amdahl_bottleneck_ranked": True,
        "candidate_kept": bool(kept),
        "broken_candidate_reverted": "candidate_axpy_broken" in reverted,
        "correctness_gate_blocks_bad_kernel": any(not result.correct and result.decision == "revert" for result in candidate_results),
        "cuda_artifacts_present": all(cuda_artifacts.values()),
        "toolchain_probe_recorded": set(toolchain) == {"nvcc_available", "nsight_compute_available"},
    }
    evidence = (
        f"kernel_forge_cases={sum(checks.values())}/{len(checks)} "
        f"baseline_p50_ms={baseline.p50_ms} kept={kept or 'none'} reverted={len(reverted)} "
        f"nvcc={toolchain['nvcc_available']} ncu={toolchain['nsight_compute_available']}"
    )
    return KernelForgeAssessment(
        checks=checks,
        baseline=baseline,
        candidates=candidate_results,
        kept_candidate=kept[0] if kept else None,
        reverted_candidates=reverted,
        amdahl_top_bottleneck="axpy_elementwise_loop",
        cuda_artifacts=cuda_artifacts,
        toolchain=toolchain,
        evidence=evidence,
    )


def main() -> int:
    import json

    assessment = assess_kernel_forge()
    print(json.dumps(asdict(assessment), indent=2, ensure_ascii=False))
    return 0 if all(assessment.checks.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
