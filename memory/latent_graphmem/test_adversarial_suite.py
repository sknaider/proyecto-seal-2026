"""Adversarial test suite for latent_graphmem router — attacks P2.4/P3.7/P3.8/P3.9.

Runs 20 queries through query_classifier.classify(), prints per-query result
and an observability summary at the end (rule_hit / llm_hit / llm_fail counters).

Wrapper-based instrumentation: does NOT modify query_classifier.py. Counts are
tracked by intercepting _rule_classify and _llm_classify at module level.

Usage:
  cd ~/IA/proyecto-seal
  /home/dadito/IA/seal-spark/.venv/bin/python3 -m memory.latent_graphmem.test_adversarial_suite
"""
from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass

import httpx

from memory.latent_graphmem import query_classifier as qc


@dataclass
class Case:
    query: str
    expected_type: str
    attacks: str  # which hallazgo this targets


CASES: list[Case] = [
    # P2.4 — multi_hop without obvious markers (100% LLM, tests qwen accuracy)
    Case("¿Quién creó ALICE y qué adapter usa en producción?", "multi_hop", "P2.4"),
    Case("El modelo base de SEAL y su quantización de release", "multi_hop", "P2.4"),
    Case("Autor del paper MAGMA y fecha de integración al stack", "multi_hop", "P2.4"),
    Case("ALICE y Henry: relación y fecha de incorporación", "multi_hop", "P2.4"),
    # P3.8 — tildes / punctuation / case (rule normalization missing)
    Case("Por que retiramos MemR3", "causal", "P3.8"),  # sin tildes, sin ¿
    Case("cuando... se unió ALICE?", "temporal", "P3.8"),  # puntos suspensivos
    Case("¿POR QUÉ MemR3 FALLÓ?", "causal", "P3.8"),  # uppercase
    # P3.9 — entity / negation (100% LLM, no rules cover these)
    Case("¿Qué rol tiene Henry?", "entity", "P3.9"),
    Case("¿SEAL corre en la nube?", "negation", "P3.9"),
    Case("¿JARVIS es el mismo modelo que ADA?", "negation", "P3.9"),
    # P3.7 prep — observability: mix of known rule-path queries
    Case("¿Por qué SOUL Lite es local-first?", "causal", "P3.7-baseline"),
    Case("¿Cuándo retiramos MemR3?", "temporal", "P3.7-baseline"),
    Case("Si SOUL Lite es para privacidad, ¿por qué necesita Docker?", "inference", "P3.7-baseline"),
    Case("¿Quién creó ALICE?", "factual", "P3.7-baseline"),
    Case("¿Qué rol tiene JARVIS?", "entity", "P3.7-baseline"),
    Case("¿ALICE es un modelo de Anthropic?", "negation", "P3.7-baseline"),
    Case("¿En qué mes se implementó TG-RAG Phase 2?", "temporal", "P3.7-baseline"),
    Case("¿Hace cuánto retiramos MemR3?", "temporal", "P2.5-fix-verify"),
    Case("¿Desde cuándo TG-RAG está en producción?", "temporal", "P2.5-fix-verify"),
    Case("Por qué falló la v3 de MemR3", "causal", "P2.6-fix-verify"),
]


class Counters:
    rule_hit = Counter()
    llm_hit = Counter()
    llm_fail = 0


# Wrap _rule_classify
_orig_rule = qc._rule_classify

def _wrapped_rule(query: str):
    result = _orig_rule(query)
    if result is not None:
        Counters.rule_hit[result] += 1
    return result

qc._rule_classify = _wrapped_rule

# Wrap _llm_classify
_orig_llm = qc._llm_classify

async def _wrapped_llm(query: str, client: httpx.AsyncClient):
    try:
        result = await _orig_llm(query, client)
        Counters.llm_hit[result] += 1
        return result
    except Exception:
        Counters.llm_fail += 1
        raise

qc._llm_classify = _wrapped_llm


async def run_suite() -> None:
    correct = 0
    total = len(CASES)
    rows: list[tuple[str, str, str, str, bool]] = []
    async with httpx.AsyncClient() as client:
        for case in CASES:
            qtype = await qc.classify(case.query, client)
            backend = qc.route(qtype)
            ok = qtype == case.expected_type
            if ok:
                correct += 1
            rows.append((case.attacks, case.query, case.expected_type, f"{qtype} → {backend}", ok))

    print("=" * 100)
    print(f"{'ATTACK':<20} {'EXPECTED':<12} {'ACTUAL':<25} OK  QUERY")
    print("-" * 100)
    for attack, q, exp, actual, ok in rows:
        mark = "✓" if ok else "✗"
        print(f"{attack:<20} {exp:<12} {actual:<25} {mark}   {q[:50]}")
    print("-" * 100)
    print(f"ACCURACY: {correct}/{total} = {correct/total:.1%}")
    print()
    print("=" * 100)
    print("OBSERVABILITY (P3.7)")
    print("-" * 100)
    rule_total = sum(Counters.rule_hit.values())
    llm_total = sum(Counters.llm_hit.values())
    print(f"Rule-path hits:  {rule_total:>3}  breakdown: {dict(Counters.rule_hit)}")
    print(f"LLM-path hits:   {llm_total:>3}  breakdown: {dict(Counters.llm_hit)}")
    print(f"LLM fails:       {Counters.llm_fail:>3}")
    if rule_total + llm_total > 0:
        rule_rate = rule_total / (rule_total + llm_total)
        print(f"Rule fast-path coverage: {rule_rate:.1%}")
    print("=" * 100)


if __name__ == "__main__":
    asyncio.run(run_suite())
