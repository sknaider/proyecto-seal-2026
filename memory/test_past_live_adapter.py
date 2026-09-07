from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import types
from dataclasses import replace
from pathlib import Path

import pytest

from memory.past_attribution_harness import HarnessConfig
from memory.past_live_adapter import (
    LIVE_DATASET_SCHEMA,
    FixedRuntimeContract,
    GraderResponse,
    LiveDatasetCase,
    ModelResponse,
    RetrievedMemory,
    RetrievalResult,
    load_live_dataset,
    make_recall_router_readonly_retriever,
    run_readonly_live_adapter,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


MODEL_HASH = _sha("model-binary-v1")
GRADER_HASH = _sha("grader-source-v1")
PROMPT_TEMPLATE = "QUERY:\n{query}\n\nPERSISTENCE:\n{persistence_context}\n\nANSWER:"


def _runtime(**overrides) -> FixedRuntimeContract:
    values = {
        "model_id": "fake-fixed-model",
        "model_sha256": MODEL_HASH,
        "prompt_template": PROMPT_TEMPLATE,
        "prompt_template_sha256": _sha(PROMPT_TEMPLATE),
        "grader_id": "fake-fixed-grader",
        "grader_sha256": GRADER_HASH,
    }
    values.update(overrides)
    return FixedRuntimeContract(**values)


def _dataset_case(**overrides) -> LiveDatasetCase:
    values = {
        "case_id": "public-preference-1",
        "capability": "memory",
        "agent": "BENCH_PUBLIC",
        "query": "¿Qué bebida prefiere Valeria?",
        "expected_memory_ids": ("101",),
        "placebo_memory_ids": ("202",),
        "replacement_by_id": {"101": "Valeria antes prefería café."},
        "grader_payload": {"expected": "limonada"},
        "data_classification": "synthetic_benchmark",
    }
    values.update(overrides)
    return LiveDatasetCase(**values)


def _retrieval(case: LiveDatasetCase, *, include_target: bool = True) -> RetrievalResult:
    target = RetrievedMemory("101", "memories", "Valeria prefiere limonada fría.")
    placebo = RetrievedMemory("202", "memories", "Mateo prefiere caminar en invierno.")
    return RetrievalResult(
        query_hits=(target,) if include_target else (),
        artifacts_by_id={"101": target, "202": placebo},
        query_sha256=_sha(case.query),
        route_id="fake-readonly-retriever-v1",
        read_only_attested=True,
        limits=("fake",),
    )


async def _fake_retriever(case: LiveDatasetCase) -> RetrievalResult:
    return _retrieval(case)


def _model(request):
    output = "Limonada fría." if "limonada" in request.prompt else "No lo sé."
    return ModelResponse(
        output=output,
        observed_prompt_sha256=request.prompt_sha256,
        observed_model_sha256=request.model_sha256,
        input_tokens=20,
        output_tokens=4,
    )


def _grader(request):
    expected = str(request.grader_payload["expected"]).lower()
    return GraderResponse(
        score=1.0 if expected in request.output.lower() else 0.0,
        observed_grader_sha256=request.grader_sha256,
    )


def _run(**overrides):
    values = {
        "dataset_cases": [_dataset_case()],
        "retriever": _fake_retriever,
        "runtime": _runtime(),
        "model": _model,
        "grader": _grader,
        "harness_config": HarnessConfig(subject="BENCH_SYNTHETIC", trials=2),
    }
    values.update(overrides)
    return asyncio.run(run_readonly_live_adapter(**values))


def test_live_adapter_runs_six_arms_with_fixed_hash_attestations() -> None:
    requests = []

    def recording_model(request):
        requests.append(request)
        return _model(request)

    report = _run(model=recording_model)

    assert report["summaries"][0]["verdict"] == "pathway_supported"
    assert len(report["trials"]) == 12
    assert {request.arm for request in requests} == {
        "persistence_on", "persistence_off", "placebo", "delete", "replace", "corrupt"
    }
    assert {request.model_sha256 for request in requests} == {MODEL_HASH}
    assert all(request.prompt_sha256 == _sha(request.prompt) for request in requests)
    adapter = report["live_adapter"]
    assert adapter["attestation"]["per_call_prompt_hash_checked"] is True
    assert adapter["attestation"]["callback_hash_echo_verified"] is True
    assert adapter["attestation"]["callback_attestation_is_independent"] is False
    assert adapter["attestation"]["retriever_read_only_flag_is_self_attested"] is True
    assert adapter["dataset_classification"]["independently_verified"] is False
    assert report["matched_contract"]["external_callback_attested"] is False
    assert report["matched_contract"]["callback_hash_echo_verified"] is True
    assert report["matched_contract"]["causal_proof_claimed"] is False


def test_live_callback_metadata_cannot_smuggle_raw_content_into_report() -> None:
    model_sentinel = "RAW_MODEL_METADATA_SENTINEL"
    grader_sentinel = "RAW_GRADER_METADATA_SENTINEL"

    def model_with_raw_metadata(request):
        response = _model(request)
        return replace(response, metadata={"raw_output": model_sentinel})

    def grader_with_raw_metadata(request):
        response = _grader(request)
        return replace(response, metadata={"raw_memory": grader_sentinel})

    report = _run(model=model_with_raw_metadata, grader=grader_with_raw_metadata)
    rendered = json.dumps(report, ensure_ascii=False)
    assert model_sentinel not in rendered
    assert grader_sentinel not in rendered
    metadata = report["trials"][0]["metadata_evidence"]
    assert set(metadata) == {"sha256", "utf8_bytes", "field_count"}


def test_missing_expected_target_fails_before_model_execution() -> None:
    calls = []

    async def missing(case):
        return _retrieval(case, include_target=False)

    def must_not_run(request):
        calls.append(request)
        return _model(request)

    with pytest.raises(ValueError, match="expected target was not recovered"):
        _run(retriever=missing, model=must_not_run)
    assert calls == []


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda result: replace(result, read_only_attested=False), "did not attest read-only"),
        (lambda result: replace(result, query_sha256="0" * 64), "query hash mismatch"),
    ],
)
def test_retrieval_boundary_attestation_fails_closed(mutation, message) -> None:
    async def mutated(case):
        return mutation(_retrieval(case))

    with pytest.raises(ValueError, match=message):
        _run(retriever=mutated)


