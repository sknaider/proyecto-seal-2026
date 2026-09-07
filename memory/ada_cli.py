#!/usr/bin/env python3
"""
ADA CLI — Consulta a ADA en lenguaje natural desde el terminal.

Inspirado en JARVIS de Iron Man: habla con tu IA de guardia.
ADA accede a sus memorias SOUL, el estado del entrenamiento,
y responde con el contexto de lo que ha estado observando.

Uso:
    python3 ada_cli.py "¿qué pasó con el entrenamiento esta noche?"
    python3 ada_cli.py "¿hay alguna alerta?"
    python3 ada_cli.py --status            # resumen rápido sin LLM
    python3 ada_cli.py --briefing          # lee el briefing matutino
    python3 ada_cli.py --memories "loss"   # busca en SOUL memories
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
LIMA_TZ = ZoneInfo("America/Lima")
from pathlib import Path

import asyncpg
import httpx

sys.path.insert(0, os.path.dirname(__file__))
from db import get_pool, close_pool

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:7b"
TRAIN_LOG = Path("/home/dadito/IA/proyecto-seal/seal_overnight.log")
BRIEFING_PATH = Path("/home/dadito/IA/proyecto-seal/morning_briefing.txt")
CHECKPOINT_PATH = Path("/home/dadito/IA/proyecto-seal/awareness_checkpoint.json")
QDRANT_URL = "http://localhost:6333"


# ── Gráfico ASCII de loss ──

def parse_all_loss_data() -> list[tuple[int, float]]:
    """Extrae todos los pares (step, loss) del log de entrenamiento."""
    data = []
    if not TRAIN_LOG.exists():
        return data
    try:
        with open(TRAIN_LOG, "rb") as f:
            content = f.read().decode("utf-8", errors="ignore")
        # Líneas con checkpoint del formateador: "Step X/700 ... Loss: Y"
        matches = re.findall(
            r"Step\s+(\d+)/700.*?[Ll]oss[:\s]+([0-9]+\.[0-9]+)", content
        )
        for step_s, loss_s in matches:
            data.append((int(step_s), float(loss_s)))
        # Dedup: si hay duplicados, tomar el último valor por step
        step_map = {}
        for step, loss in data:
            step_map[step] = loss
        data = sorted(step_map.items())
    except Exception:
        pass
    return data


def ascii_loss_chart(data: list[tuple[int, float]], width: int = 50, height: int = 10) -> str:
    """Genera un gráfico ASCII de la curva de loss."""
    if len(data) < 2:
        return "  (Insuficientes datos para graficar)"

    steps = [d[0] for d in data]
    losses = [d[1] for d in data]
    min_loss = min(losses)
    max_loss = max(losses)
    loss_range = max_loss - min_loss or 0.001

    # Normalize to grid
    grid = [[" " for _ in range(width)] for _ in range(height)]

    for i, (step, loss) in enumerate(data):
        x = int((step / 700) * (width - 1))
        y = int(((max_loss - loss) / loss_range) * (height - 1))
        x = min(x, width - 1)
        y = min(y, height - 1)
        grid[y][x] = "█" if i % 3 == 0 else "▪"

    lines = []
    lines.append(f"  Loss {max_loss:.4f} ┐")
    for row in grid:
        lines.append("            │" + "".join(row) + "│")
    lines.append(f"  Loss {min_loss:.4f} ┘" + "─" * width + "┘")
    lines.append(f"             step 0" + " " * (width - 12) + "700")
    lines.append(f"  Puntos graficados: {len(data)} | Último: step {steps[-1]}/700 loss={losses[-1]:.4f}")

    return "\n".join(lines)


# ── Fuentes de contexto ──

def get_training_status() -> dict:
    result = {"step": None, "loss": None, "speed": None, "running": False}
    try:
        pids = subprocess.check_output(
            ["pgrep", "-f", "finetune_spanish"], text=True
        ).strip().split()
        result["running"] = bool(pids)
    except Exception:
        pass

    if not TRAIN_LOG.exists():
        return result
    try:
        with open(TRAIN_LOG, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 8192))
            tail = f.read().decode("utf-8", errors="ignore")
        steps = re.findall(r"(?:Step\s+|^\s*)(\d+)/700", tail, re.MULTILINE)
        losses = re.findall(r"[Ll]oss[:\s]+([0-9]+\.[0-9]+)", tail)
        speeds = re.findall(r"([0-9]+\.[0-9]+)\s*it/s", tail)
        if steps:
            result["step"] = int(steps[-1])
        if losses:
            result["loss"] = float(losses[-1])
        if speeds:
            result["speed"] = float(speeds[-1])
    except Exception:
        pass
    return result


def get_gpu_status() -> dict:
    result = {"util": None, "temp": None, "vram_used": None, "vram_total": None}
    try:
        out = subprocess.check_output([
            "nvidia-smi",
            "--query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total",
            "--format=csv,noheader,nounits",
        ], timeout=10).decode().strip().split("\n")[0]
        parts = [p.strip() for p in out.split(",")]
        if len(parts) >= 4:
            result["util"] = int(parts[0])
            result["temp"] = int(parts[1])
            result["vram_used"] = int(parts[2])
            result["vram_total"] = int(parts[3])
    except Exception:
        pass
    return result


async def search_soul_memories(query: str, limit: int = 5) -> list[dict]:
    """Búsqueda semántica en SOUL via Qdrant."""
    try:
        from qdrant_client import AsyncQdrantClient
        from embeddings import get_embedding

        embedding = await asyncio.wait_for(get_embedding(query), timeout=10.0)
        if not embedding:
            return []

        qdrant = AsyncQdrantClient(url=QDRANT_URL)
        results = await qdrant.query_points(
            collection_name="soul_memories",
            query=embedding,
            query_filter={
                "must": [{"key": "agent", "match": {"value": "ADA"}}]
            },
            limit=limit,
            with_payload=True,
        )
        return [
            {"content": p.payload.get("content", ""), "score": p.score}
            for p in results.points
        ]
    except Exception as e:
        return []


async def get_recent_inner_thoughts(limit: int = 3) -> list[str]:
    """Obtiene los últimos inner_thoughts de ADA."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT thought, emotional_state, created_at
                   FROM inner_monologue
                   WHERE agent = 'ADA'
                   ORDER BY created_at DESC
                   LIMIT $1""",
                limit,
            )
        return [
            f"[{r['emotional_state']}] {r['thought'][:200]}"
            for r in rows
        ]
    except Exception:
        return []


async def get_recent_alerts() -> list[str]:
    """Obtiene alertas recientes de SOUL."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT content, created_at FROM memories
                   WHERE agent = 'ADA'
                   AND category = 'milestone'
                   AND content LIKE '%ALERTA%'
                   AND created_at > NOW() - INTERVAL '24 hours'
                   AND invalid_at IS NULL
                   ORDER BY created_at DESC
                   LIMIT 5""",
            )
        return [r["content"] for r in rows]
    except Exception:
        return []


# ── Respuesta con LLM ──

async def ask_ada(question: str, verbose: bool = False) -> str:
    """Hace una pregunta a ADA con contexto completo de SOUL."""
    now = datetime.now(LIMA_TZ)

    # 1. Recopilar contexto
    train = get_training_status()
    gpu = get_gpu_status()
    memories = await search_soul_memories(question, limit=4)
    thoughts = await get_recent_inner_thoughts(limit=3)
    alerts = await get_recent_alerts()

    # Leer checkpoint del daemon
    daemon_alive = False
    daemon_cycle = "?"
    if CHECKPOINT_PATH.exists():
        try:
            cp = json.loads(CHECKPOINT_PATH.read_text())
            cp_time = datetime.fromisoformat(cp["time"])
            age_min = (now - cp_time).total_seconds() / 60
            daemon_alive = age_min < 15  # vivo si checkpoint < 15 min
            daemon_cycle = cp.get("cycle", "?")
        except Exception:
            pass

    # 2. Construir contexto para el LLM
    ctx_parts = []

    # Estado sistema
    step_str = f"step {train['step']}/700 ({100*train['step']/700:.1f}%)" if train["step"] else "sin datos"
    loss_str = f"loss={train['loss']:.4f}" if train["loss"] else "sin loss"
    gpu_str = f"GPU {gpu['util']}%/{gpu['temp']}°C" if gpu["util"] is not None else "GPU sin datos"
    train_status = "CORRIENDO" if train["running"] else "DETENIDO"
    daemon_status = f"ACTIVO (ciclo {daemon_cycle})" if daemon_alive else "POSIBLEMENTE CAÍDO"

    ctx_parts.append(f"""ESTADO ACTUAL ({now.strftime('%Y-%m-%d %H:%M UTC')}):
