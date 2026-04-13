"""query_classifier.py — Rule-assisted LLM classifier for LatentGraphMem router.

Classifies a query into one of 7 types and decides which retriever to invoke:
  - LATENT  → causal, temporal, multi_hop, inference  (LatentGraphMem wins these)
  - MAGMA   → factual, entity, negation               (MAGMA wins or ties these)

Design:
  - Rule-based fast path (regex heuristics) catches ~60% with high precision.
  - Fallback to qwen2.5:7b few-shot prompt for ambiguous queries.
  - Routing rule derived from diagnostic_eval_extended.py run 2026-04-12.

Usage:
  from latent_graphmem.query_classifier import classify, route
  qtype = await classify("¿Por qué SOUL Lite es local-first?")  # → "causal"
  backend = route(qtype)                                         # → "latent"
"""
from __future__ import annotations

import logging
import re
from typing import Literal

import httpx

_logger = logging.getLogger("latent_graphmem.query_classifier")

QueryType = Literal["factual", "temporal", "causal", "entity", "multi_hop", "negation", "inference"]
Backend = Literal["latent", "magma"]

LATENT_TYPES = {"causal", "temporal", "multi_hop", "inference"}

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5:7b"

# Rule-based fast path — HIGH PRECISION only. Ambiguous cases fall to LLM.
# Lesson from V1 (accuracy 56%): greedy rules hurt more than they help.
_RULES: list[tuple[re.Pattern, QueryType]] = [
    # Inference: explicit "si ... entonces/implica" construction
    (re.compile(r"^\s*¿?\s*si\b.*\b(entonces|implica|por\s*qu[ée]\s+necesita)\b", re.I), "inference"),
    # Causal: starts with "por qué" — opening ¿ is optional (chat informal)
    (re.compile(r"^\s*¿?\s*por\s*qu[ée]\b", re.I), "causal"),
    # Temporal: "cuándo" / "en qué fecha..." / "hace cuánto" / "desde cuándo"
    (re.compile(
        r"^\s*¿?\s*("
        r"cu[áa]ndo|"
        r"en\s+qu[ée]\s+(fecha|mes|año|d[íi]a)|"
        r"hace\s+cu[áa]nto|"
        r"desde\s+cu[áa]ndo"
        r")\b",
        re.I,
    ), "temporal"),
]

FEWSHOT_EXAMPLES = """Clasifica la siguiente pregunta en EXACTAMENTE uno de estos tipos:
factual, temporal, causal, entity, multi_hop, negation, inference

Ejemplos:
P: ¿Quién creó a ALICE?
T: factual

P: ¿Cuándo se unió ALICE al equipo SEAL?
T: temporal

P: ¿Por qué SOUL Lite es 'local-first' y no depende de la nube?
T: causal

P: ¿Qué rol tiene Henry (Kinger) en el equipo SEAL?
T: entity

P: ¿Quién creó ALICE y qué presupuesto tiene su creador para comercializar SOUL?
T: multi_hop

P: ¿ALICE es un modelo de lenguaje de Anthropic?
T: negation

P: Si SOUL Lite es para usuarios que quieren privacidad, ¿por qué necesita Docker?
T: inference

P: {query}
T:"""

_VALID = {"factual", "temporal", "causal", "entity", "multi_hop", "negation", "inference"}


def _rule_classify(query: str) -> QueryType | None:
    """Fast path — returns a type if a high-precision rule matches, else None."""
    q = query.strip()
    for pat, qtype in _RULES:
        if pat.search(q):
            return qtype
    return None


async def _llm_classify(query: str, client: httpx.AsyncClient) -> QueryType:
    """Fallback — qwen2.5:7b few-shot."""
    prompt = FEWSHOT_EXAMPLES.format(query=query)
    try:
        r = await client.post(
            OLLAMA_URL,
            json={
                "model": MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0, "num_predict": 8, "stop": ["\n", "P:"]},
            },
            timeout=20.0,
        )
        r.raise_for_status()
        out = r.json().get("response", "").strip().lower()
        for tok in re.findall(r"[a-z_]+", out):
            if tok in _VALID:
                return tok  # type: ignore
        _logger.warning(
            "llm_classify: no valid token (query=%r, raw=%r) — default=factual",
            query[:80], out[:40],
        )
    except Exception as e:
        _logger.warning(
            "llm_classify: exception (query=%r, err=%s) — default=factual",
            query[:80], type(e).__name__,
        )
    return "factual"  # safest default (routes to MAGMA which is the current champion)


async def classify(query: str, client: httpx.AsyncClient | None = None) -> QueryType:
    """Classify a query. Uses rule-based fast path, falls back to qwen2.5:7b."""
    rule = _rule_classify(query)
    if rule is not None:
        return rule
    if client is None:
        async with httpx.AsyncClient() as c:
            return await _llm_classify(query, c)
    return await _llm_classify(query, client)


def route(qtype: QueryType) -> Backend:
    """Decide which retriever to invoke based on query type."""
    return "latent" if qtype in LATENT_TYPES else "magma"


async def classify_and_route(query: str, client: httpx.AsyncClient | None = None) -> tuple[QueryType, Backend]:
    qtype = await classify(query, client)
    return qtype, route(qtype)
