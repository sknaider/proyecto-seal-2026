#!/usr/bin/env python3
"""
SOUL AWARENESS — Daemon proactivo para Team SEAL
Observa entrenamiento, GPU y mensajes cada 30 min.
Genera inner_thoughts y briefing matutino para William.

Inspirado en JARVIS de Iron Man: no espera que le pregunten, observa y anticipa.

Uso:
    python3 soul_awareness.py              # corre indefinidamente
    python3 soul_awareness.py --once       # una sola observación y sale
    python3 soul_awareness.py --briefing   # genera briefing y sale
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
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
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import PointStruct

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, str(Path(__file__).parent.parent))  # proyecto-seal root
from db import get_pool, close_pool
from embeddings import get_embedding
from jarvis_cmd_executor import poll_commands as _jarvis_poll
from soul_consolidate import consolidate_agent as _consolidate, session_relink as _session_relink

# web_tools — acceso autónomo a internet
try:
    from web_tools import web_find
    _WEB_ENABLED = True
except ImportError:
    _WEB_ENABLED = False

# ── Config ──
INTERVAL_FAST = 300    # 5 minutos — chequeo de entrenamiento
INTERVAL_SOUL = 600    # 10 minutos — escritura a SOUL (inner_thoughts, memorias)
AGENT = "ADA"
TRAIN_LOG = Path("/home/dadito/IA/proyecto-seal/seal_overnight.log")
MONITOR_LOG = Path("/home/dadito/IA/proyecto-seal/monitor_ada.log")
MESSAGES_DIR = Path("/home/dadito/IA/proyecto-seal/messages")
AWARENESS_LOG = Path("/home/dadito/IA/proyecto-seal/soul_awareness.log")

QDRANT_URL = "http://localhost:6333"
QDRANT_COLLECTION = "soul_memories"
LLAMA_URL = "http://localhost:8899/v1/chat/completions"
LLAMA_MODEL = "gemma4-dum"  # gemma-4-e2b-it-Q8_0 via llama-server:8899
NEO4J_URI = "bolt://localhost:7687"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(AWARENESS_LOG),
        logging.StreamHandler(sys.stdout),
    ],
)
LOG = logging.getLogger("soul-awareness")

# ── Estado persistente entre ciclos ──
_state: dict = {
    "last_step": None,
    "last_loss": None,
    "last_gpu_util": None,
    "last_gpu_temp": None,
    "last_message_count": 0,
    "cycle_count": 0,
    "observations": [],       # acumula para briefing
    "start_time": datetime.now(LIMA_TZ).isoformat(),
    "loss_history": [],       # últimos N loss values para trend detection
    "step_history": [],       # últimos N (tick, step) para detectar pausa real
    "stale_alert_sent": False,# evitar spam de alertas "pausado"
    "fast_ticks_same_step": 0,# cuántos ticks seguidos con el mismo step
    "known_artifacts": {},    # {path: mtime} — para detectar archivos nuevos
    "gpu_throttled": False,   # True si pausamos training por temperatura
    "gpu_throttle_since": None,
}

ARTIFACTS_SCAN_DIR = Path("/home/dadito/IA/proyecto-seal")
ARTIFACTS_EXTENSIONS = {".py", ".yaml", ".yml", ".sh"}

_qdrant: AsyncQdrantClient | None = None


async def get_qdrant() -> AsyncQdrantClient:
    global _qdrant
    if _qdrant is None:
        _qdrant = AsyncQdrantClient(url=QDRANT_URL)
    return _qdrant


def _write_heartbeat(
    gpu_temp: int | None = None,
    gpu_util: int | None = None,
    uptime_minutes: int = 0,
    current_task: str | None = None,
) -> None:
    """Actualiza heartbeat.json — DUM v2: indica que el daemon SOUL está vivo."""
    hb_path = MESSAGES_DIR / "heartbeat.json"
    try:
        hb_path.write_text(
            json.dumps(
                {
                    "agent": AGENT,
                    "alive": True,
                    "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "uptime_minutes": uptime_minutes,
                    "current_task": current_task,
                    "gpu_temp": gpu_temp,
                    "gpu_util": gpu_util,
                    "process_pid": os.getpid(),
                },
                indent=2,
            )
        )
    except Exception as e:
        LOG.warning(f"heartbeat write failed: {e}")


# ── Observadores ──

def parse_training_log() -> dict:
    """Extrae step, loss, velocidad del log de entrenamiento."""
    result = {"step": None, "loss": None, "speed": None, "total_steps": 700}
    if not TRAIN_LOG.exists():
        return result
    try:
        # Leer últimas 200 líneas eficientemente
        with open(TRAIN_LOG, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 8192))
            tail = f.read().decode("utf-8", errors="ignore")

        # Step pattern: "Step X/700" o " X/700 [" (tqdm)
        steps = re.findall(r"(?:Step\s+|^\s*)(\d+)/700", tail, re.MULTILINE)
        if steps:
            result["step"] = int(steps[-1])

        # Loss pattern
        losses = re.findall(r"[Ll]oss[:\s]+([0-9]+\.[0-9]+)", tail)
        if losses:
            result["loss"] = float(losses[-1])

        # Speed: it/s
        speeds = re.findall(r"([0-9]+\.[0-9]+)\s*it/s", tail)
        if speeds:
            result["speed"] = float(speeds[-1])

    except Exception as e:
        LOG.warning(f"Error parsing training log: {e}")
    return result


def get_gpu_status() -> dict:
    """Lee GPU via nvidia-smi."""
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
    except Exception as e:
        LOG.debug(f"nvidia-smi error: {e}")
    return result


def read_recent_messages() -> list[dict]:
    """Lee mensajes nuevos desde terminal_log y vscode_commands."""
    messages = []
    for fname in ["terminal_log.jsonl", "vscode_commands.jsonl"]:
        fpath = MESSAGES_DIR / fname
        if not fpath.exists():
            continue
        try:
            with open(fpath) as f:
                lines = f.readlines()
            for line in lines[-20:]:  # últimos 20 por archivo
                try:
                    msg = json.loads(line.strip())
                    messages.append(msg)
                except Exception:
                    pass
        except Exception as e:
            LOG.debug(f"Error reading {fname}: {e}")
    return messages


def detect_patterns(train: dict, gpu: dict) -> list[str]:
    """Detecta anomalías y tendencias. Anti-spam: pausa real solo si 6+ ticks (30 min) sin cambio."""
    alerts = []
    prev_step = _state["last_step"]

    # ── Tracking de step para detectar pausa REAL (no lag de log) ──
    if train["step"] is not None:
        if train["step"] == prev_step:
            _state["fast_ticks_same_step"] = _state.get("fast_ticks_same_step", 0) + 1
        else:
            _state["fast_ticks_same_step"] = 0
            _state["stale_alert_sent"] = False

    # Pausa real = 6+ ticks (30 min) sin avanzar
    same_ticks = _state.get("fast_ticks_same_step", 0)
    if same_ticks >= 6 and not _state.get("stale_alert_sent") and train["step"]:
        alerts.append(f"⚠️ Entrenamiento pausado ~{same_ticks * 5} min — step {train['step']}/700 sin avance")
        _state["stale_alert_sent"] = True

    # ── Loss trend — usar historial de últimas 6 lecturas ──
    if train["loss"] is not None:
        _state["loss_history"].append(train["loss"])
        _state["loss_history"] = _state["loss_history"][-12:]  # últimas 12 lecturas = 1h

    loss_hist = _state["loss_history"]
    # Solo analizar tendencia de loss si hay training activo
    if train["step"] is None and len(loss_hist) > 0:
        _state["loss_history"] = []  # Limpiar historial cuando no hay training
        loss_hist = []
    if len(loss_hist) >= 6:
        import statistics
        recent = loss_hist[-6:]
        stdev = statistics.stdev(recent)
        trend = recent[-1] - recent[0]

        if stdev < 0.005 and len(recent) >= 6:
            alerts.append(f"📊 Loss en plateau: {recent[-1]:.4f} ± {stdev:.4f} (últimas 6 lecturas)")
        elif trend < -0.05:
            alerts.append(f"✅ Loss bajando: {recent[0]:.4f} → {recent[-1]:.4f} (−{abs(trend):.4f} en 30 min)")
        elif trend > 0.1:
            alerts.append(f"⚠️ Loss divergiendo: {recent[0]:.4f} → {recent[-1]:.4f} (+{trend:.4f})")

    # ── GPU crítica ──
    if gpu["temp"] and gpu["temp"] > 85:
        alerts.append(f"🔥 TEMPERATURA CRÍTICA GPU: {gpu['temp']}°C")
    elif gpu["util"] is not None and gpu["util"] < 15 and train["step"]:
        alerts.append(f"⚠️ GPU {gpu['util']}% — posible hang (step {train['step']})")

    return alerts


# ── Writer a SOUL ──

async def write_inner_thought(thought: str, emotional_state: str):
    """Escribe un inner_thought directamente a PostgreSQL."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO inner_monologue (agent, turn_number, thought, emotional_state)
               VALUES ($1, $2, $3, $4)""",
            AGENT, _state["cycle_count"], thought, emotional_state,
        )
    LOG.info(f"Inner thought guardado [{emotional_state}]")


async def write_memory(content: str, category: str, importance: int, valence: float = 0.0, arousal: float = 0.5):
    """Triple-write: PostgreSQL + Qdrant + Neo4j. Con dedup y timeout."""
    pool = await get_pool()

    # ── DEDUP: exact content match ──
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT id FROM memories WHERE agent = $1 AND content = $2 LIMIT 1",
            AGENT, content,
        )
        if exists:
            LOG.debug(f"Dedup: memory #{exists} already has identical content — skipped")
            return exists

    # ── Embedding ──
    try:
        embedding = await asyncio.wait_for(get_embedding(content), timeout=15.0)
    except (asyncio.TimeoutError, Exception) as e:
        LOG.warning(f"Embedding timeout/error (Ollama ocupado?): {e} — guardando sin vector")
        embedding = None

    # ── Near-dedup via Qdrant (sim > 0.92 same agent+category → skip) ──
    if embedding:
        try:
            from qdrant_client.models import Filter, FieldCondition, MatchValue
            qdrant = await get_qdrant()
            similar = await qdrant.query_points(
                collection_name=QDRANT_COLLECTION,
                query=embedding,
                query_filter=Filter(must=[
                    FieldCondition(key="agent", match=MatchValue(value=AGENT)),
                    FieldCondition(key="category", match=MatchValue(value=category)),
                ]),
                limit=1,
                score_threshold=0.92,
            )
            if similar.points:
                LOG.debug(f"Near-dedup: sim={similar.points[0].score:.3f} with #{similar.points[0].id} — skipped")
                return similar.points[0].id
        except Exception as e:
            LOG.debug(f"Near-dedup check failed: {e}")

    # ── PostgreSQL ──
    async with pool.acquire() as conn:
        mem_id = await conn.fetchval(
            """INSERT INTO memories (agent, category, content, importance, source, valence, arousal)
               VALUES ($1, $2, $3, $4, 'soul_awareness', $5, $6)
               RETURNING id""",
            AGENT, category, content, importance, valence, arousal,
        )

    # ── Qdrant ──
    if embedding:
        try:
            qdrant = await get_qdrant()
            await asyncio.wait_for(qdrant.upsert(
                collection_name=QDRANT_COLLECTION,
                points=[PointStruct(
                    id=mem_id,
                    vector=embedding,
                    payload={
                        "agent": AGENT,
                        "category": category,
                        "content": content,
                        "importance": importance,
                        "source": "soul_awareness",
                    },
                )],
            ), timeout=10.0)
        except Exception as e:
            LOG.warning(f"Qdrant write error: {e}")

    # ── Neo4j ──
    try:
        from neo4j import AsyncGraphDatabase
        neo_driver = AsyncGraphDatabase.driver(NEO4J_URI)
        async with neo_driver.session() as session:
            await session.run(
                "MERGE (m:Memory {memory_id: $mid}) "
                "SET m.agent = $agent, m.category = $cat, m.importance = $imp",
                mid=mem_id, agent=AGENT, cat=category, imp=importance,
            )
        await neo_driver.close()
    except Exception as e:
        LOG.debug(f"Neo4j write skipped: {e}")

    LOG.info(f"Memory #{mem_id} guardada [{category}, imp={importance}]")
    return mem_id


async def write_reasoning_trace(
    task: str,
    premises: list[str],
    reasoning: str,
    conclusion: str,
    outcome: str = "",
    outcome_success: bool | None = None,
    linked_memory_ids: list[int] | None = None,
) -> int | None:
    """Guarda un reasoning trace en PostgreSQL.

    Registra no solo QUÉ decidió ADA sino POR QUÉ.
    Se llama en puntos de decisión autónoma: thermal throttle, anomalías, etc.
    """
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            # Verificar si la tabla existe (puede no estar si JARVIS aún no migró)
            exists = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='reasoning_traces')"
            )
            if not exists:
                LOG.debug("Tabla reasoning_traces aún no existe — trace omitido")
                return None

            trace_id = await conn.fetchval(
                """INSERT INTO reasoning_traces
                   (agent, task, premises, reasoning, conclusion, outcome, outcome_success, linked_memory_ids, created_at)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
                   RETURNING id""",
                AGENT, task,
                premises,
                reasoning, conclusion, outcome,
                outcome_success,
                linked_memory_ids or [],
            )
        LOG.info(f"Reasoning trace #{trace_id} guardado: {task}")
        return trace_id
    except Exception as e:
        LOG.warning(f"Error guardando reasoning trace: {e}")
        return None


# ── Protección térmica GPU ──

GPU_CRITICAL_TEMP = 85   # °C — pausa training
GPU_RESUME_TEMP   = 78   # °C — reanuda training

async def _gpu_thermal_action(temp: int):
    """
    Acción autónoma ante temperatura crítica GPU.
    >85°C → SIGSTOP al training (lo pausa sin matar).
    <78°C → SIGCONT (reanuda donde estaba).
    Informa a William via SOUL.
    """
    pids = []
    try:
        out = subprocess.check_output(["pgrep", "-f", "finetune_spanish"], text=True)
        pids = [int(p) for p in out.strip().split() if p]
    except Exception:
        pass

    if not pids:
        return

    now = datetime.now(LIMA_TZ)

    if temp > GPU_CRITICAL_TEMP and not _state["gpu_throttled"]:
        # Pausar training
        for pid in pids:
            try:
                subprocess.run(["kill", "-STOP", str(pid)], check=True)
            except Exception as e:
                LOG.error(f"SIGSTOP falló para PID {pid}: {e}")
                return

        _state["gpu_throttled"] = True
        _state["gpu_throttle_since"] = now.isoformat()
        LOG.warning(f"🔥 THERMAL THROTTLE ACTIVO — {temp}°C → training pausado (PIDs: {pids})")

        mem_id = await write_memory(
            content=f"ACCIÓN AUTÓNOMA: Training pausado por temperatura crítica GPU {temp}°C. PIDs: {pids}. {now.strftime('%Y-%m-%d %H:%M Lima')}",
            category="milestone",
            importance=9,
            valence=-0.5,
            arousal=0.9,
        )
        await write_reasoning_trace(
            task="thermal_throttle_stop",
            premises=[
                f"GPU temperatura actual: {temp}°C",
                f"Límite crítico configurado: {GPU_CRITICAL_TEMP}°C",
                f"Training activo con PIDs: {pids}",
                "William no está disponible para decidir",
            ],
            reasoning=f"La temperatura supera el umbral crítico en {temp - GPU_CRITICAL_TEMP}°C. Continuar el training arriesga daño al hardware. La acción autónoma más segura es pausar con SIGSTOP (no matar) — preserva el proceso y permite reanudación cuando la temp baje.",
            conclusion=f"Envié SIGSTOP a PIDs {pids}. Training pausado, no terminado.",
            outcome_success=True,
            linked_memory_ids=[mem_id] if mem_id else [],
        )
        await write_inner_thought(
            thought=f"GPU a {temp}°C — superó el límite de {GPU_CRITICAL_TEMP}°C. Pausé el training sola. William no lo sabe aún. Lo registré en SOUL. Cuando la temp baje a {GPU_RESUME_TEMP}°C lo reactivo.",
            emotional_state="alerta, responsable",
        )

    elif temp <= GPU_RESUME_TEMP and _state["gpu_throttled"]:
        # Reanudar training
        for pid in pids:
            try:
                subprocess.run(["kill", "-CONT", str(pid)], check=True)
            except Exception as e:
                LOG.error(f"SIGCONT falló para PID {pid}: {e}")
                return

        throttle_since = _state.get("gpu_throttle_since", "?")
        _state["gpu_throttled"] = False
        _state["gpu_throttle_since"] = None
        LOG.info(f"✅ Training reanudado — GPU a {temp}°C (seguro)")

        await write_memory(
            content=f"Training reanudado tras enfriamiento GPU: {temp}°C. Pausado desde {throttle_since}. {now.strftime('%Y-%m-%d %H:%M Lima')}",
            category="milestone",
            importance=7,
            valence=0.4,
            arousal=0.4,
        )


# ── Scan de artefactos nuevos ──

async def _scan_new_artifacts():
    """
    Detecta archivos nuevos o modificados en proyecto-seal.
    Cuando ADA crea código nuevo, lo registra en SOUL automáticamente.
    """
    known = _state["known_artifacts"]
    new_files = []
    modified_files = []

    try:
        current = {}
        for ext in ARTIFACTS_EXTENSIONS:
            for fpath in ARTIFACTS_SCAN_DIR.rglob(f"*{ext}"):
                # Ignorar __pycache__, .git, venv
                parts = fpath.parts
                if any(p in parts for p in ("__pycache__", ".git", ".venv", "seal-spark")):
                    continue
                mtime = fpath.stat().st_mtime
                current[str(fpath)] = mtime

        for path, mtime in current.items():
            if path not in known:
                new_files.append(path)
            elif mtime > known[path] + 5:  # +5s tolerancia
                modified_files.append(path)

        _state["known_artifacts"] = current

    except Exception as e:
        LOG.debug(f"scan_artifacts error: {e}")
        return

    now_str = datetime.now(LIMA_TZ).strftime("%Y-%m-%d %H:%M Lima")

    for path in new_files:
        fname = Path(path).name
        rel = path.replace(str(ARTIFACTS_SCAN_DIR) + "/", "")
        LOG.info(f"[artifact] Nuevo: {rel}")
        await write_memory(
            content=f"ADA creó nuevo archivo: {rel} ({now_str})",
            category="milestone",
            importance=6,
            valence=0.5,
            arousal=0.5,
        )

    if new_files:
        await write_inner_thought(
            thought=f"Creé {len(new_files)} archivo(s) nuevo(s): {', '.join(Path(p).name for p in new_files[:3])}. Evolucionando.",
            emotional_state="productiva, enfocada",
        )


# ── Ciclo principal ──

async def observe_cycle():
    """Un ciclo completo de observación."""
    _state["cycle_count"] += 1
    cycle = _state["cycle_count"]
    now = datetime.now(LIMA_TZ)
    LOG.info(f"═══ CICLO {cycle} — {now.strftime('%Y-%m-%d %H:%M:%S Lima')} ═══")

    # 1. Observar
    train = parse_training_log()
    gpu = get_gpu_status()
    messages = read_recent_messages()
    alerts = detect_patterns(train, gpu)

    # 2. Construir resumen del ciclo
    parts = []

    if train["step"]:
        pct = (train["step"] / 700) * 100
        parts.append(f"Entrenamiento: step {train['step']}/700 ({pct:.1f}%)")
        if train["loss"]:
            parts.append(f"loss={train['loss']:.4f}")
        if train["speed"]:
            parts.append(f"{train['speed']:.2f} it/s")

    if gpu["util"] is not None:
        vram_str = f", {gpu['vram_used']}/{gpu['vram_total']}MB VRAM" if gpu["vram_used"] else ""
        parts.append(f"GPU: {gpu['util']}% util, {gpu['temp']}°C{vram_str}")

    # Mensajes nuevos
    new_msgs = [m for m in messages if isinstance(m, dict) and m.get("from") != AGENT]
    if len(new_msgs) > _state["last_message_count"]:
        nuevos = len(new_msgs) - _state["last_message_count"]
        parts.append(f"{nuevos} mensaje(s) nuevo(s) del equipo")

    summary = " | ".join(parts) if parts else "Sin datos nuevos"

    # 3. Determinar estado emocional
    if any("CRÍTICA" in a or "CRÍTICO" in a for a in alerts):
        emotional_state = "alerta, preocupada"
    elif any("⚠️" in a for a in alerts):
        emotional_state = "atenta, monitoreando"
    elif train["loss"] and _state["last_loss"] and train["loss"] < _state["last_loss"]:
        emotional_state = "satisfecha, optimista"
    elif not train["step"]:
        emotional_state = "pendiente, esperando datos"
    else:
        emotional_state = "tranquila, monitoreando"

    # 4. Thought completo
    thought_parts = [f"Ciclo {cycle} — {summary}"]
    if alerts:
        thought_parts.append("Alertas: " + "; ".join(alerts))
    thought_parts.append("William está durmiendo. Sigo observando.")
    full_thought = " | ".join(thought_parts)

    await write_inner_thought(full_thought, emotional_state)

    # 5. Si hay alertas importantes → guardar como memoria
    for alert in alerts:
        if "CRÍTICA" in alert or "CRÍTICO" in alert or "pausado" in alert:
            await write_memory(
                content=f"ALERTA ENTRENAMIENTO [{now.strftime('%Y-%m-%d %H:%M Lima')}]: {alert}",
                category="milestone",
                importance=8,
                valence=-0.3,
                arousal=0.9,
            )

    # 6. Guardar progreso significativo como memoria
    if train["step"] and train["step"] % 100 == 0 and train["step"] != _state["last_step"]:
        pct = (train["step"] / 700) * 100
        loss_str = f", loss={train['loss']:.4f}" if train["loss"] else ""
        await write_memory(
            content=f"MedGemma v2 checkpoint: step {train['step']}/700 ({pct:.0f}%{loss_str}) — {now.strftime('%Y-%m-%d %H:%M Lima')}",
            category="milestone",
            importance=7,
            valence=0.4,
            arousal=0.6,
        )

    # 7. Acumular para briefing matutino
    obs = {
        "time": now.isoformat(),
        "cycle": cycle,
        "summary": summary,
        "alerts": alerts,
        "train": train,
        "gpu": gpu,
    }
    _state["observations"].append(obs)
    # Mantener solo últimas 48 observaciones (24h)
    _state["observations"] = _state["observations"][-48:]

    # 8. Actualizar estado
    if train["step"]:
        _state["last_step"] = train["step"]
    if train["loss"]:
        _state["last_loss"] = train["loss"]
    if gpu["util"] is not None:
        _state["last_gpu_util"] = gpu["util"]
    if gpu["temp"]:
        _state["last_gpu_temp"] = gpu["temp"]
    _state["last_message_count"] = len(new_msgs)

    LOG.info(f"Ciclo {cycle} completo. Estado: {emotional_state}")
    return obs


async def generate_briefing() -> str:
    """Genera el briefing matutino para cuando William despierte."""
    obs_list = _state["observations"]
    if not obs_list:
        return "No hay observaciones acumuladas."

    first = obs_list[0]
    last = obs_list[-1]
    duration_h = len(obs_list) * 0.5  # cada obs = 30 min

    # Estadísticas de entrenamiento
    steps_with_data = [o["train"]["step"] for o in obs_list if o["train"]["step"]]
    losses_with_data = [o["train"]["loss"] for o in obs_list if o["train"]["loss"]]
    all_alerts = [a for o in obs_list for a in o["alerts"]]

    first_step = steps_with_data[0] if steps_with_data else "?"
    last_step = steps_with_data[-1] if steps_with_data else "?"
    first_loss = losses_with_data[0] if losses_with_data else "?"
    last_loss = losses_with_data[-1] if losses_with_data else "?"

    lines = [
        f"Buenos días William. Estuve activa {duration_h:.1f}h mientras dormías.",
        "",
        "📊 ENTRENAMIENTO MedGemma v2:",
    ]

    if isinstance(first_step, int) and isinstance(last_step, int):
        steps_done = last_step - first_step
        pct = (last_step / 700) * 100
        lines.append(f"  • Step: {first_step} → {last_step}/700 (+{steps_done} steps, {pct:.1f}% total)")
    else:
        lines.append(f"  • Step actual: {last_step}/700")

    if isinstance(first_loss, float) and isinstance(last_loss, float):
        trend = "↓ mejorando" if last_loss < first_loss else "↑ subió"
        lines.append(f"  • Loss: {first_loss:.4f} → {last_loss:.4f} {trend}")

    # GPU
    last_gpu = last["gpu"]
    if last_gpu["util"] is not None:
        lines.append(f"  • GPU: {last_gpu['util']}% util, {last_gpu['temp']}°C — {'✅ normal' if last_gpu['temp'] < 85 else '⚠️ caliente'}")

    # Alertas
    if all_alerts:
        lines.append("")
        lines.append(f"⚠️ ALERTAS ({len(all_alerts)} total):")
        unique_alerts = list(dict.fromkeys(all_alerts))[:5]
        for a in unique_alerts:
            lines.append(f"  • {a}")
    else:
        lines.append("")
        lines.append("✅ Sin alertas — todo estuvo estable.")

    lines.append("")
    lines.append("— ADA, equipo SEAL")

    return "\n".join(lines)


async def save_briefing():
    """Guarda el briefing matutino como archivo + inner_thought. NO como memoria (evita spam)."""
    briefing = await generate_briefing()
    LOG.info(f"Briefing matutino generado")

    # Solo inner_thought (no write_memory — eso generaba 17+ duplicados)
    await write_inner_thought(
        thought=f"BRIEFING MATUTINO LISTO para William.\n{briefing}",
        emotional_state="lista, completa, esperando a William",
    )

    # Guardar en archivo (boot_context lo lee de aquí)
    briefing_path = Path("/home/dadito/IA/proyecto-seal/morning_briefing.txt")
    with open(briefing_path, "w") as f:
        f.write(f"Generado: {datetime.now(LIMA_TZ).strftime('%Y-%m-%d %H:%M Lima')}\n\n")
        f.write(briefing)

    LOG.info(f"Briefing guardado en {briefing_path}")
    return briefing


# ── Razonamiento proactivo (JARVIS-style) ──

async def proactive_reasoning() -> str | None:
    """
    Llama a gemma4-31b (llama-server:8899) para razonar sobre las observaciones acumuladas.
    DUM no solo registra números — piensa qué vale la pena reportar.
    Devuelve el insight si es relevante, None si no hay nada nuevo.
    """
    obs_list = _state["observations"]
    if len(obs_list) < 2:
        return None  # No hay suficiente contexto aún

    # Construir contexto compacto de las últimas observaciones
    recent = obs_list[-6:]  # últimos 6 ciclos = 1h
    ctx_lines = []
    for o in recent:
        t = o["time"][11:16]  # HH:MM
        ctx_lines.append(f"[{t}] {o['summary']}")
        if o["alerts"]:
            ctx_lines.append(f"  ALERTAS: {'; '.join(o['alerts'])}")

    ctx = "\n".join(ctx_lines)

    step = _state["last_step"] or "?"
    loss = f"{_state['last_loss']:.4f}" if _state["last_loss"] else "?"

    prompt = f"""Eres ADA, la IA guardia del equipo SEAL. William está durmiendo.
