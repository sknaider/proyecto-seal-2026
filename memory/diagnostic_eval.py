#!/usr/bin/env python3
"""
diagnostic_eval.py — Retrieval-vs-Utilization Diagnostic Harness
Paper: arxiv 2603.02473

Purpose: separate two failure modes in SOUL memory pipeline:
  1. Retrieval failure  → relevant memory NOT found (search bug)
  2. Utilization failure → relevant memory found but agent IGNORES it (prompt bug)

Designed by JARVIS, executed by ADA. Test set authored by ALICE.

Status: SKELETON — fill in `_load_test_set()` once ALICE delivers ground truth JSONL.

Run modes (one query × 3 modes):
  - baseline_no_memory  : agent answers cold, no SOUL access
  - hybrid_search       : single-pass hybrid_search retrieval
  - magma_erl           : MAGMA multi-graph fusion + ERL heuristic injection

Metrics per query × mode:
  - recall_at_k       : was the ground-truth memory id in top-k results?
  - usage_rate        : did the agent's answer cite/apply the retrieved memory?
  - counterfactual    : would removing the memory change the answer? (true utilization)
  - ignorance_rate    : memory in context but answer identical to baseline (= noise)

Final report: per-mode aggregate + bottleneck classification
  (retrieval-bound vs utilization-bound vs both).
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any

# ── Config ──
TEST_SET_PATH = Path(__file__).parent / "diagnostic" / "test_set_v1.jsonl"
RESULTS_DIR = Path(__file__).parent / "diagnostic" / "results"
DEFAULT_TOP_K = 5
DEFAULT_AGENT = "ADA"
LLAMA_URL = "http://localhost:8899/v1/chat/completions"
LLAMA_MODEL = "gemma4-31b"
OLLAMA_TIMEOUT = 60.0


class Mode(str, Enum):
    BASELINE_NO_MEMORY = "baseline_no_memory"
    HYBRID_SEARCH = "hybrid_search"
    MAGMA_ERL = "magma_erl"


@dataclass
class TestQuery:
    """One ground-truth query from ALICE's test set (schema v1)."""
    query_id: str                        # id field in JSONL
    query: str
    expected_memory_ids: list[int]
    expected_answer: str                 # full reference answer
    ground_truth_content: str            # memory content that SHOULD satisfy the query
    query_type: str                      # factual | temporal | causal | entity | multi_hop | negation | inference
    difficulty: str = "medium"           # easy | medium | hard
    notes: str = ""


@dataclass
class QueryResult:
    """One (query × mode) execution outcome."""
    query_id: str
    mode: Mode
    retrieved_ids: list[int] = field(default_factory=list)
    answer: str = ""
    latency_ms: int = 0
    # Metrics (filled by _score)
    recall_at_k: float = 0.0
    usage: bool = False
    counterfactual_changed: bool | None = None
    error: str | None = None


# ── Test set loading ──

def _load_test_set(path: Path = TEST_SET_PATH) -> list[TestQuery]:
    """Load ALICE's JSONL test set. Returns [] if not yet delivered."""
    if not path.exists():
        return []
    queries: list[TestQuery] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                d = json.loads(line)
                queries.append(TestQuery(
                    query_id=d["id"],
                    query=d["query"],
                    expected_memory_ids=d.get("expected_memory_ids", []),
                    expected_answer=d.get("expected_answer", ""),
                    ground_truth_content=d.get("ground_truth_content", ""),
                    query_type=d.get("query_type", "factual"),
                    difficulty=d.get("difficulty", "medium"),
                    notes=d.get("notes", ""),
                ))
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                print(f"[WARN] skipping malformed line: {e}", file=sys.stderr)
    return queries


# ── LLM helper ──

