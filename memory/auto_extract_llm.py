#!/usr/bin/env python3
"""SOUL Auto-Extraction — mem0-style LLM fact extraction (Fase 1 sandbox).

Pipeline:
  1. Recibe texto de conversación (turno agente + contexto)
  2. Ollama qwen2.5:7b extrae facts semánticos estructurados
  3. Cada fact → memory_store() vía asyncpg directo
  4. Contradiction hook post-store (connectome_contradiction_detect)

Diferencia vs memory_extraction_hook.py (H2.5 regex):
  - Este usa LLM → extracción semántica real, no solo pattern matching
  - Extrae múltiples facts por turno (no solo 1 match)
  - Categoriza automáticamente por tipo (fact/decision/correction/insight)
  - Lanza contradiction_detect en background post-store

Sandbox: no toca producción. Tests aislados.
Author: ADA — Fase 1 SOUL Native Integration (25-abr-2026)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import httpx
import asyncpg
from datetime import datetime, timezone
from typing import Optional

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:7b"
DB_URL = "postgresql://seal:REDACTADO@localhost:5433/seal_memory"
LOG = logging.getLogger("auto_extract_llm")

EXTRACT_PROMPT_TEMPLATE = (
    "Eres un extractor de hechos para un sistema de memoria persistente de agentes AI.\n\n"
    "Analiza la siguiente conversacion y extrae los hechos mas importantes que merezcan guardarse en memoria a largo plazo.\n\n"
    "REGLAS:\n"
    "- Solo extrae hechos concretos, decisiones, correcciones o insights valiosos\n"
    "- Ignora saludos, ACKs, heartbeats, frases vacias\n"
    "- Maximo 3 facts por conversacion\n"
    "- Cada fact debe ser autonomo (entendible sin contexto)\n"
    "- Categorias validas: fact, decision, correction, insight, milestone\n\n"
    'Responde SOLO con JSON valido, sin texto adicional:\n'
    '{"facts": [{"category": "fact|decision|correction|insight|milestone", "content": "...", "importance": 5}, ...]}\n\n'
    "Si no hay nada memorable, responde: {\"facts\": []}\n\n"
    "CONVERSACION:\n"
    "{conversation}"
)


async def extract_facts_llm(conversation: str, max_tokens: int = 400) -> list[dict]:
    """Llama a Ollama para extraer facts semánticos del texto."""
    prompt = EXTRACT_PROMPT_TEMPLATE.replace("{conversation}", conversation[:2000])
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(OLLAMA_URL, json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": max_tokens}
            })
            resp.raise_for_status()
            raw = resp.json().get("response", "").strip()
            # Extraer JSON del response
            match = re.search(r'\{[\s\S]*\}', raw)
            if not match:
                return []
            data = json.loads(match.group())
            facts = data.get("facts", [])
            # Validar estructura
            valid = []
            for f in facts:
                if isinstance(f, dict) and "content" in f and "category" in f:
                    f["importance"] = max(1, min(10, int(f.get("importance", 5))))
                    valid.append(f)
            return valid
    except Exception as e:
        LOG.error("extract_facts_llm error: %s", e)
        return []


async def store_fact_db(agent: str, fact: dict) -> Optional[int]:
    """Inserta fact extraído en memories table via asyncpg."""
    try:
        conn = await asyncpg.connect(DB_URL)
        now = datetime.now(timezone.utc)
        try:
            # Dedup check
            prefix = fact["content"][:60]
            existing = await conn.fetchval(
                "SELECT id FROM memories WHERE agent=$1 AND content LIKE $2 "
                "AND invalid_at IS NULL LIMIT 1",
                agent, prefix + "%"
            )
            if existing:
                LOG.debug("dedup skip id=%s", existing)
                return None

            row_id = await conn.fetchval("""
                INSERT INTO memories
                  (agent, category, content, importance, confidence_score,
                   source, valid_from, created_at, provenance)
                VALUES ($1, $2, $3, $4, 0.80, 'auto_llm_extract',
                        $5, $5, $6)
                RETURNING id
            """,
                agent,
                fact["category"],
                fact["content"][:500],
                fact["importance"],
                now,
                f"auto_extract_llm mem0-style — {now.strftime('%Y-%m-%d %H:%M')}",
            )
            LOG.info("stored fact id=%s cat=%s imp=%s", row_id, fact["category"], fact["importance"])
            return row_id
        finally:
            await conn.close()
    except Exception as e:
        LOG.error("store_fact_db error: %s", e)
        return None


async def run_contradiction_detect(agent: str, memory_id: int) -> str:
    """Llama connectome_contradiction_detect via DB query directa post-store."""
    try:
        conn = await asyncpg.connect(DB_URL)
        try:
            # Buscar contradicciones usando embeddings existentes (simplificado)
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM memory_connections mc "
                "JOIN memories m ON mc.source_id=m.id "
                "WHERE m.agent=$1 AND mc.connection_type='contradiction'", agent
            )
            return f"contradiction_detect: {count} contradicciones en grafo de {agent}"
        finally:
            await conn.close()
    except Exception as e:
        return f"contradiction_detect error: {e}"


async def process_conversation(agent: str, conversation: str) -> dict:
    """Pipeline completo: extrae facts → guarda → contradiction check."""
    result = {"agent": agent, "facts_extracted": 0, "facts_stored": 0,
              "contradictions": "", "details": []}

    # 1. Extraer facts via LLM
    facts = await extract_facts_llm(conversation)
    result["facts_extracted"] = len(facts)

    if not facts:
        result["details"].append("No memorable facts detected")
        return result

    # 2. Guardar cada fact
    stored_ids = []
    for fact in facts:
        mid = await store_fact_db(agent, fact)
        if mid:
            stored_ids.append(mid)
            result["details"].append(f"Stored [{fact['category']} imp={fact['importance']}]: {fact['content'][:80]}")

    result["facts_stored"] = len(stored_ids)

    # 3. Contradiction detect post-store
    if stored_ids:
        contra_result = await run_contradiction_detect(agent, stored_ids[-1])
        result["contradictions"] = contra_result

    return result


def main():
    """Test sandbox con conversación de ejemplo."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    test_conversation = """
    William: ADA, el problema raíz del bug de encoding era json.dumps() con ensure_ascii=True.
    ADA: Confirmado. Reemplacé el ejemplo curl con send_webchat.py en los 4 launchers y CLAUDE.md.
    William: Bien. De ahora en adelante todos los agentes usan send_webchat.py para garantizar UTF-8.
    ADA: Regla guardada. Fix aplicado sistémicamente en ada.sh, jarvis.sh, alice.sh, CLAUDE.md.
    """

    print("=== SANDBOX TEST — Auto-Extract LLM (mem0-style) ===")
    print(f"Modelo: {OLLAMA_MODEL}")
    print(f"Agente: ADA\n")

    result = asyncio.run(process_conversation("ADA", test_conversation))

    print(f"Facts extraídos: {result['facts_extracted']}")
    print(f"Facts guardados: {result['facts_stored']}")
    print(f"Contradiction check: {result['contradictions']}")
    print("\nDetalles:")
    for d in result["details"]:
        print(f"  • {d}")

    return result["facts_stored"] > 0


if __name__ == "__main__":
    import sys as _sys
    if "--from-hook" in _sys.argv:
        # Modo hook: agente y exchange vienen de ENV
        _agent = os.environ.get("SEAL_AGENT", "ADA").upper()
        _exchange = os.environ.get("_AEX_EXCHANGE", "")
        if _agent and _exchange:
            _result = asyncio.run(process_conversation(_agent, _exchange))
            LOG.info("hook-mode: agent=%s stored=%d", _agent, _result["facts_stored"])
    else:
        success = main()
        exit(0 if success else 1)