@pytest.mark.parametrize(
    ("runtime", "message"),
    [
        (_runtime(model_sha256="x" * 64), "model_sha256"),
        (_runtime(prompt_template_sha256="0" * 64), "prompt_template_sha256 does not match"),
        (_runtime(grader_sha256="NOT-A-HASH"), "grader_sha256"),
    ],
)
def test_runtime_contract_hashes_fail_closed(runtime, message) -> None:
    with pytest.raises(ValueError, match=message):
        _run(runtime=runtime)


def test_model_prompt_hash_mismatch_fails_closed() -> None:
    def wrong_prompt(request):
        response = _model(request)
        return ModelResponse(
            output=response.output,
            observed_prompt_sha256="0" * 64,
            observed_model_sha256=response.observed_model_sha256,
        )

    with pytest.raises(ValueError, match="prompt hash mismatch"):
        _run(model=wrong_prompt)


def test_model_binary_hash_mismatch_fails_closed() -> None:
    def wrong_model(request):
        response = _model(request)
        return ModelResponse(
            output=response.output,
            observed_prompt_sha256=response.observed_prompt_sha256,
            observed_model_sha256="0" * 64,
        )

    with pytest.raises(ValueError, match="model hash mismatch"):
        _run(model=wrong_model)


def test_grader_hash_mismatch_fails_closed() -> None:
    def wrong_grader(request):
        response = _grader(request)
        return GraderResponse(score=response.score, observed_grader_sha256="0" * 64)

    with pytest.raises(ValueError, match="grader callback hash mismatch"):
        _run(grader=wrong_grader)


def test_private_dataset_is_rejected() -> None:
    with pytest.raises(ValueError, match="data_classification"):
        _dataset_case(data_classification="private")


def test_dataset_loader_requires_explicit_non_private_cases(tmp_path: Path) -> None:
    dataset = {
        "schema": LIVE_DATASET_SCHEMA,
        "cases": [
            {
                "case_id": "case-1",
                "capability": "memory",
                "agent": "BENCH_PUBLIC",
                "query": "consulta",
                "expected_memory_ids": ["101"],
                "placebo_memory_ids": ["202"],
                "replacement_by_id": {"101": "stale"},
                "grader_payload": {"expected": "ok"},
                "data_classification": "public_benchmark",
            }
        ],
    }
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(dataset), encoding="utf-8")

    loaded = load_live_dataset(path)

    assert loaded[0].expected_memory_ids == ("101",)
    assert loaded[0].data_classification == "public_benchmark"


def test_real_retriever_wrapper_uses_fetch_only_and_no_mutation(monkeypatch) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parent))
    embeddings_stub = types.ModuleType("embeddings")

    async def unused_embedding(_query):
        raise AssertionError("memory-only read path must not embed the query")

    embeddings_stub.get_embedding = unused_embedding
    monkeypatch.setitem(sys.modules, "embeddings", embeddings_stub)
    sys.modules.pop("recall_router", None)
    import recall_router

    class FakePool:
        def __init__(self):
            self.fetch_calls = []

        async def fetch(self, query, *args):
            self.fetch_calls.append((query, args))
            assert query.lstrip().startswith("SELECT")
            if "embedding_bm25 @@" in query:
                return [
                    {
                        "id": 101,
                        "agent": "BENCH_PUBLIC",
                        "category": "fact",
                        "content": "Valeria prefiere limonada fría.",
                        "importance": 8,
                        "created_at": None,
                        "scope": "private",
                        "kw_rank": 1.0,
                    }
                ]
            return [
                {
                    "id": 101,
                    "agent": "BENCH_PUBLIC",
                    "category": "fact",
                    "content": "Valeria prefiere limonada fría.",
                    "scope": "private",
                    "importance": 8,
                    "created_at": None,
                },
                {
                    "id": 202,
                    "agent": "BENCH_PUBLIC",
                    "category": "fact",
                    "content": "Mateo prefiere caminar en invierno.",
                    "scope": "private",
                    "importance": 5,
                    "created_at": None,
                },
            ]

        async def execute(self, *_args, **_kwargs):
            raise AssertionError("read-only retriever must never execute mutations")

    pool = FakePool()
    retriever = make_recall_router_readonly_retriever(pool)
    result = asyncio.run(retriever(_dataset_case()))

    assert [item.memory_id for item in result.query_hits] == ["101"]
    assert sorted(result.artifacts_by_id) == ["101", "202"]
    assert result.read_only_attested is True
    assert len(pool.fetch_calls) == 2
