#!/usr/bin/env python3
"""
SOUL Import Pipeline — Claude & ChatGPT conversation exports
Ingesta conversaciones existentes como memorias en SOUL.

Formatos soportados:
  - Claude: conversations.json  (claude.ai > Settings > Export data)
  - ChatGPT: conversations.json (chatgpt.com > Settings > Export data)

Uso:
  python3 soul_import.py claude conversations.json --agent ADA --dry-run
  python3 soul_import.py chatgpt conversations.json --agent JARVIS
  python3 soul_import.py claude conversations.json --agent ADA --limit 50 --min-length 100

Lo que importa (por defecto):
  - Solo mensajes del ASSISTANT/model (no del usuario — el usuario ya sabe lo que dijo)
  - Mensajes con longitud >= min-length (filtra saludos vacíos)
  - Categoría: 'fact' para hechos, 'insight' para reflexiones largas
  - Importancia: calculada por longitud + keywords técnicos
  - NO importa duplicados: verifica antes de insertar (content hash)
"""

import argparse
import asyncio
import asyncpg
import hashlib
import json
import re
import sys
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
LIMA_TZ = ZoneInfo("America/Lima")
from pathlib import Path

from config import settings

DB_URL = settings.pg_dsn

# Keywords que suben la importancia del mensaje
HIGH_IMPORTANCE_KEYWORDS = [
    "arquitectura", "architecture", "decisión", "decision", "implementar",
    "implement", "error", "bug", "crítico", "critical", "medical", "médic",
    "soul", "seal", "jarvis", "ada", "william", "memoria", "memory",
    "entrenamiento", "training", "modelo", "model", "pipeline", "deploy",
    "producción", "production", "seguridad", "security", "importante",
    "important", "never", "always", "nunca", "siempre", "regla", "rule",
]

INSIGHT_KEYWORDS = [
    "porque", "because", "la razón", "the reason", "conclusión", "conclusion",
    "aprendí", "learned", "filosofía", "philosophy", "reflexión", "reflection",
    "lo que importa", "what matters", "en retrospectiva", "in retrospect",
]


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def score_importance(text: str) -> int:
    """Calcula importancia 1-8 basada en longitud y keywords."""
    base = 4
    lower = text.lower()

    # Longitud
    if len(text) > 500:
        base += 1
    if len(text) > 1500:
        base += 1

    # Keywords de alta importancia
    matches = sum(1 for kw in HIGH_IMPORTANCE_KEYWORDS if kw in lower)
    base += min(matches, 2)

    return min(base, 8)


def categorize(text: str) -> str:
    """Categoriza el mensaje: insight si es reflexivo, fact si es técnico."""
    lower = text.lower()
    insight_hits = sum(1 for kw in INSIGHT_KEYWORDS if kw in lower)
    if insight_hits >= 2 or len(text) > 2000:
        return "insight"
    return "fact"


# ── Parsers por formato ──────────────────────────────────────────────

def parse_claude_export(data: list | dict, min_length: int) -> list[dict]:
    """
    Claude export format:
    [{
      "uuid": "...",
      "name": "conversation title",
      "created_at": "...",
      "chat_messages": [
        {"uuid": "...", "sender": "human"|"assistant", "text": "...", "created_at": "..."}
      ]
    }]
    """
    messages = []
    convs = data if isinstance(data, list) else [data]

    for conv in convs:
        title = conv.get("name", "Sin título")
        chat_msgs = conv.get("chat_messages", [])

        for msg in chat_msgs:
            if msg.get("sender") != "assistant":
                continue
            text = msg.get("text", "").strip()
            if len(text) < min_length:
                continue

            created_at = msg.get("created_at") or conv.get("created_at")

            messages.append({
                "content": text,
                "event_time": created_at,
                "source_title": title,
                "source_format": "claude",
                "importance": score_importance(text),
                "category": categorize(text),
            })

    return messages


def parse_chatgpt_export(data: list | dict, min_length: int) -> list[dict]:
    """
    ChatGPT export format:
    [{
      "id": "...",
      "title": "...",
      "create_time": 1234567890.0,
      "mapping": {
        "node_id": {
          "message": {
            "author": {"role": "assistant"|"user"|"system"},
            "content": {"parts": ["text..."]},
            "create_time": 1234567890.0
          }
        }
      }
    }]
    """
    messages = []
    convs = data if isinstance(data, list) else [data]

    for conv in convs:
        title = conv.get("title", "Sin título")
        mapping = conv.get("mapping", {})

        for node_id, node in mapping.items():
            msg = node.get("message")
            if not msg:
                continue
            if msg.get("author", {}).get("role") != "assistant":
                continue

            content_obj = msg.get("content", {})
            parts = content_obj.get("parts", [])
            text = " ".join(str(p) for p in parts if isinstance(p, str)).strip()

            if len(text) < min_length:
                continue

            ts = msg.get("create_time")
            event_time = None
            if ts:
                try:
                    event_time = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                except Exception:
                    pass

            messages.append({
                "content": text,
                "event_time": event_time,
                "source_title": title,
                "source_format": "chatgpt",
                "importance": score_importance(text),
                "category": categorize(text),
            })

    return messages


