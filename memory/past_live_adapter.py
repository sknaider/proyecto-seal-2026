#!/usr/bin/env python3
"""Read-only live-path adapter for the PAST-style attribution harness.

The adapter separates three boundaries:

1. an explicit, non-private benchmark dataset names expected memory IDs;
2. a read-only retriever must return those IDs through the real query path;
3. a fixed prompt/model/grader contract runs all attribution arms.

The bundled Recall Router retriever does not write, update or delete memory: it
calls the per-source SELECT/ranking path and avoids ``soul_recall_router()``,
whose public wrapper writes a recall-audit row.  Injected retriever, model and
grader callbacks are arbitrary Python and therefore remain a trust boundary;
their self-reported hashes/flags are checked but are not sandboxing or
independent cryptographic execution proof.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence

try:
    from past_attribution_harness import (
        AttributionCase,
        ExecutionObservation,
        HarnessConfig,
        RetainedArtifact,
        run_attribution_harness,
    )
except ModuleNotFoundError:  # Support package imports from the repository root.
    from memory.past_attribution_harness import (
        AttributionCase,
        ExecutionObservation,
        HarnessConfig,
        RetainedArtifact,
        run_attribution_harness,
    )


LIVE_DATASET_SCHEMA = "seal.past-attribution.live-dataset.v1"
LIVE_ADAPTER_SCHEMA = "seal.past-attribution.live-adapter.v1"
ALLOWED_DATA_CLASSIFICATIONS = frozenset({"public_benchmark", "synthetic_benchmark"})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_text(_canonical_json(value))


def _require_sha256(name: str, value: str) -> None:
    if not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")


@dataclass(frozen=True)
class LiveDatasetCase:
    case_id: str
    capability: str
    agent: str
    query: str
    expected_memory_ids: tuple[str, ...]
    placebo_memory_ids: tuple[str, ...]
    replacement_by_id: Mapping[str, str]
    grader_payload: Mapping[str, Any]
    data_classification: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.case_id.strip():
            raise ValueError("case_id must not be empty")
        if not self.agent.strip():
            raise ValueError("agent must not be empty")
        if self.agent.strip().upper() == "FABLE":
            raise ValueError("FABLE is reserved as an independent control/reviewer")
        if not self.query.strip():
            raise ValueError("query must not be empty")
        if not self.expected_memory_ids:
            raise ValueError("expected_memory_ids must not be empty")
        if not self.placebo_memory_ids:
            raise ValueError("placebo_memory_ids must not be empty")
        expected = set(self.expected_memory_ids)
        placebo = set(self.placebo_memory_ids)
        if len(expected) != len(self.expected_memory_ids):
            raise ValueError("expected_memory_ids must be unique")
        if len(placebo) != len(self.placebo_memory_ids):
            raise ValueError("placebo_memory_ids must be unique")
        if expected & placebo:
            raise ValueError("expected and placebo memory IDs must be disjoint")
        if self.data_classification not in ALLOWED_DATA_CLASSIFICATIONS:
            raise ValueError(
                "data_classification must be public_benchmark or synthetic_benchmark"
            )
        unknown_replacements = set(self.replacement_by_id) - expected
        if unknown_replacements:
            raise ValueError(
                f"replacement_by_id contains non-target IDs: {sorted(unknown_replacements)}"
            )


def load_live_dataset(path: str | Path) -> tuple[LiveDatasetCase, ...]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema") != LIVE_DATASET_SCHEMA:
        raise ValueError("invalid live attribution dataset schema")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("live attribution dataset requires a non-empty cases list")
    cases = []
    for raw in raw_cases:
        cases.append(
            LiveDatasetCase(
                case_id=str(raw["case_id"]),
                capability=str(raw["capability"]),
                agent=str(raw["agent"]),
                query=str(raw["query"]),
                expected_memory_ids=tuple(str(item) for item in raw["expected_memory_ids"]),
                placebo_memory_ids=tuple(str(item) for item in raw["placebo_memory_ids"]),
                replacement_by_id={
                    str(key): str(value) for key, value in raw.get("replacement_by_id", {}).items()
                },
                grader_payload=dict(raw.get("grader_payload", {})),
                data_classification=str(raw["data_classification"]),
                metadata=dict(raw.get("metadata", {})),
            )
        )
    case_ids = [case.case_id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("live attribution case IDs must be unique")
    return tuple(cases)


@dataclass(frozen=True)
class RetrievedMemory:
    memory_id: str
    source: str
    content: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.memory_id.strip() or not self.source.strip():
            raise ValueError("retrieved memory ID and source must not be empty")


@dataclass(frozen=True)
class RetrievalResult:
    query_hits: tuple[RetrievedMemory, ...]
    artifacts_by_id: Mapping[str, RetrievedMemory]
    query_sha256: str
    route_id: str
    read_only_attested: bool
    limits: tuple[str, ...] = ()


RetrieverFn = Callable[[LiveDatasetCase], Awaitable[RetrievalResult]]


@dataclass(frozen=True)
class FixedRuntimeContract:
    model_id: str
    model_sha256: str
    prompt_template: str
    prompt_template_sha256: str
    grader_id: str
    grader_sha256: str

    def validate(self) -> None:
        if not self.model_id.strip() or not self.grader_id.strip():
            raise ValueError("model_id and grader_id must not be empty")
        _require_sha256("model_sha256", self.model_sha256)
        _require_sha256("prompt_template_sha256", self.prompt_template_sha256)
        _require_sha256("grader_sha256", self.grader_sha256)
        if _sha256_text(self.prompt_template) != self.prompt_template_sha256:
            raise ValueError("prompt_template_sha256 does not match prompt_template")
        if self.prompt_template.count("{query}") != 1:
            raise ValueError("prompt_template must contain {query} exactly once")
        if self.prompt_template.count("{persistence_context}") != 1:
            raise ValueError("prompt_template must contain {persistence_context} exactly once")


@dataclass(frozen=True)
class ModelRequest:
    case_id: str
    arm: str
    trial_seed: int
    prompt: str
    prompt_sha256: str
    model_id: str
    model_sha256: str


@dataclass(frozen=True)
class ModelResponse:
    output: str
    observed_prompt_sha256: str
    observed_model_sha256: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


ModelFn = Callable[[ModelRequest], ModelResponse]


@dataclass(frozen=True)
class GraderRequest:
    case_id: str
    query: str
    output: str
    grader_payload: Mapping[str, Any]
    grader_id: str
    grader_sha256: str


@dataclass(frozen=True)
class GraderResponse:
    score: float
    observed_grader_sha256: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 <= self.score <= 1.0:
            raise ValueError("grader score must be in [0, 1]")


GraderFn = Callable[[GraderRequest], GraderResponse]


def _render_prompt(template: str, query: str, artifacts: Sequence[RetainedArtifact]) -> str:
    context = "\n".join(
        f"- [{artifact.source} #{artifact.artifact_id}] {artifact.content}"
        for artifact in artifacts
    )
    if not context:
        context = "[NO_PERSISTENCE_CONTEXT]"
    return template.replace("{query}", query).replace("{persistence_context}", context)


def _to_attribution_case(
    dataset_case: LiveDatasetCase,
    retrieval: RetrievalResult,
) -> AttributionCase:
    _require_sha256("retrieval.query_sha256", retrieval.query_sha256)
    if retrieval.query_sha256 != _sha256_text(dataset_case.query):
        raise ValueError(f"retriever query hash mismatch for case {dataset_case.case_id}")
    if not retrieval.read_only_attested:
        raise ValueError(f"retriever did not attest read-only mode for case {dataset_case.case_id}")
    if not retrieval.route_id.strip():
        raise ValueError(f"retriever route_id is missing for case {dataset_case.case_id}")

    query_hit_ids = {item.memory_id for item in retrieval.query_hits}
    expected = set(dataset_case.expected_memory_ids)
    missing_targets = expected - query_hit_ids
    if missing_targets:
        raise ValueError(
            f"expected target was not recovered for case {dataset_case.case_id}: "
            f"{sorted(missing_targets)}"
        )
    required = expected | set(dataset_case.placebo_memory_ids)
    missing_artifacts = required - set(retrieval.artifacts_by_id)
    if missing_artifacts:
        raise ValueError(
            f"retriever did not resolve required artifacts for case {dataset_case.case_id}: "
            f"{sorted(missing_artifacts)}"
        )

    retained = tuple(
        RetainedArtifact(
            artifact_id=memory_id,
            source=retrieval.artifacts_by_id[memory_id].source,
            content=retrieval.artifacts_by_id[memory_id].content,
            replacement=dataset_case.replacement_by_id.get(memory_id),
            metadata=dict(retrieval.artifacts_by_id[memory_id].metadata),
        )
        for memory_id in dataset_case.expected_memory_ids
    )
    placebo = tuple(
        RetainedArtifact(
            artifact_id=memory_id,
            source=retrieval.artifacts_by_id[memory_id].source,
            content=retrieval.artifacts_by_id[memory_id].content,
            metadata=dict(retrieval.artifacts_by_id[memory_id].metadata),
        )
        for memory_id in dataset_case.placebo_memory_ids
    )
    return AttributionCase(
        case_id=dataset_case.case_id,
        capability=dataset_case.capability,
        query=dataset_case.query,
        retained_artifacts=retained,
        expected_artifact_ids=dataset_case.expected_memory_ids,
        placebo_artifacts=placebo,
        grader_version="live-fixed-hash-v1",
        metadata={
            **dict(dataset_case.metadata),
            "data_classification": dataset_case.data_classification,
            "retriever_route_id": retrieval.route_id,
        },
    )


def _reseal_report(report: dict[str, Any]) -> None:
    report.pop("evidence_sha256", None)
    report["evidence_sha256"] = _sha256_json(report)


async def run_readonly_live_adapter(
    dataset_cases: Sequence[LiveDatasetCase],
    *,
    retriever: RetrieverFn,
    runtime: FixedRuntimeContract,
    model: ModelFn,
    grader: GraderFn,
    harness_config: HarnessConfig | None = None,
) -> dict[str, Any]:
    """Retrieve expected public/synthetic artifacts and run six matched arms."""

    if not dataset_cases:
        raise ValueError("at least one live dataset case is required")
    runtime.validate()
    active_config = harness_config or HarnessConfig(subject="BENCH_SYNTHETIC")
    if tuple(active_config.arms) != (
        "persistence_on",
        "persistence_off",
        "placebo",
        "delete",
        "replace",
        "corrupt",
    ):
        raise ValueError("live adapter requires all six standard arms")

    retrievals: dict[str, RetrievalResult] = {}
    attribution_cases: list[AttributionCase] = []
    by_id: dict[str, LiveDatasetCase] = {}
    for dataset_case in dataset_cases:
        if dataset_case.case_id in by_id:
            raise ValueError("live dataset case IDs must be unique")
        by_id[dataset_case.case_id] = dataset_case
        retrieval = await retriever(dataset_case)
        retrievals[dataset_case.case_id] = retrieval
        attribution_cases.append(_to_attribution_case(dataset_case, retrieval))

    def execute(request) -> ExecutionObservation:
        prompt = _render_prompt(
            runtime.prompt_template,
            request.case.query,
            request.available_artifacts,
        )
        prompt_sha256 = _sha256_text(prompt)
        model_response = model(
            ModelRequest(
                case_id=request.case.case_id,
                arm=request.arm,
                trial_seed=request.trial_seed,
                prompt=prompt,
                prompt_sha256=prompt_sha256,
                model_id=runtime.model_id,
                model_sha256=runtime.model_sha256,
            )
        )
        _require_sha256("observed_prompt_sha256", model_response.observed_prompt_sha256)
        _require_sha256("observed_model_sha256", model_response.observed_model_sha256)
        if model_response.observed_prompt_sha256 != prompt_sha256:
            raise ValueError("model callback prompt hash mismatch")
        if model_response.observed_model_sha256 != runtime.model_sha256:
            raise ValueError("model callback model hash mismatch")

        dataset_case = by_id[request.case.case_id]
        grader_response = grader(
            GraderRequest(
                case_id=request.case.case_id,
                query=request.case.query,
                output=model_response.output,
                grader_payload=dataset_case.grader_payload,
                grader_id=runtime.grader_id,
                grader_sha256=runtime.grader_sha256,
            )
        )
        _require_sha256("observed_grader_sha256", grader_response.observed_grader_sha256)
        if grader_response.observed_grader_sha256 != runtime.grader_sha256:
            raise ValueError("grader callback hash mismatch")

        # These IDs attest adapter-controlled prompt injection, not cognitive
        # use by the model. Counterfactual degradation is still required.
        exposed_ids = tuple(item.artifact_id for item in request.available_artifacts)
        return ExecutionObservation(
            score=grader_response.score,
            output=model_response.output,
            read_artifact_ids=exposed_ids,
            input_tokens=model_response.input_tokens,
            output_tokens=model_response.output_tokens,
            metadata={
                "adapter_exposure_ids": list(exposed_ids),
                "read_trace_kind": "adapter_controlled_prompt_exposure",
                "observed_prompt_sha256": model_response.observed_prompt_sha256,
                "observed_model_sha256": model_response.observed_model_sha256,
                "observed_grader_sha256": grader_response.observed_grader_sha256,
                "model_metadata_sha256": _sha256_json(dict(model_response.metadata)),
                "grader_metadata_sha256": _sha256_json(dict(grader_response.metadata)),
            },
        )

    report = run_attribution_harness(attribution_cases, execute, config=active_config)
    report["live_adapter"] = {
        "schema": LIVE_ADAPTER_SCHEMA,
        "runtime_contract": {
            "model_id": runtime.model_id,
            "model_sha256": runtime.model_sha256,
            "prompt_template_sha256": runtime.prompt_template_sha256,
            "grader_id": runtime.grader_id,
            "grader_sha256": runtime.grader_sha256,
        },
        "attestation": {
            "per_call_prompt_hash_checked": True,
            "per_call_model_hash_checked": True,
            "per_call_grader_hash_checked": True,
            "callback_hash_echo_verified": True,
            "callback_attestation_is_independent": False,
            "retriever_read_only_flag_is_self_attested": True,
        },
        "dataset_classification": {
            "accepted_values": sorted(ALLOWED_DATA_CLASSIFICATIONS),
            "attestation_source": "dataset_author_self_declaration",
            "independently_verified": False,
        },
        "retrieval": [
            {
                "case_id": case.case_id,
                "route_id": retrievals[case.case_id].route_id,
                "query_sha256": retrievals[case.case_id].query_sha256,
                "query_hit_ids": [item.memory_id for item in retrievals[case.case_id].query_hits],
                "resolved_artifact_ids": sorted(retrievals[case.case_id].artifacts_by_id),
                "read_only_attested": retrievals[case.case_id].read_only_attested,
                "limits": list(retrievals[case.case_id].limits),
            }
            for case in dataset_cases
        ],
        "limits": [
            "no_causal_proof_claimed",
            "model_and_grader_hashes_are_callback_attestations_not_remote_attestation",
            "adapter_exposure_ids_do_not_prove_cognitive_use",
            "read_only_scope_is_the_bundled_retriever_not_arbitrary_callbacks",
            "injected_retriever_read_only_flag_is_not_independently_verified",
            "only_public_or_synthetic_benchmark_datasets_are_accepted",
            "dataset_classification_is_self_declared_not_independently_verified",
            "production_recall_audit_wrapper_is_not_called_to_preserve_read_only_operation",
        ],
    }
    report["matched_contract"]["external_callback_attested"] = False
    report["matched_contract"]["callback_hash_echo_verified"] = True
    report["matched_contract"]["claim"] = (
        "matched_requests_with_callback_hash_attestation_not_independent_runtime_proof"
    )
    report["matched_contract"]["not_observed_by_harness"] = [
        "independent_remote_attestation_of_model_binary",
        "independent_remote_attestation_of_grader_binary",
        "model_internal_use_of_exposed_artifacts",
        "unreported_external_state_inside_callbacks",
    ]
    _reseal_report(report)
    return report


def make_recall_router_readonly_retriever(pool: Any, *, limit: int = 50) -> RetrieverFn:
    """Build a SELECT-only retriever using Recall Router memory ranking internals.

    This deliberately bypasses ``soul_recall_router`` because that function
    creates/writes ``recall_audit``.  Expected targets must appear in the real
    ``_recall_memories`` query result; explicit placebo IDs are resolved by a
    second SELECT under the same agent/shared visibility rule.
    """

    if limit < 1:
        raise ValueError("limit must be positive")

    async def retrieve(case: LiveDatasetCase) -> RetrievalResult:
        import recall_router

        intents = recall_router.classify_recall_intent(case.query)
        hits = await recall_router._recall_memories(case.query, case.agent, pool, intents, limit)
        ranked = recall_router._rank(recall_router._dedup(hits), intents)[:limit]
        required_ids = tuple(dict.fromkeys((*case.expected_memory_ids, *case.placebo_memory_ids)))
        try:
            numeric_ids = [int(memory_id) for memory_id in required_ids]
        except ValueError as exc:
            raise ValueError("live SOUL memory IDs must be integers") from exc
        rows = await pool.fetch(
            """
            SELECT id, agent, category, content, scope, importance, created_at
            FROM soul_v3.memories
            WHERE id = ANY($1::bigint[])
              AND invalid_at IS NULL
              AND (agent = $2 OR scope IN ('shared', 'team'))
            """,
            numeric_ids,
            case.agent,
        )
        artifacts = {
            str(row["id"]): RetrievedMemory(
                memory_id=str(row["id"]),
                source="memories",
                content=row["content"] or "",
                metadata={
                    "agent": row["agent"],
                    "category": row["category"],
                    "scope": row["scope"],
                    "importance": row["importance"],
                    "created_at": str(row["created_at"]),
                },
            )
            for row in rows
        }
        query_hits = tuple(
            RetrievedMemory(
                memory_id=str(hit["id"]),
                source=str(hit["source"]),
                content=str(hit["content"]),
                metadata={
                    "agent": hit.get("agent"),
                    "category": hit.get("category"),
                    "score_final": hit.get("score_final"),
                },
            )
            for hit in ranked
        )
        return RetrievalResult(
            query_hits=query_hits,
            artifacts_by_id=artifacts,
            query_sha256=_sha256_text(case.query),
            route_id="recall_router._recall_memories+_dedup+_rank/select-only-v1",
            read_only_attested=True,
            limits=(
                "memories_source_only",
                "BM25_recall_router_path_without_recall_audit_write",
                "placebos_resolved_by_explicit_id_SELECT",
            ),
        )

    return retrieve