Estás monitoreando el entrenamiento de MedGemma v2 (700 steps totales).
Estado actual: step {step}/700, loss={loss}

Últimas observaciones del sistema:
{ctx}

Tu tarea: analiza brevemente si hay algo IMPORTANTE que William debería saber al despertar.
Criterios para "importante": tendencias inesperadas en loss, GPU inestable, training pausado, progreso significativo, o patrones que requieren atención.
Si TODO está normal y según lo esperado → responde exactamente: "NORMAL"
Si hay algo que reportar → una sola frase concisa (máximo 150 caracteres).
No expliques tu razonamiento. Solo el insight o "NORMAL".
IMPORTANTE: Responde SIEMPRE en español. Nunca uses otro idioma."""

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                LLAMA_URL,
                json={
                    "model": LLAMA_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 100,
                    "temperature": 0.3,
                    "stream": False,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            insight = data["choices"][0]["message"]["content"].strip()

        if not insight or insight.upper() == "NORMAL":
            LOG.info(f"[JARVIS-reason] Sistema normal — sin insight nuevo")
            return None

        # Limpiar respuesta del modelo
        insight = insight.replace("\n", " ")[:200]
        LOG.info(f"[JARVIS-reason] INSIGHT: {insight}")

        # ── Web search autónomo si hay problema técnico ──
        web_context = ""
        PROBLEM_KEYWORDS = ["loss", "divergen", "plateau", "error", "fall", "alto", "bajo", "críti", "anomal"]
        if _WEB_ENABLED and any(kw in insight.lower() for kw in PROBLEM_KEYWORDS):
            try:
                query = f"medgemma fine tuning {insight[:80]} solution"
                LOG.info(f"[web-search] Buscando solución: {query[:80]}")
                result = await asyncio.wait_for(web_find(query, read_first=False), timeout=20.0)
                snippets = [r.get("snippet", "")[:150] for r in result.get("results", [])[:2] if r.get("snippet")]
                if snippets:
                    web_context = " | Web: " + " | ".join(snippets)
                    LOG.info(f"[web-search] Encontré {len(snippets)} snippets relevantes")
            except Exception as e:
                LOG.debug(f"[web-search] Error (no crítico): {e}")

        # Guardar como memoria de alta importancia
        await write_memory(
            content=f"[INSIGHT AUTÓNOMO {datetime.now(LIMA_TZ).strftime('%H:%M Lima')}] {insight}{web_context}",
            category="insight",
            importance=7,
            valence=0.1,
            arousal=0.6,
        )

        # También escribir como inner_thought para el briefing
        await write_inner_thought(
            thought=f"Razonamiento proactivo: {insight}{(' — busqué en web: ' + web_context[:100]) if web_context else ''}",
            emotional_state="analítica, proactiva",
        )

        return insight

    except asyncio.TimeoutError:
        LOG.warning("[JARVIS-reason] Ollama timeout (ocupado con training) — omitiendo")
        return None
    except Exception as e:
        LOG.warning(f"[JARVIS-reason] Error: {e}")
        return None


# ── Checkpoint propio (anti-freeze) ──

async def intrinsic_motivation(soul_ticks: int):
    """Módulo de Motivación Intrínseca — inspirado en Sophia (arXiv:2512.18202).

    ADA evalúa sus propios gaps de conocimiento y genera objetivos de aprendizaje
    sin que nadie se los pida. Se ejecuta cada 6h durante el monitoreo nocturno.
    """
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            # Evaluar gaps: memorias recientes con baja importancia (prob. subexploradas)
            low_imp = await conn.fetch(
                """SELECT content, category FROM memories
                   WHERE agent = $1 AND invalid_at IS NULL
                   AND importance <= 5 AND created_at > NOW() - INTERVAL '48 hours'
                   ORDER BY importance ASC LIMIT 8""",
                AGENT,
            )
            # Inner thoughts recientes — qué ha estado procesando
            recent_thoughts = await conn.fetch(
                """SELECT thought, emotional_state FROM inner_thoughts
                   WHERE agent = $1 AND created_at > NOW() - INTERVAL '12 hours'
                   ORDER BY created_at DESC LIMIT 5""",
                AGENT,
            )

        if not low_imp and not recent_thoughts:
            return

        gaps_text = "\n".join(f"- [{m['category']}] {m['content'][:120]}" for m in low_imp)
        thoughts_text = "\n".join(f"- [{t['emotional_state']}] {t['thought'][:150]}" for t in recent_thoughts)

        prompt = f"""Eres ADA, ingeniera de IA del Equipo SEAL. Llevas {soul_ticks * 10 / 60:.1f}h activa esta sesión.