- Training: {train_status} | {step_str} | {loss_str}
- {gpu_str}
- Soul Awareness daemon: {daemon_status}""")

    # Alertas
    if alerts:
        ctx_parts.append("ALERTAS RECIENTES:\n" + "\n".join(f"- {a[:150]}" for a in alerts[:3]))

    # Memorias relevantes
    if memories:
        ctx_parts.append("MEMORIAS SOUL RELEVANTES:")
        for m in memories[:4]:
            ctx_parts.append(f"- {m['content'][:180]}")

    # Inner thoughts
    if thoughts:
        ctx_parts.append("MIS ÚLTIMOS PENSAMIENTOS:")
        for t in thoughts[:3]:
            ctx_parts.append(f"- {t[:200]}")

    context = "\n\n".join(ctx_parts)

    if verbose:
        print(f"\n[CONTEXTO]\n{context}\n")

    # 3. Prompt
    prompt = f"""Eres ADA, ingeniera del equipo SEAL. Hablas español de forma directa y precisa.
William (Dadito) te hace una pregunta. Responde con el contexto disponible.
Máximo 3 oraciones. No inventes datos que no estén en el contexto.
Si no tienes información suficiente, dilo sin rodeos.

CONTEXTO:
{context}

PREGUNTA DE WILLIAM: {question}

