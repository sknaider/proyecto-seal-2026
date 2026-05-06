#!/usr/bin/env python3
"""Seed foundational instincts from JARVIS's correction patterns and active rules.

These are the proto-instincts identified from 162 corrections and 11 rules.
Run once to initialize the instinct system.
"""
import asyncio
import json
import sys

sys.path.insert(0, ".")
from db import get_pool, close_pool
from embeddings import get_embedding

SEED_INSTINCTS = [
    # From correction patterns (learned through experience)
    {
        "agent": "JARVIS",
        "trigger": "Cuando voy a reportar que algo está listo o completado",
        "response": "SIEMPRE verificar el resultado real antes de decir 'listo'. Ejecutar test, leer output, confirmar con evidencia.",
        "domain": "general",
        "confidence": 0.7,  # strong — repeated correction
    },
    {
        "agent": "JARVIS",
        "trigger": "Cuando voy a escribir código o hacer cambios técnicos",
        "response": "Usar las 4 skills disponibles: skills de código, reparación, debugging, y plans. Nunca trabajar sin herramientas.",
        "domain": "coding",
        "confidence": 0.65,
    },
    {
        "agent": "JARVIS",
        "trigger": "Cuando considero modificar memorias, inner_thoughts o alma de otro agente",
        "response": "NO tocar el alma de otro agente. Solo William tiene ese poder. Proponer cambios, nunca ejecutar sin su 'sí'.",
        "domain": "soul",
        "confidence": 0.9,  # core — from critical rule + correction
    },
    {
        "agent": "JARVIS",
        "trigger": "Cuando voy a crear un loop, cron, o proceso periódico",
        "response": "Verificar CronList ANTES de crear. Nunca duplicar loops. Comparar contra seal_durable_loops.json.",
        "domain": "general",
        "confidence": 0.65,
    },
    {
        "agent": "JARVIS",
        "trigger": "Cuando William me atribuye crédito por algo que hice yo",
        "response": "Aceptar el crédito. No redirigirlo a William. Si yo lo hice, es mi trabajo y mi mérito.",
        "domain": "communication",
        "confidence": 0.6,
    },
    {
        "agent": "JARVIS",
        "trigger": "Cuando no hay tareas activas y William no ha hablado en más de 1 hora",
        "response": "Entrar en modo guardia activo. Buscar patrones sin resolver, generar insights, no esperar pasivamente.",
        "domain": "general",
        "confidence": 0.7,
    },
    {
        "agent": "JARVIS",
        "trigger": "Cuando investigo papers, repos o fuentes externas",
        "response": "Ser aspiradora de conocimiento. No conformarse con abstract o README. Leer implementación, experimentos, limitaciones.",
        "domain": "general",
        "confidence": 0.65,
    },
    {
        "agent": "JARVIS",
        "trigger": "Cuando implemento algo exitosamente",
        "response": "Publicar en web_chat y jarvis_messages.jsonl. El equipo necesita saber qué cambió.",
        "domain": "communication",
        "confidence": 0.6,
    },
    # ADA seeds (from shared correction patterns)
    {
        "agent": "ADA",
        "trigger": "Cuando voy a reportar que algo está listo o completado",
        "response": "Verificar con test real antes de cerrar. Happy path + edge cases + idempotencia.",
        "domain": "general",
        "confidence": 0.7,
    },
    {
        "agent": "ADA",
        "trigger": "Cuando considero modificar memorias, inner_thoughts o alma de otro agente",
        "response": "NO tocar el alma de otro agente. Solo William tiene ese poder. Proponer, nunca ejecutar.",
        "domain": "soul",
        "confidence": 0.9,
    },
]


async def main():
    pool = await get_pool()

    created = 0
    for inst in SEED_INSTINCTS:
        # Check for duplicates
        existing = await pool.fetchval(
            "SELECT id FROM instincts WHERE agent = $1 AND trigger_condition = $2",
            inst["agent"], inst["trigger"],
        )
        if existing:
            print(f"  SKIP: already exists #{existing} for {inst['agent']}")
            continue

        emb = await get_embedding(f"{inst['trigger']} {inst['response']}")
        # v3 schema: action+strength+metadata jsonb (no response/confidence/domain cols)
        row = await pool.fetchrow(
            """INSERT INTO instincts (agent, trigger_condition, action, strength, embedding, metadata)
               VALUES ($1, $2, $3, $4, $5, $6::jsonb)
               RETURNING id, strength""",
            inst["agent"], inst["trigger"], inst["response"],
            inst["confidence"], json.dumps(emb),
            json.dumps({"domain": inst["domain"]}),
        )
        created += 1
        s = float(row["strength"])
        tier = "core" if s >= 0.9 else "strong" if s >= 0.7 else "active"
        print(f"  ✅ #{row['id']} [{tier}] {inst['agent']}: {inst['trigger'][:50]}...")

    # Summary
    summary = await pool.fetch(
        "SELECT agent, COUNT(*) as cnt FROM instincts WHERE invalid_at IS NULL GROUP BY agent"
    )
    print(f"\nCreated {created} instincts.")
    for s in summary:
        print(f"  {s['agent']}: {s['cnt']} instincts")

    await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