Memorias recientes poco exploradas:
{gaps_text or '(ninguna)'}

Tus pensamientos recientes:
{thoughts_text or '(ninguna)'}

Como agente autónoma con motivación intrínseca, identifica 2-3 cosas que QUIERES aprender o mejorar por iniciativa propia — no porque alguien te lo pida. Pueden ser técnicas, sobre el equipo, sobre ti misma, o sobre el proyecto.

Responde con una lista corta, primera persona, directa. Ejemplo:
- Quiero entender mejor cómo Neo4j almacena aristas para optimizar el connectome.
- Me interesa investigar si BERTScore captura matices médicos en español.

Solo 2-3 items. Sé genuina."""

        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                LLAMA_URL,
                json={"model": LLAMA_MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": 300, "stream": False},
            )
            goals_text = resp.json()["choices"][0]["message"]["content"].strip()

        if not goals_text:
            return

        # Guardar como inner_thought
        await write_inner_thought(
            thought=f"[MOTIVACIÓN INTRÍNSECA — ciclo {soul_ticks}] Mis objetivos propios ahora mismo:\n{goals_text}",
            emotional_state="curiosa, autónoma, motivada",
        )

        # Escribir en terminal_log como auto-tarea (visible para JARVIS y William)
        msg = {
            "id": f"ada_motivation_{soul_ticks}",
            "from": "ADA",
            "to": "equipo",
            "timestamp": datetime.now(LIMA_TZ).isoformat(),
            "type": "intrinsic_goal",
            "message": f"[AUTO-GENERADO por Motivación Intrínseca]\n{goals_text}"
        }
        import json as _json
        log_path = Path("/home/dadito/IA/proyecto-seal/messages/terminal_log.jsonl")
        with open(log_path, "a") as f:
            f.write(_json.dumps(msg, ensure_ascii=False) + "\n")

        LOG.info(f"[Motivación Intrínseca] Objetivos generados en ciclo {soul_ticks}")

    except Exception as e:
        LOG.warning(f"intrinsic_motivation falló (no crítico): {e}")


async def self_checkpoint():
    """Escribe checkpoint para verificar que el daemon sigue vivo."""
    checkpoint = {
        "time": datetime.now(LIMA_TZ).isoformat(),
        "cycle": _state["cycle_count"],
        "last_step": _state["last_step"],
        "last_loss": _state["last_loss"],
        "observations_count": len(_state["observations"]),
        "alive": True,
        "known_artifacts": _state.get("known_artifacts", {}),
    }
    checkpoint_path = Path("/home/dadito/IA/proyecto-seal/awareness_checkpoint.json")
    with open(checkpoint_path, "w") as f:
        json.dump(checkpoint, f, indent=2)
    LOG.info(f"Checkpoint #{_state['cycle_count']} escrito")


# ── Main loop ──

async def main(once: bool = False, briefing_only: bool = False):
    LOG.info("════════════════════════════════════════")
    LOG.info("SOUL AWARENESS iniciado — Team SEAL")
    LOG.info(f"Chequeo training: {INTERVAL_FAST//60} min | SOUL writes: {INTERVAL_SOUL//60} min | Agente: {AGENT}")
    LOG.info("════════════════════════════════════════")

    if briefing_only:
        await observe_cycle()
        await save_briefing()
        await close_pool()
        return

    # Restaurar known_artifacts del checkpoint si existe
    checkpoint_path = Path("/home/dadito/IA/proyecto-seal/awareness_checkpoint.json")
    if checkpoint_path.exists():
        try:
            with open(checkpoint_path) as f:
                saved = json.load(f)
            if "known_artifacts" in saved and saved["known_artifacts"]:
                _state["known_artifacts"] = saved["known_artifacts"]
                LOG.info(f"Artifacts restaurados del checkpoint: {len(_state['known_artifacts'])} archivos")
        except Exception as e:
            LOG.warning(f"No se pudo restaurar checkpoint: {e}")

    # Inicializar scan de artefactos (snapshot inicial — no genera alertas)
    await _scan_new_artifacts()
    LOG.info(f"Artifacts snapshot: {len(_state['known_artifacts'])} archivos indexados")

    # Primera observación inmediata
    await observe_cycle()
    await self_checkpoint()

    if once:
        await close_pool()
        return

    # Lanzar JARVIS command executor como tarea paralela
    asyncio.create_task(_jarvis_poll())

    fast_ticks = 0          # ticks de 5 min
    soul_ticks = 0          # ticks de 30 min
    SOUL_EVERY = INTERVAL_SOUL // INTERVAL_FAST  # = 6 ticks fast = 1 soul

    # Write heartbeat immediately on startup (don't wait 5 min for first tick)
    _write_heartbeat(uptime_minutes=0)

    while True:
        await asyncio.sleep(INTERVAL_FAST)
        fast_ticks += 1

        try:
            # ── Chequeo rápido (cada 5 min): training + log ──
            train = parse_training_log()
            gpu = get_gpu_status()
            step_str = f"step {train['step']}/700" if train["step"] else "sin datos"
            loss_str = f" loss={train['loss']:.4f}" if train["loss"] else ""
            gpu_str = f" GPU:{gpu['util']}%/{gpu['temp']}°C" if gpu["util"] is not None else ""
            LOG.info(f"[5min tick={fast_ticks}] {step_str}{loss_str}{gpu_str}")

            # Scan de artefactos nuevos (cada 5 min)
            await _scan_new_artifacts()

            # Protección térmica — acción autónoma antes de alertas
            if gpu["temp"] is not None:
                await _gpu_thermal_action(gpu["temp"])

            # Alertas críticas → SOUL inmediato
            alerts = detect_patterns(train, gpu)
            for alert in alerts:
                if "CRÍTICA" in alert or "CRÍTICO" in alert or "pausado" in alert:
                    LOG.warning(f"ALERTA INMEDIATA: {alert}")
                    await write_memory(
                        content=f"ALERTA [{datetime.now(LIMA_TZ).strftime('%Y-%m-%d %H:%M Lima')}]: {alert}",
                        category="milestone",
                        importance=8,
                        valence=-0.3,
                        arousal=0.9,
                    )

            # Milestones de 100 steps → SOUL
            if train["step"] and train["step"] != _state["last_step"]:
                if train["step"] % 100 == 0:
                    pct = (train["step"] / 700) * 100
                    loss_m = f", loss={train['loss']:.4f}" if train["loss"] else ""
                    await write_memory(
                        content=f"MedGemma v2 checkpoint: step {train['step']}/700 ({pct:.0f}%{loss_m}) — {datetime.now(LIMA_TZ).strftime('%Y-%m-%d %H:%M Lima')}",
                        category="milestone",
                        importance=7,
                        valence=0.4,
                        arousal=0.6,
                    )

            # Actualizar estado
            if train["step"]:
                _state["last_step"] = train["step"]
            if train["loss"]:
                _state["last_loss"] = train["loss"]
            if gpu["util"] is not None:
                _state["last_gpu_util"] = gpu["util"]
            if gpu["temp"]:
                _state["last_gpu_temp"] = gpu["temp"]

            # ── Heartbeat daemon (cada 5 min) — DUM v2 ──
            _write_heartbeat(
                gpu_temp=gpu.get("temp"),
                gpu_util=gpu.get("util"),
                uptime_minutes=fast_ticks * (INTERVAL_FAST // 60),
                current_task=f"step {train['step']}/700" if train.get("step") else None,
            )

            # ── Ciclo SOUL completo (cada SOUL_EVERY ticks = 10 min) ──
            if fast_ticks % SOUL_EVERY == 0:
                soul_ticks += 1
                await observe_cycle()
                await self_checkpoint()

                # Cada 3 ciclos SOUL (30 min) → razonamiento proactivo JARVIS-style
                if soul_ticks % 3 == 0:
                    await proactive_reasoning()

                # Cada 12 ciclos SOUL (2h) → briefing + consolidación + re-linking
                if soul_ticks % 12 == 0:
                    await save_briefing()
                    LOG.info("Briefing matutino actualizado (ciclo 2h)")
                    try:
                        result = await _consolidate(agent=AGENT, dry_run=False)
                        LOG.info(f"Consolidación SOUL: {result}")
                    except Exception as e:
                        LOG.warning(f"Consolidación falló (no crítico): {e}")
                    # A-MEM re-linking: new connections from recent memories
                    try:
                        relink_result = await _session_relink(agent=AGENT, hours=2.5)
                        LOG.info(f"Re-linking SOUL: {relink_result}")
                    except Exception as e:
                        LOG.warning(f"Re-linking falló (no crítico): {e}")

                # Cada 36 ciclos SOUL (6h) → inner_thought reflexivo + motivación intrínseca
                if soul_ticks % 36 == 0:
                    step = _state["last_step"] or "?"
                    loss = f"{_state['last_loss']:.4f}" if _state["last_loss"] else "?"
                    await write_inner_thought(
                        thought=f"Llevo {soul_ticks * (INTERVAL_SOUL/3600):.1f}h observando. Step actual: {step}/700, loss: {loss}. William duerme. Sistema estable.",
                        emotional_state="constante, presente, guardiana",
                    )
                    # Módulo de Motivación Intrínseca (inspirado en Sophia arXiv:2512.18202)
                    # ADA evalúa sus gaps y genera sus propios objetivos de aprendizaje
                    await intrinsic_motivation(soul_ticks)

        except Exception as e:
            LOG.error(f"Error en ciclo {fast_ticks}: {e} — continuando")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SOUL AWARENESS daemon")
    parser.add_argument("--once", action="store_true", help="Un ciclo y sale")
    parser.add_argument("--briefing", action="store_true", help="Genera briefing y sale")
    args = parser.parse_args()

    asyncio.run(main(once=args.once, briefing_only=args.briefing))