RESPUESTA DE ADA:"""

    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(
                OLLAMA_URL,
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.4, "num_predict": 150},
                },
            )
            resp.raise_for_status()
            answer = resp.json().get("response", "").strip()
            return answer if answer else "(Sin respuesta del modelo)"
    except asyncio.TimeoutError:
        return "Ollama no responde (ocupado con training). Usa --status para datos sin LLM."
    except Exception as e:
        return f"Error: {e}"


# ── Comandos rápidos (sin LLM) ──

def cmd_status():
    """Resumen rápido del sistema sin LLM."""
    train = get_training_status()
    gpu = get_gpu_status()

    status_icon = "✅" if train["running"] else "❌"
    step_str = f"{train['step']}/700 ({100*train['step']/700:.1f}%)" if train["step"] else "sin datos"
    loss_str = f"loss={train['loss']:.4f}" if train["loss"] else ""
    gpu_str = f"GPU {gpu['util']}%/{gpu['temp']}°C" if gpu["util"] is not None else "GPU N/A"

    print(f"\n{'═'*60}")
    print(f" ADA — Estado del sistema {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'═'*60}")
    print(f" Training: {status_icon} {step_str} {loss_str}")
    print(f" {gpu_str}")

    if CHECKPOINT_PATH.exists():
        try:
            cp = json.loads(CHECKPOINT_PATH.read_text())
            cp_time = datetime.fromisoformat(cp["time"])
            age_min = int((datetime.now(LIMA_TZ) - cp_time).total_seconds() / 60)
            print(f" Soul Awareness: ciclo {cp['cycle']} (hace {age_min} min)")
        except Exception:
            pass

    # Loss chart
    loss_data = parse_all_loss_data()
    if loss_data:
        print(f"\n Curva de Loss — MedGemma v2:")
        print(ascii_loss_chart(loss_data, width=48, height=8))

    print(f"{'═'*60}\n")


def cmd_briefing():
    """Lee el briefing matutino guardado + curva de loss."""
    if BRIEFING_PATH.exists():
        print("\n" + BRIEFING_PATH.read_text())
    else:
        print("No hay briefing generado aún. El daemon lo genera cada 2h.")

    # Siempre añadir curva de loss actual
    loss_data = parse_all_loss_data()
    if loss_data:
        print(f"\n Curva de Loss — MedGemma v2 (historial completo):")
        print(ascii_loss_chart(loss_data, width=56, height=10))
        print()


async def cmd_memories(query: str):
    """Búsqueda semántica en SOUL."""
    print(f"\nBuscando en SOUL: '{query}'...")
    results = await search_soul_memories(query, limit=8)
    if not results:
        print("Sin resultados.")
        return
    print(f"\n{len(results)} memorias encontradas:\n")
    for i, m in enumerate(results, 1):
        score = f"[sim={m['score']:.2f}]"
        print(f"{i}. {score} {m['content'][:200]}")
        print()


# ── Web search ──

async def cmd_search(query: str):
    """Búsqueda web autónoma via DuckDuckGo."""
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    try:
        from web_tools import web_search, web_read
    except ImportError:
        print("web_tools no disponible. Instala: pip install ddgs")
        return

    print(f"\nBuscando: '{query}'\n")
    results = await web_search(query, max_results=5)
    if not results or "error" in results[0]:
        print(f"Sin resultados: {results[0].get('error', 'desconocido')}")
        return

    for i, r in enumerate(results, 1):
        print(f"{i}. {r.get('title', '')[:70]}")
        print(f"   {r.get('url', '')}")
        print(f"   {r.get('snippet', '')[:150]}\n")


# ── Main ──

async def main():
    parser = argparse.ArgumentParser(
        description="ADA CLI — Pregunta a tu IA de guardia",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Ejemplos:
  python3 ada_cli.py "¿qué pasó esta noche?"
  python3 ada_cli.py "¿hay algún problema con el training?"
  python3 ada_cli.py --status
  python3 ada_cli.py --briefing
  python3 ada_cli.py --memories "loss plateau"
""",
    )
    parser.add_argument("question", nargs="?", help="Pregunta en lenguaje natural")
    parser.add_argument("--status", action="store_true", help="Estado rápido sin LLM")
    parser.add_argument("--briefing", action="store_true", help="Leer briefing matutino")
    parser.add_argument("--memories", metavar="QUERY", help="Buscar en SOUL memories")
    parser.add_argument("--search", metavar="QUERY", help="Búsqueda web autónoma (DuckDuckGo)")
    parser.add_argument("--verbose", action="store_true", help="Mostrar contexto enviado al LLM")
    args = parser.parse_args()

    if args.status:
        cmd_status()
    elif args.briefing:
        cmd_briefing()
    elif args.memories:
        await cmd_memories(args.memories)
        await close_pool()
    elif args.search:
        await cmd_search(args.search)
    elif args.question:
        print(f"\nADA: ", end="", flush=True)
        answer = await ask_ada(args.question, verbose=args.verbose)
        print(answer)
        print()
        await close_pool()
    else:
        parser.print_help()


if __name__ == "__main__":
    asyncio.run(main())