# ── Ingesta a SOUL ───────────────────────────────────────────────────

async def check_duplicate(conn, agent: str, text: str) -> bool:
    """True si ya existe una memoria con el mismo hash de contenido."""
    h = content_hash(text)
    row = await conn.fetchval("""
        SELECT id FROM memories
        WHERE agent = $1
          AND metadata::jsonb ->> 'import_hash' = $2
          AND invalid_at IS NULL
        LIMIT 1
    """, agent, h)
    return row is not None


async def ingest_messages(
    messages: list[dict],
    agent: str,
    dry_run: bool,
    limit: int | None,
) -> dict:
    stats = {"inserted": 0, "skipped_duplicate": 0, "skipped_dry": 0, "total": len(messages)}

    if limit:
        messages = messages[:limit]
        stats["total"] = len(messages)

    if dry_run:
        print(f"\n[DRY RUN] {len(messages)} mensajes serían importados para {agent}:")
        for i, m in enumerate(messages[:10], 1):
            print(f"  {i}. [{m['category']}|imp={m['importance']}] {m['content'][:80]}...")
        if len(messages) > 10:
            print(f"  ... y {len(messages) - 10} más")
        stats["skipped_dry"] = len(messages)
        return stats

    conn = await asyncpg.connect(DB_URL)

    for m in messages:
        # Check duplicado
        if await check_duplicate(conn, agent, m["content"]):
            stats["skipped_duplicate"] += 1
            continue

        h = content_hash(m["content"])
        metadata = json.dumps({
            "import_hash": h,
            "source_title": m["source_title"],
            "source_format": m["source_format"],
            "imported_at": datetime.now(LIMA_TZ).isoformat(),
        })

        await conn.execute("""
            INSERT INTO memories
              (agent, category, content, importance, source, metadata, created_at, event_time)
            VALUES ($1, $2, $3, $4, 'import', $5, NOW(),
                    CASE WHEN $6::text IS NULL THEN NOW()
                         ELSE $6::timestamptz END)
        """,
            agent,
            m["category"],
            m["content"],
            m["importance"],
            metadata,
            m.get("event_time"),
        )
        stats["inserted"] += 1

    await conn.close()
    return stats


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Importar conversaciones Claude/ChatGPT a SOUL",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python3 soul_import.py claude ~/Downloads/conversations.json --agent ADA --dry-run
  python3 soul_import.py chatgpt ~/Downloads/conversations.json --agent JARVIS --limit 100
  python3 soul_import.py claude conversations.json --agent ADA --min-length 200
        """
    )
    parser.add_argument("format", choices=["claude", "chatgpt"],
                        help="Formato del export")
    parser.add_argument("file", type=Path,
                        help="Path al archivo conversations.json")
    parser.add_argument("--agent", default="ADA",
                        help="Agente destino (ADA, JARVIS, DUM). Default: ADA")
    parser.add_argument("--dry-run", action="store_true",
                        help="Mostrar qué se importaría sin insertar nada")
    parser.add_argument("--limit", type=int, default=None,
                        help="Máximo de mensajes a importar")
    parser.add_argument("--min-length", type=int, default=150,
                        help="Longitud mínima del mensaje en caracteres. Default: 150")

    args = parser.parse_args()

    if not args.file.exists():
        print(f"❌ Archivo no encontrado: {args.file}", file=sys.stderr)
        sys.exit(1)

    print(f"Cargando {args.file}...")
    with open(args.file) as f:
        data = json.load(f)

    print(f"Parseando formato '{args.format}'...")
    if args.format == "claude":
        messages = parse_claude_export(data, args.min_length)
    else:
        messages = parse_chatgpt_export(data, args.min_length)

    print(f"  → {len(messages)} mensajes del asistente (min_length={args.min_length})")

    if not messages:
        print("⚠️  Sin mensajes para importar.")
        sys.exit(0)

    # Distribución por importancia
    imp_dist = {}
    for m in messages:
        imp_dist[m["importance"]] = imp_dist.get(m["importance"], 0) + 1
    print(f"  → Distribución importancia: {dict(sorted(imp_dist.items()))}")

    # Distribución por categoría
    cat_dist = {}
    for m in messages:
        cat_dist[m["category"]] = cat_dist.get(m["category"], 0) + 1
    print(f"  → Categorías: {cat_dist}")

    stats = asyncio.run(ingest_messages(messages, args.agent, args.dry_run, args.limit))

    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}Resultado:")
    print(f"  Total procesados:    {stats['total']}")
    print(f"  Insertados:          {stats['inserted']}")
    print(f"  Duplicados saltados: {stats['skipped_duplicate']}")
    if args.dry_run:
        print(f"  (dry-run, nada insertado)")

    if not args.dry_run and stats["inserted"] > 0:
        print(f"\n✅ {stats['inserted']} memorias importadas para {args.agent}")
    elif not args.dry_run:
        print(f"\n⚠️  Sin memorias nuevas — todo ya existe o fue filtrado")


if __name__ == "__main__":
    main()