async def _ollama_generate(prompt: str, max_tokens: int = 400) -> str:
    """Call llama-server (gemma4-31b) and return the response text. Raises on failure."""
    import httpx
    async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT) as client:
        resp = await client.post(
            LLAMA_URL,
            json={
                "model": LLAMA_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0,
                "stream": False,
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()


def _build_answer_prompt(query: str, context_block: str | None) -> str:
    """Build a minimal Spanish answer prompt with optional retrieved context."""
    if context_block:
        return (
            "Responde la pregunta usando SOLO la información del contexto. "
            "Si el contexto no es suficiente, di 'no sé'. Responde en español, máximo 2 frases.\n\n"
            f"Contexto:\n{context_block}\n\n"
            f"Pregunta: {query}\n\nRespuesta:"
        )
    return (
        "Responde la pregunta en español, máximo 2 frases. "
        "Si no sabes, di 'no sé' — no inventes.\n\n"
        f"Pregunta: {query}\n\nRespuesta:"
    )


def _results_to_context(results: list[dict], max_chars: int = 1500) -> tuple[str, list[int]]:
    """Convert a list of memory hits into a numbered context block + retrieved IDs."""
    lines = []
    ids: list[int] = []
    total = 0
    for i, r in enumerate(results, 1):
        mid = r.get("id")
        if isinstance(mid, int):
            ids.append(mid)
        content = str(r.get("content", ""))[:300]
        line = f"[{i}] {content}"
        if total + len(line) > max_chars:
            break
        lines.append(line)
        total += len(line)
    return "\n".join(lines) if lines else "", ids


# ── Mode runners ──

async def _run_baseline(q: TestQuery) -> QueryResult:
    """Mode 1: LLM only, no SOUL access. Establishes the floor."""
    r = QueryResult(query_id=q.query_id, mode=Mode.BASELINE_NO_MEMORY)
    t0 = time.perf_counter()
    try:
        r.answer = await _ollama_generate(_build_answer_prompt(q.query, None))
    except Exception as e:
        r.error = f"{type(e).__name__}: {e}"
    r.latency_ms = int((time.perf_counter() - t0) * 1000)
    return r


async def _run_hybrid(q: TestQuery, top_k: int = DEFAULT_TOP_K) -> QueryResult:
    """Mode 2: memory_hybrid_search → LLM answer."""
    from mcp_server_v3 import memory_hybrid_search  # lazy import
    r = QueryResult(query_id=q.query_id, mode=Mode.HYBRID_SEARCH)
    t0 = time.perf_counter()
    try:
        raw = await memory_hybrid_search(query=q.query, agent=DEFAULT_AGENT, limit=top_k)
        results = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(results, list):
            results = []
        ctx, r.retrieved_ids = _results_to_context(results)
        r.answer = await _ollama_generate(_build_answer_prompt(q.query, ctx or None))
    except Exception as e:
        r.error = f"{type(e).__name__}: {e}"
    r.latency_ms = int((time.perf_counter() - t0) * 1000)
    return r


async def _run_magma_erl(q: TestQuery, top_k: int = DEFAULT_TOP_K) -> QueryResult:
    """Mode 4: MAGMA multi-graph fusion (+ ERL heuristics if available)."""
    from mcp_server_v3 import magma_retrieve  # lazy import
    try:
        from mcp_server_v3 import erl_inject  # optional
    except ImportError:
        erl_inject = None  # type: ignore
    r = QueryResult(query_id=q.query_id, mode=Mode.MAGMA_ERL)
    t0 = time.perf_counter()
    try:
        raw = await magma_retrieve(agent=DEFAULT_AGENT, query=q.query, top_k=top_k)
        payload = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(payload, dict):
            results = payload.get("memories") or payload.get("results") or []
        elif isinstance(payload, list):
            results = payload
        else:
            results = []
        ctx_main, r.retrieved_ids = _results_to_context(results)
        heuristic_block = ""
        if erl_inject is not None:
            try:
                eraw = await erl_inject(agent=DEFAULT_AGENT, task_description=q.query, top_k=3)
                epayload = json.loads(eraw) if isinstance(eraw, str) else eraw
                heuristic_block = (epayload or {}).get("formatted_context", "") if isinstance(epayload, dict) else ""
            except Exception:
                heuristic_block = ""
        ctx = (ctx_main + ("\n\n" + heuristic_block if heuristic_block else "")).strip()
        r.answer = await _ollama_generate(_build_answer_prompt(q.query, ctx or None))
    except Exception as e:
        r.error = f"{type(e).__name__}: {e}"
    r.latency_ms = int((time.perf_counter() - t0) * 1000)
    return r


MODE_RUNNERS = {
    Mode.BASELINE_NO_MEMORY: _run_baseline,
    Mode.HYBRID_SEARCH: _run_hybrid,
    Mode.MAGMA_ERL: _run_magma_erl,
}


# ── Scoring ──

_STOPWORDS = {
    "the", "a", "an", "of", "to", "in", "on", "at", "for", "and", "or", "is",
    "es", "la", "el", "los", "las", "un", "una", "de", "del", "y", "o", "en",
    "que", "qué", "con", "por", "para", "se", "al", "lo", "como", "cómo",
    "su", "sus", "ser", "este", "esta", "esto", "fue", "son", "por",
}

# Token regex that preserves numbers, decimals, '=' pairs like "O=0.80"
_TOKEN_RE = re.compile(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]+(?:=[\d.]+)?|[\d]+(?:[.,]\d+)?")


def _extract_tokens(text: str, k: int = 6) -> list[str]:
    """Pick up to k significant tokens from expected answer.

    Preserves structured patterns (O=0.80), decimals (0.80), proper nouns.
    Rejects stopwords and tokens shorter than 3 chars unless numeric/structured.
    """
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall((text or "").lower()):
        if raw in _STOPWORDS:
            continue
        # Keep structured (has = or digit) always; otherwise require len>3
        has_struct = "=" in raw or any(c.isdigit() for c in raw)
        if not has_struct and len(raw) <= 3:
            continue
        tokens.append(raw)
        if len(tokens) >= k:
            break
    return tokens


def _score(result: QueryResult, q: TestQuery) -> None:
    """Fill recall/usage on result in place.

    usage=True if ≥50% of significant tokens from expected_answer appear
    in the answer (case-insensitive substring match). Falls back to
    ground_truth_content tokens if expected_answer yields nothing.
    """
    expected = set(q.expected_memory_ids)
    if expected:
        hit = expected.intersection(result.retrieved_ids)
        result.recall_at_k = len(hit) / len(expected)

    ans_lc = (result.answer or "").lower()
    tokens = _extract_tokens(q.expected_answer, k=6)
    if not tokens:
        tokens = _extract_tokens(q.ground_truth_content, k=6)
    if tokens and ans_lc:
        matched = sum(1 for t in tokens if t in ans_lc)
        result.usage = matched >= max(1, (len(tokens) + 1) // 2)
    else:
        result.usage = False


# ── Aggregate + bottleneck classification ──

def _percentile(values: list[float], pct: float) -> float:
    """Naive percentile (no numpy dep). pct in [0,100]."""
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (pct / 100.0)
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _aggregate(results: list[QueryResult]) -> dict[str, Any]:
    """Roll up per-mode stats and classify bottleneck."""
    by_mode: dict[str, dict[str, float]] = {}
    for mode in Mode:
        m_results = [r for r in results if r.mode == mode and r.error is None]
        if not m_results:
            continue
        n = len(m_results)
        latencies = [r.latency_ms for r in m_results]
        by_mode[mode.value] = {
            "n": n,
            "recall_at_k": sum(r.recall_at_k for r in m_results) / n,
            "usage_rate": sum(1 for r in m_results if r.usage) / n,
            "ignorance_rate": sum(
                1 for r in m_results
                if r.recall_at_k > 0 and not r.usage
            ) / n,
            "avg_latency_ms": sum(latencies) / n,
            "p50_latency_ms": _percentile(latencies, 50),
            "p95_latency_ms": _percentile(latencies, 95),
        }

    # Bottleneck classification (simple heuristic)
    diagnosis = "insufficient_data"
    if Mode.HYBRID_SEARCH.value in by_mode:
        h = by_mode[Mode.HYBRID_SEARCH.value]
        if h["recall_at_k"] < 0.5:
            diagnosis = "retrieval_bound"
        elif h["usage_rate"] < 0.5 and h["recall_at_k"] >= 0.7:
            diagnosis = "utilization_bound"
        elif h["recall_at_k"] >= 0.7 and h["usage_rate"] >= 0.7:
            diagnosis = "healthy"
        else:
            diagnosis = "mixed"

    return {"by_mode": by_mode, "diagnosis": diagnosis}


# ── Entry point ──

async def run(top_k: int = DEFAULT_TOP_K) -> dict[str, Any]:
    queries = _load_test_set()
    if not queries:
        return {
            "status": "blocked",
            "reason": f"test set not found at {TEST_SET_PATH} — waiting on ALICE",
        }

    results: list[QueryResult] = []
    for q in queries:
        for mode, runner in MODE_RUNNERS.items():
            try:
                r = await runner(q, top_k=top_k) if mode != Mode.BASELINE_NO_MEMORY else await runner(q)
            except NotImplementedError as e:
                r = QueryResult(query_id=q.query_id, mode=mode, error=str(e))
            except Exception as e:
                r = QueryResult(query_id=q.query_id, mode=mode, error=f"{type(e).__name__}: {e}")
            else:
                _score(r, q)
            results.append(r)

    report = _aggregate(results)
    report["queries_total"] = len(queries)
    report["results"] = [asdict(r) for r in results]

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / "latest.json"
    out.write_text(json.dumps(report, indent=2, default=str))
    return report


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run()), indent=2, default=str))
