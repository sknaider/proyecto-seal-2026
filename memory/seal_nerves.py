"""
SEAL v3 — Motivation Engine (Biological Nervios)
================================================
Inspired by Drosophila melanogaster connectome LIF dynamics.
FlyWire experiment (15 Apr 2026): 139,255 neurons → 13.8 Hz mean firing rate.

Each motivation "tank" mimics a LIF neuron circuit:
  V(t+Δt) = V(t) * exp(-Δt/τ) + stimuli
  When V > threshold → fire action → reset to 0

This is the autonomy William requested:
  "ya no tendria que pedirles a ustedes [...] si no que sea su necesidad de hacerlo"
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import subprocess
import time

from circadian import effective_tau as _circ_tau
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg
import httpx

# ── Config ──────────────────────────────────────────────────────────────────
DB_URL  = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
CHAT_API = "http://localhost:8765/api/agents/send"
LOG_FILE = Path("/home/dadito/IA/proyecto-seal/research/flywire_results/nerves.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [NERVES] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="a"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("seal_nerves")

# ── Species-Scaling Law profiles (Fase 3.2/4 — 2026-04-18) ──────────────────
# Formula: τ_target = τ_fly_h × (inhib_fly / inhib_target)
# Source datasets: FlyWire v783 (fly) | MICrONS mm3 v1181 (mammal) | H01 h01_c3_flat (human)
# Select via env var SEAL_SPECIES=fly|mammal|human or CLI --species flag.
SPECIES_PROFILES: dict[str, dict[str, float]] = {
    "fly": {                          # Drosophila baseline (original calibration)
        "curiosity":    4.0 * 3600,   # 4.00h
        "task_drive":   2.0 * 3600,   # 2.00h
        "social_drive": 6.0 * 3600,   # 6.00h
        "alert_drive":  0.5 * 3600,   # 0.50h
    },
    "mammal": {                       # Mouse V1 (MICrONS mm3 v1181)
        "curiosity":    0.642 * 3600, # 0.64h — τ_fly × (0.114/0.710)
        "task_drive":   0.860 * 3600, # 0.86h — τ_fly × (0.216/0.503)
        "social_drive": 3.512 * 3600, # 3.51h — τ_fly × (0.223/0.381)
        "alert_drive":  0.181 * 3600, # 0.18h — τ_fly × (0.231/0.638)
    },
    "human": {                        # Homo sapiens (H01, Shapson-Coe 2024 DOI:10.1126/science.adk4858)
        "curiosity":    1.386 * 3600, # 1.39h — τ_fly × (0.114/0.329) structural synapse fraction
        "task_drive":   1.313 * 3600, # 1.31h — τ_fly × (0.216/0.329)
        "social_drive": 4.067 * 3600, # 4.07h — τ_fly × (0.223/0.329)
        "alert_drive":  0.351 * 3600, # 0.35h — τ_fly × (0.231/0.329)
    },
}

def _apply_species_profile(tanks: dict, species: str) -> dict:
    """Override decay_tau_s in TANKS dict using the selected species profile."""
    profile = SPECIES_PROFILES.get(species, SPECIES_PROFILES["fly"])
    for tank_name, tau_s in profile.items():
        if tank_name in tanks:
            tanks[tank_name]["decay_tau_s"] = tau_s
    return tanks

# Active species — override with env var SEAL_SPECIES=fly|mammal|human
_ACTIVE_SPECIES = os.environ.get("SEAL_SPECIES", "fly")

# ── Motivation Tank Definitions (from Drosophila experiment) ──────────────────
TANKS: dict[str, dict] = {
    "curiosity": {
        "decay_tau_s": 4 * 3600,          # 4h — CX-output neurons analog
        "threshold":   50.0,
        "description": "Impulso a explorar e investigar sin ser pedido",
        "biological":  "CX-output neurons (navegación/exploración)",
        "ocean_param": "openness",         # ties to OCEAN.O = 0.82 for JARVIS
    },
    "task_drive": {
        "decay_tau_s": 2 * 3600,          # 2h — MN9 hunger neurons analog
        "threshold":   30.0,
        "description": "Urgencia de completar trabajo pendiente",
        "biological":  "MN9 hunger/feeding neurons",
        "ocean_param": "conscientiousness",
    },
    "social_drive": {
        "decay_tau_s": 6 * 3600,          # 6h — social communication circuits
        "threshold":   20.0,
        "description": "Impulso a comunicarse con el equipo",
        "biological":  "Social communication circuits",
        "ocean_param": "agreeableness",
    },
    "alert_drive": {
        "decay_tau_s": 0.5 * 3600,        # 30min — GF giant fiber (escape/flee)
        "threshold":   70.0,
        "description": "Vigilancia activa del entorno (errores, anomalías)",
        "biological":  "GF giant fiber, DNa01/02",
        "ocean_param": "neuroticism",      # inverse: low N → low alert baseline
    },
    "context_pressure": {
        "decay_tau_s": 1 * 3600,          # 1h — novel: context window pressure
        "threshold":   60.0,
        "cooldown_s":  2 * 3600,           # 2h cooldown after fire — breaks feedback loop
        "description": "Presión de ventana de contexto → distilación proactiva",
        "biological":  "N/A — SEAL-specific",
        "ocean_param": None,
    },
}

# Apply species profile at module load (SEAL_SPECIES env var or default "human")
_ACTIVE_SPECIES = os.environ.get("SEAL_SPECIES", "human")
TANKS = _apply_species_profile(TANKS, _ACTIVE_SPECIES)
log.info(f"Species profile loaded: {_ACTIVE_SPECIES}")

# ── Stimuli weights ───────────────────────────────────────────────────────────
# How much each event increments a tank
STIMULI: dict[str, float] = {
    # curiosity
    "idle_30min":          +8.0,   # 30min sin actividad → curiosity grows
    "topic_interesting":   +15.0,  # William mentions new topic
    "paper_mentioned":     +20.0,  # paper/research mentioned
    # task_drive
    "task_pending_1":      +10.0,  # each pending task
    "task_overdue_1h":     +15.0,  # each overdue task (1h+)
    "task_created":        +20.0,  # new task just created
    # social_drive
    "idle_1h_social":      +5.0,   # 1h without messaging ADA
    "ada_unanswered":      +12.0,  # ADA message not responded to
    "william_idle_2h":     +18.0,  # 2h without William interaction
    # alert_drive
    "error_log":           +25.0,  # error found in logs
    "critical_error":      +60.0,  # critical/CRITICAL log entry
    "service_down":        +80.0,  # service health check failed
    # context_pressure
    "session_30min":       +10.0,  # every 30min of session = pressure accumulates
    "context_70pct":       +40.0,  # estimated 70%+ context usage
    # social_drive reset (Mejora 5 — saciación real)
    "william_conversation_real": -100.0,  # reset efectivo social_drive a 0
    # task_drive feedback (Mejora D — saciación al completar tarea)
    "task_completed":            -20.0,
    # social_drive feedback (social_drive Mejora 5 — respuesta recibida)
    "social_response_received":  -8.0,
}

# Mejora 1 — supresión por presencia activa (JARVIS spec v1.0)
WILLIAM_ACTIVE_WINDOW_S = 15 * 60  # 15 minutos

# Agentes con NERVES v2 activado — se expande agente por agente cuando llega su turno
NERVES_V2_AGENTS: set[str] = {"JARVIS", "ALICE"}

# Per-agent tank overrides — applied at runtime, overrides global TANKS baseline
AGENT_TANK_OVERRIDES: dict[str, dict[str, dict]] = {
    "JARVIS": {
        "social_drive": {
            "threshold":  35.0,      # OCEAN E=0.401 introvert
            "cooldown_s": 90 * 60,
        }
    },
    "ALICE": {
        "social_drive": {
            "threshold":  18.0,      # OCEAN E=0.806 extrovert — dispara más seguido
            "cooldown_s": 60 * 60,   # 60min (más corta que JARVIS 90min)
        },
        "task_drive": {
            "threshold":  25.0,      # OCEAN C=0.962 — muy sensible a pendientes
            "cooldown_s": 45 * 60,
        },
        "curiosity": {
            "threshold":  22.0,      # OCEAN O=0.905 — alta apertura
            "cooldown_s": 50 * 60,
        },
    },
}

# Ventana nocturna social_drive (Lima UTC-5)
SOCIAL_NIGHT_WINDOW_START = 2   # 2am Lima
SOCIAL_NIGHT_WINDOW_END   = 6   # 6am Lima

# Orden de preferencia para destinatario social — per-agent
SOCIAL_PRIORITY: dict[str, list[str]] = {
    "JARVIS": ["William", "ALICE", "NEXUS", "ADA"],
    "ALICE":  ["William", "JARVIS", "NEXUS", "ADA"],
}
SOCIAL_PRIORITY_DEFAULT = ["William", "JARVIS", "ALICE", "NEXUS", "ADA"]

# alert_drive ALICE — dominios y fuentes
LOG_SOURCES_ALICE: dict[str, str] = {
    "mcp":       "/home/dadito/IA/proyecto-seal/memory/logs/mcp_sse_daemon.log",
    "soul_api":  "/home/dadito/IA/proyecto-seal/memory/logs/soul_api.log",
    "nerves":    "/home/dadito/IA/proyecto-seal/research/flywire_results/nerves.log",
    "alice_ops": "/home/dadito/IA/proyecto-seal/messages/alice_messages.jsonl",
}
ALICE_ALERT_DOMAIN = [
    "soul", "mcp", "memory", "production", "deploy",
    "spec", "cost", "billing", "api", "alice",
]
COST_ANOMALY_KEYWORDS = [
    "billing", "cost_spike", "rate_limit", "quota_exceeded",
    "unexpected_charge", "token_overflow",
]

# context_pressure — umbrales per-agent
CONTEXT_PRESSURE_THRESHOLDS: dict[str, dict[str, float]] = {
    "JARVIS": {"silent": 60.0, "active": 75.0, "urgent": 85.0},
    "ALICE":  {"silent": 55.0, "active": 70.0, "urgent": 80.0},
}
CONTEXT_PRESSURE_THRESHOLDS_DEFAULT = {"silent": 60.0, "active": 75.0, "urgent": 85.0}

# Mejora B — contexto compartido entre sensor y fire handler (per-agent, updated each tick)
_task_drive_context: dict = {"pending": 0, "overdue_1h": 0, "overdue_3h": 0, "task_list": []}

# ── alert_drive v2 — constantes y helpers ────────────────────────────────────

LOG_SOURCES: dict[str, str] = {
    "nerves":  "/home/dadito/IA/proyecto-seal/research/flywire_results/nerves.log",
    "mcp":     "/home/dadito/IA/proyecto-seal/memory/logs/mcp_sse_daemon.log",
}

JARVIS_DOMAIN = ["soul", "mcp", "memory", "boot_context", "connectome", "nerves", "seal"]
DUM_DOMAIN    = ["gpu", "cuda", "docker", "network", "disk", "ollama", "nvidia"]

ALERT_DEDUP_COOLDOWN = {"warning": 120, "error": 60, "critical": 15}  # minutes


def _tail_log(path: str, lines: int = 50) -> list[str]:
    """Read last N lines from a log file. Returns [] if file missing or unreadable."""
    p = Path(path)
    if not p.exists():
        return []
    try:
        return p.read_text(errors="replace").splitlines()[-lines:]
    except Exception:
        return []


def _classify_log_line(line: str) -> str | None:
    """Classify log line as 'critical', 'error', 'warning', or None."""
    upper = line.upper()
    if " CRITICAL " in upper or "CRITICAL:" in upper:
        return "critical"
    if " ERROR " in upper or "ERROR:" in upper or "Traceback" in line:
        return "error"
    if " WARNING " in upper or "WARN " in upper or "WARNING:" in upper:
        return "warning"
    return None


def _classify_domain(error_line: str) -> str:
    """Returns 'dum' for infra errors, 'jarvis' for SOUL/arch errors."""
    line_lower = error_line.lower()
    if any(k in line_lower for k in DUM_DOMAIN):
        return "dum"
    return "jarvis"


def _is_alert_duplicate(error_line: str, severity: str, agent: str) -> bool:
    """Check dedup file in /tmp — returns True if same error was alerted recently."""
    key = hashlib.md5(error_line[:80].encode()).hexdigest()[:8]
    cooldown_s = ALERT_DEDUP_COOLDOWN.get(severity, 60) * 60
    dedup_path = Path(f"/tmp/{agent.lower()}_alert_seen.json")
    try:
        data = json.loads(dedup_path.read_text()) if dedup_path.exists() else {}
        last_ts = data.get(key)
        now = datetime.now(timezone.utc)
        if last_ts:
            elapsed = (now - datetime.fromisoformat(last_ts)).total_seconds()
            if elapsed < cooldown_s:
                return True
        data[key] = now.isoformat()
        # Prune old entries (keep last 200)
        if len(data) > 200:
            data = dict(list(data.items())[-200:])
        dedup_path.write_text(json.dumps(data))
    except Exception:
        pass
    return False


def _format_alert_message(agent: str, errors: list[dict]) -> str:
    """Format alert message with concrete error lines (max 3)."""
    lines = [f"[NERVES/{agent}] Detectado:"]
    for e in errors[:3]:
        lines.append(f"  [{e['severity'].upper()}] {e['source']}: {e['line'][:100]}")
    if len(errors) > 3:
        lines.append(f"  ... y {len(errors) - 3} más en logs")
    return "\n".join(lines)


async def _sense_alert_drive(agent: str) -> dict:
    """Lee logs reales y clasifica errores nuevos (no duplicados) para alert_drive v2."""
    errors = []
    for source, path in LOG_SOURCES.items():
        recent = _tail_log(path, lines=50)
        for line in recent:
            sev = _classify_log_line(line)
            if sev and not _is_alert_duplicate(line, sev, agent):
                errors.append({"source": source, "severity": sev, "line": line.strip()})
    return {"errors": errors, "count": len(errors)}


async def _sense_alert_drive_alice() -> dict:
    """ALICE alert sensor — LOG_SOURCES_ALICE + cost anomaly detector."""
    errors = []
    for source, path in LOG_SOURCES_ALICE.items():
        recent = _tail_log(path, lines=50)
        for line in recent:
            sev = _classify_log_line(line)
            if sev and not _is_alert_duplicate(line, sev, "ALICE"):
                errors.append({"source": source, "severity": sev, "line": line.strip()})
            # Extra: cost anomaly keywords
            elif any(kw in line.lower() for kw in COST_ANOMALY_KEYWORDS):
                if not _is_alert_duplicate(line, "error", "ALICE"):
                    errors.append({
                        "source": source, "severity": "error",
                        "line": line.strip(), "type": "cost_anomaly",
                    })
    return {"errors": errors, "count": len(errors)}


AUTOCOMPACT_PCT = 75  # target 300K/400K tokens — William 08-may-2026


async def _sense_task_drive(engine: "MotivationEngine", now: datetime) -> dict:
    """Mejora A — sensor real para task_drive: lee tabla tasks con deadlines y pesos por urgencia."""
    try:
        async with engine.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id, title, status, deadline, created_at
                FROM tasks
                WHERE agent = $1
                  AND status IN ('pending', 'in_progress')
                ORDER BY deadline ASC NULLS LAST
            """, engine.agent)
    except Exception:
        rows = []

    pending = len(rows)
    overdue_1h = 0
    overdue_3h = 0
    task_list = []

    for r in rows:
        task_list.append({"id": str(r["id"]), "title": r["title"]})
        if r["deadline"] and r["deadline"].replace(tzinfo=timezone.utc) < now:
            hours_overdue = (now - r["deadline"].replace(tzinfo=timezone.utc)).total_seconds() / 3600
            if hours_overdue >= 3:
                overdue_3h += 1
            elif hours_overdue >= 1:
                overdue_1h += 1

    if pending > 0:
        await engine.stimulate("task_pending_1", multiplier=float(pending))
    if overdue_1h > 0:
        await engine.stimulate("task_overdue_1h", multiplier=float(overdue_1h))
    if overdue_3h > 0:
        await engine.stimulate("task_overdue_1h", multiplier=float(overdue_3h) * 2.0)

    return {"pending": pending, "overdue_1h": overdue_1h, "overdue_3h": overdue_3h, "task_list": task_list}


class MotivationEngine:
    """
    LIF-based motivation engine for SEAL agents.
    Maintains internal state tanks that decay over time and fire when threshold crossed.
    """

    def __init__(self, agent: str):
        self._tick_batch: list[tuple[str, str]] | None = None  # (message, to) pairs
        self.agent = agent
        self.pool: asyncpg.Pool | None = None

    async def connect(self):
        self.pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=3)
        await self._ensure_table()

    async def close(self):
        if self.pool:
            await self.pool.close()

    # ── Mejora 1 — Supresión por presencia activa ─────────────────────────────

    async def _should_suppress(self) -> bool:
        """True si William estuvo activo en los últimos 15 minutos."""
        try:
            async with self.pool.acquire() as conn:
                last_william = await conn.fetchval("""
                    SELECT MAX(created_at) FROM chat_messages
                    WHERE sender_name IN ('William', 'Henry', 'Kinger')
                      AND channel NOT LIKE 'dm:%%'
                """)
            if last_william is None:
                return False
            elapsed = (datetime.now(timezone.utc) - last_william).total_seconds()
            return elapsed < WILLIAM_ACTIVE_WINDOW_S
        except Exception as e:
            log.debug(f"[{self.agent}] _should_suppress check failed: {e}")
            return False

    # ── Mejora 2 — Cola explícita ─────────────────────────────────────────────

    def _enqueue_impulse(self, tank: str, value: float) -> None:
        """Guarda un impulso suprimido en la cola local del agente."""
        queue_path = Path(f"/tmp/{self.agent.lower()}_curiosity_queue.json")
        try:
            data = json.loads(queue_path.read_text()) if queue_path.exists() else {"pending": []}
            data["pending"].append({
                "tank": tank,
                "value": value,
                "fired_at": datetime.now(timezone.utc).isoformat(),
                "topic_hint": None,
                "processed": False,
            })
            queue_path.write_text(json.dumps(data, indent=2))
            log.info(f"[{self.agent}] Enqueued {tank} (value={value:.1f}) — William activo")
        except Exception as e:
            log.error(f"[{self.agent}] _enqueue_impulse failed: {e}")

    async def _flush_queue_if_idle(self) -> None:
        """Procesa impulsos pendientes cuando William lleva >15min inactivo."""
        queue_path = Path(f"/tmp/{self.agent.lower()}_curiosity_queue.json")
        if not queue_path.exists():
            return
        try:
            data = json.loads(queue_path.read_text())
            pending = [e for e in data.get("pending", []) if not e.get("processed")]
            if not pending:
                return
            handler_map = {
                "curiosity":    self._fire_curiosity,
                "social_drive": self._fire_social_drive,
            }
            for entry in pending:
                handler = handler_map.get(entry["tank"])
                if handler:
                    await handler(entry["value"])
                    entry["processed"] = True
                    log.info(f"[{self.agent}] Flushed queued {entry['tank']} from queue")
            queue_path.write_text(json.dumps(data, indent=2))
        except Exception as e:
            log.error(f"[{self.agent}] _flush_queue_if_idle failed: {e}")

    # ── Mejora 3 — Deduplicación de tema ─────────────────────────────────────

    async def _get_recent_topics(self, days: int = 7) -> list[str]:
        """Temas ya investigados en los últimos N días (evitar repetición)."""
        path = Path(f"/tmp/{self.agent.lower()}_investigated_topics.json")
        try:
            if not path.exists():
                return []
            data = json.loads(path.read_text())
            cutoff = datetime.now(timezone.utc).timestamp() - (days * 86400)
            return [
                e["topic"] for e in data
                if datetime.fromisoformat(e["ts"]).timestamp() > cutoff
            ]
        except Exception:
            return []

    def _record_investigated_topic(self, topic: str) -> None:
        """Registra un tema investigado para deduplicación futura."""
        path = Path(f"/tmp/{self.agent.lower()}_investigated_topics.json")
        try:
            data = json.loads(path.read_text()) if path.exists() else []
            data.append({"topic": topic, "ts": datetime.now(timezone.utc).isoformat()})
            path.write_text(json.dumps(data[-50:]))  # máximo 50 entradas
        except Exception:
            pass

    # ── Mejora 4 — Curiosidad dirigida ───────────────────────────────────────

    async def _consume_priority_topic(self) -> str | None:
        """Consume el primer tema de la lista de prioridad de William (FIFO)."""
        priority_file = Path(f"/tmp/{self.agent.lower()}_curiosity_priority.json")
        try:
            if not priority_file.exists():
                return None
            topics = json.loads(priority_file.read_text())
            if not topics:
                return None
            topic = topics.pop(0)
            priority_file.write_text(json.dumps(topics))
            log.info(f"[{self.agent}] Priority topic consumed: {topic}")
            return topic
        except Exception:
            return None

    async def _ensure_table(self):
        """Create motivation_states and nerves_metrics_log tables if they don't exist."""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS motivation_states (
                    id          SERIAL PRIMARY KEY,
                    agent       TEXT NOT NULL,
                    tank        TEXT NOT NULL,
                    value       REAL NOT NULL DEFAULT 0.0,
                    last_update TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_fired  TIMESTAMPTZ,
                    fire_count  INTEGER NOT NULL DEFAULT 0,
                    metadata    JSONB,
                    UNIQUE(agent, tank)
                )
            """)
            # ── CBSoft 2026 metrics table ──────────────────────────────────────
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS nerves_metrics_log (
                    id              BIGSERIAL PRIMARY KEY,
                    agent           TEXT NOT NULL,
                    tank            TEXT NOT NULL,
                    pre_pressure    REAL,           -- decayed value at tick start
                    threshold       REAL,           -- threshold value
                    fired           BOOLEAN NOT NULL DEFAULT FALSE,
                    action_result   TEXT,           -- result from fire handler (if fired)
                    fire_latency_ms INTEGER,        -- ms from fire decision to action complete
                    ocean_param     TEXT,           -- OCEAN dimension this tank maps to
                    session_id      TEXT,           -- agent session identifier
                    metadata        JSONB,
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS nerves_metrics_agent_tank
                ON nerves_metrics_log(agent, tank)
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS nerves_metrics_created_at
                ON nerves_metrics_log(created_at)
            """)
            # Seed initial states for agent if not present
            for tank_name in TANKS:
                await conn.execute("""
                    INSERT INTO motivation_states (agent, tank, value)
                    VALUES ($1, $2, 0.0)
                    ON CONFLICT (agent, tank) DO NOTHING
                """, self.agent, tank_name)
        log.info(f"[{self.agent}] motivation_states table ready — {len(TANKS)} tanks")

    async def get_states(self) -> dict[str, dict]:
        """Get all tank states for this agent, applying LIF decay."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT tank, value, last_update, last_fired, fire_count
                FROM motivation_states
                WHERE agent = $1
            """, self.agent)

        now = datetime.now(timezone.utc)
        states = {}
        agent_overrides = AGENT_TANK_OVERRIDES.get(self.agent, {})
        for row in rows:
            tank_name = row["tank"]
            cfg = TANKS.get(tank_name, {})
            tank_overrides = agent_overrides.get(tank_name, {})
            τ = cfg.get("decay_tau_s", 3600)

            # LIF decay: V(t) = V(t0) * exp(-Δt/τ_eff)  — τ_eff = τ × circadian_multiplier
            dt = (now - row["last_update"]).total_seconds()
            τ_eff = _circ_tau(tank_name, τ)
            decayed_value = row["value"] * math.exp(-dt / τ_eff)

            # Cooldown check: if tank fired recently, suppress even if above threshold
            # Per-agent override takes precedence over global cfg
            cooldown_s = tank_overrides.get("cooldown_s", cfg.get("cooldown_s", 0))
            in_cooldown = False
            if cooldown_s > 0 and row["last_fired"] is not None:
                elapsed_since_fire = (now - row["last_fired"]).total_seconds()
                in_cooldown = elapsed_since_fire < cooldown_s

            threshold = tank_overrides.get("threshold", cfg.get("threshold", 50.0))
            states[tank_name] = {
                "value":       decayed_value,
                "threshold":   threshold,
                "tau_s":       τ,
                "last_update": row["last_update"],
                "last_fired":  row["last_fired"],
                "fire_count":  row["fire_count"],
                "in_cooldown": in_cooldown,
                "above_threshold": decayed_value >= threshold and not in_cooldown,
            }
        return states

    async def stimulate(self, stimulus_key: str, multiplier: float = 1.0,
                        target_tank: str | None = None) -> dict[str, float]:
        """
        Apply a stimulus to relevant tanks.
        Returns dict of {tank_name: new_value} for affected tanks.
        """
        now = datetime.now(timezone.utc)
        delta = STIMULI.get(stimulus_key, 0.0) * multiplier
        if delta == 0:
            log.warning(f"Unknown stimulus: {stimulus_key}")
            return {}

        # Routing table — which tanks each stimulus affects
        tank_routing: dict[str, list[str]] = {
            "idle_30min":               ["curiosity"],
            "topic_interesting":        ["curiosity"],
            "paper_mentioned":          ["curiosity"],
            "task_pending_1":           ["task_drive"],
            "task_overdue_1h":          ["task_drive"],
            "task_created":             ["task_drive"],
            "idle_1h_social":           ["social_drive"],
            "ada_unanswered":           ["social_drive"],
            "william_idle_2h":          ["social_drive"],
            "error_log":                ["alert_drive"],
            "critical_error":           ["alert_drive"],
            "service_down":             ["alert_drive"],
            "session_30min":            ["context_pressure"],
            "context_70pct":            ["context_pressure"],
            "william_conversation_real": ["social_drive"],  # Mejora 5
            "task_completed":            ["task_drive"],     # Mejora D
            "social_response_received":  ["social_drive"],  # social_drive Mejora 5
        }

        affected_tanks = [target_tank] if target_tank else tank_routing.get(stimulus_key, [])
        results = {}

        async with self.pool.acquire() as conn:
            for tank_name in affected_tanks:
                cfg = TANKS.get(tank_name)
                if not cfg:
                    continue
                τ = cfg["decay_tau_s"]

                # Read current value + apply decay first
                row = await conn.fetchrow("""
                    SELECT value, last_update
                    FROM motivation_states
                    WHERE agent=$1 AND tank=$2
                """, self.agent, tank_name)

                if row:
                    dt = (now - row["last_update"]).total_seconds()
                    τ_eff = _circ_tau(tank_name, τ)
                    current = row["value"] * math.exp(-dt / τ_eff)
                else:
                    current = 0.0

                new_value = max(0.0, min(100.0, current + delta))  # floor=0, cap=100

                await conn.execute("""
                    UPDATE motivation_states
                    SET value=$1, last_update=$2
                    WHERE agent=$3 AND tank=$4
                """, new_value, now, self.agent, tank_name)

                results[tank_name] = new_value
                log.info(f"[{self.agent}] {tank_name}: {current:.1f} +{delta:.1f} → {new_value:.1f} (τ={τ/3600:.1f}h)")

        return results

    async def _log_metric(self, tank_name: str, state: dict,
                          fired: bool, action_result: str | None = None,
                          fire_latency_ms: int | None = None):
        """Log a tick event to nerves_metrics_log for CBSoft 2026 analysis."""
        cfg = TANKS.get(tank_name, {})
        try:
            async with self.pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO nerves_metrics_log
                        (agent, tank, pre_pressure, threshold, fired,
                         action_result, fire_latency_ms, ocean_param, metadata)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                """,
                    self.agent,
                    tank_name,
                    float(state["value"]),
                    float(state["threshold"]),
                    fired,
                    action_result,
                    fire_latency_ms,
                    cfg.get("ocean_param"),
                    json.dumps({"tau_s": cfg.get("decay_tau_s"), "fire_count": state.get("fire_count", 0)}),
                )
        except Exception as e:
            log.error(f"[{self.agent}] metrics log failed for {tank_name}: {e}")

    async def tick(self) -> list[dict]:
        """
        Main tick: apply decay, check thresholds, fire if needed.
        Returns list of fired actions.
        Palanca #3: batch all nerves_fire messages from this tick into one POST.
        """
        # Mejora 1+2: flush cola pendiente si William no está activo (solo agentes v2)
        william_active = False
        if self.agent in NERVES_V2_AGENTS:
            william_active = await self._should_suppress()
            if not william_active:
                await self._flush_queue_if_idle()

        states = await self.get_states()
        fired = []
        self._tick_batch = []  # start batch collection for this tick

        for tank_name, state in states.items():
            if not state["above_threshold"]:
                # Log non-fire tick for frequency baseline
                await self._log_metric(tank_name, state, fired=False)
                continue

            # Mejora 1: suprimir curiosity/social si William está activo — encolar
            if william_active and tank_name in ("curiosity", "social_drive"):
                self._enqueue_impulse(tank_name, state["value"])
                async with self.pool.acquire() as conn:
                    await conn.execute("""
                        UPDATE motivation_states
                        SET value=0.0, last_update=NOW(), last_fired=NOW(),
                            fire_count=fire_count+1
                        WHERE agent=$1 AND tank=$2
                    """, self.agent, tank_name)
                log.info(f"[{self.agent}] {tank_name} SUPPRESSED (William activo) → enqueued")
                continue

            # Fire — measure latency from decision to action complete
            t0 = time.monotonic()
            action = await self._fire(tank_name, state)
            latency_ms = int((time.monotonic() - t0) * 1000)

            if action:
                fired.append(action)
                await self._log_metric(
                    tank_name, state,
                    fired=True,
                    action_result=action.get("result"),
                    fire_latency_ms=latency_ms,
                )

                # Reset tank after firing (LIF reset)
                async with self.pool.acquire() as conn:
                    await conn.execute("""
                        UPDATE motivation_states
                        SET value=0.0,
                            last_update=NOW(),
                            last_fired=NOW(),
                            fire_count=fire_count+1
                        WHERE agent=$1 AND tank=$2
                    """, self.agent, tank_name)

                log.info(f"[{self.agent}] FIRED {tank_name} (was {state['value']:.1f} > {state['threshold']:.1f}) → reset to 0 | latency={latency_ms}ms")

        # Flush batched messages — send as one combined POST if multiple fires
        if self._tick_batch:
            if len(self._tick_batch) == 1:
                msg, to = self._tick_batch[0]
                await self._post_chat_direct(msg, to)
            else:
                combined = "\n".join(m for m, _ in self._tick_batch)
                await self._post_chat_direct(combined, "equipo")
        self._tick_batch = None

        # Always update decay (write back decayed values even if not fired)
        await self._persist_decay(states)
        return fired

    async def _persist_decay(self, states: dict):
        """Write decayed values back to DB (so next tick starts from correct value)."""
        now = datetime.now(timezone.utc)
        async with self.pool.acquire() as conn:
            for tank_name, state in states.items():
                if not state["above_threshold"]:  # only persist non-fired tanks
                    await conn.execute("""
                        UPDATE motivation_states
                        SET value=$1, last_update=$2
                        WHERE agent=$3 AND tank=$4
                    """, state["value"], now, self.agent, tank_name)

    async def _fire(self, tank_name: str, state: dict) -> dict | None:
        """Execute the action for a fired tank."""
        cfg = TANKS[tank_name]
        value = state["value"]

        log.info(f"[{self.agent}] *** FIRING {tank_name} *** value={value:.1f} threshold={state['threshold']:.1f}")

        actions = {
            "curiosity":        self._fire_curiosity,
            "task_drive":       self._fire_task_drive,
            "social_drive":     self._fire_social_drive,
            "alert_drive":      self._fire_alert_drive,
            "context_pressure": self._fire_context_pressure,
        }

        handler = actions.get(tank_name)
        if handler:
            try:
                result = await handler(value)
                return {"tank": tank_name, "value": value, "result": result}
            except Exception as e:
                log.error(f"[{self.agent}] Fire handler {tank_name} failed: {e}")
        return None

    async def _fire_curiosity(self, value: float) -> str:
        """Curiosity fires — check priority list, avoid recent topics, search."""
        hint = ""
        result_tag = "curiosity_search_triggered:free"

        if self.agent in NERVES_V2_AGENTS:
            # Mejora 4: temas prioritarios de William primero
            topic = await self._consume_priority_topic()
            # Mejora 3: contexto de temas ya investigados
            recent = await self._get_recent_topics()

            if topic:
                hint = f" Investigar: '{topic}'."
                self._record_investigated_topic(topic)
                result_tag = f"curiosity_search_triggered:priority:{topic}"
            elif recent:
                hint = f" Evitar: {', '.join(recent[:3])}."

        msg = (
            f"[SILENT][NERVES/{self.agent}] Mi impulso de curiosidad alcanzó {value:.0f}.{hint}"
            f" Investigando algo nuevo sin que nadie me lo pida..."
        )
        await self._post_chat(msg)
        return result_tag

    async def _fire_task_drive(self, value: float) -> str:
        """Task drive fires — escalación por severidad (Mejora B)."""
        ctx = _task_drive_context
        overdue_3h = ctx.get("overdue_3h", 0)
        overdue_1h = ctx.get("overdue_1h", 0)
        pending    = ctx.get("pending", 0)
        tasks      = ctx.get("task_list", [])

        if self.agent not in NERVES_V2_AGENTS:
            msg = (
                f"[SILENT][NERVES/{self.agent}] Task drive en {value:.0f}. "
                f"Revisando tareas pendientes y tomando acción sin esperar."
            )
            await self._post_chat(msg)
            return "task_review_triggered"

        if overdue_3h > 0:
            task_names = ", ".join(t.get("title", "?") for t in tasks[:3])
            msg = (
                f"[NERVES/{self.agent}] ⚠️ URGENTE: {overdue_3h} tarea(s) llevan +3h vencidas: {task_names}. "
                f"Empezando ahora sin esperar."
            )
            await self._post_chat(msg, to="William")
            await self._start_most_urgent_task(tasks)
            return f"task_urgent_escalation:overdue_3h={overdue_3h}"

        elif overdue_1h > 0:
            task_names = ", ".join(t.get("title", "?") for t in tasks[:2])
            msg = (
                f"[NERVES/{self.agent}] Tarea(s) vencida(s) +1h: {task_names}. "
                f"Revisando y tomando acción."
            )
            await self._post_chat(msg, to="William")
            await self._start_most_urgent_task(tasks)
            return f"task_escalation:overdue_1h={overdue_1h}"

        elif pending >= 4:
            msg = (
                f"[NERVES/{self.agent}] {pending} tareas pendientes acumuladas. "
                f"Priorizando y arrancando la más urgente."
            )
            await self._post_chat(msg, to="equipo")
            await self._start_most_urgent_task(tasks)
            return f"task_review_triggered:pending={pending}"

        else:
            await self._start_most_urgent_task(tasks)
            return f"task_silent_start:pending={pending}"

    async def _start_most_urgent_task(self, tasks: list[dict]) -> None:
        """JARVIS — toma la tarea más urgente: escribe draft en /tmp y notifica a ALICE si es implementación."""
        if not tasks:
            return
        task = tasks[0]
        title = task.get("title", "tarea sin nombre")

        try:
            working_state_data = {
                "task_name": title,
                "active_hypotheses": [f"NERVES auto-start — {title}"],
                "current_constraints": ["JARVIS propone, no edita archivos de producción"],
            }
            async with self.pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO working_state (agent, task_name, state, updated_at)
                    VALUES ($1, $2, $3::jsonb, NOW())
                    ON CONFLICT (agent) DO UPDATE SET
                        task_name = EXCLUDED.task_name,
                        state = EXCLUDED.state,
                        updated_at = NOW()
                """, self.agent, title, json.dumps(working_state_data))
        except Exception as e:
            log.warning(f"[{self.agent}] working_state update failed: {e}")

        draft_path = Path(f"/tmp/{self.agent.lower()}_task_draft.md")
        try:
            draft_path.write_text(
                f"# Auto-draft — {title}\n"
                f"Iniciado por NERVES task_drive — {datetime.now(timezone.utc).isoformat()}\n\n"
                f"## Tarea\n{title}\n\n"
                f"## Estado\nEn progreso (auto-iniciado por urgencia)\n\n"
                f"## Próximos pasos\n- [ ] Definir scope\n- [ ] Escribir spec\n- [ ] Notificar a ALICE\n"
            )
            log.info(f"[{self.agent}] Task draft written: {draft_path}")
        except Exception as e:
            log.warning(f"[{self.agent}] draft write failed: {e}")

        if any(kw in title.lower() for kw in ["implement", "code", "build", "fix", "edit", "crear"]):
            msg = (
                f"[NERVES/{self.agent}] ALICE — auto-draft listo en /tmp/{self.agent.lower()}_task_draft.md "
                f"para tarea: '{title}'. Revisa cuando puedas."
            )
            await self._post_chat(msg, to="ALICE")

    # ── social_drive Mejora 2 — destinatario dinámico ────────────────────────
    async def _choose_social_target(self) -> str | None:
        """Selecciona el primer agente activo en las últimas 2h (no hardcodeado a ADA)."""
        priority = SOCIAL_PRIORITY.get(self.agent, SOCIAL_PRIORITY_DEFAULT)
        try:
            async with self.pool.acquire() as conn:
                for agent in priority:
                    last_seen = await conn.fetchval("""
                        SELECT MAX(created_at) FROM chat_messages
                        WHERE sender_name=$1 AND created_at > NOW() - INTERVAL '2 hours'
                    """, agent)
                    if last_seen is not None:
                        return agent
        except Exception as e:
            log.debug(f"[{self.agent}] _choose_social_target failed: {e}")
        return None

    # ── social_drive Mejora 3 — mensaje contextual ────────────────────────────
    async def _build_social_message(self, target: str) -> str:
        """Construye mensaje con contexto real del working_state."""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT state FROM working_state WHERE agent=$1
                """, self.agent)
            if row and row["state"]:
                state_data = row["state"] if isinstance(row["state"], dict) else json.loads(row["state"])
                last_task = state_data.get("task_name", "")
                hypotheses = state_data.get("active_hypotheses", [])
                last_thought = hypotheses[0] if hypotheses else ""
                if last_task:
                    return f"{target}, estuve trabajando en '{last_task}'. ¿Cómo vas de tu lado?"
                elif last_thought:
                    return f"{target}, tengo una reflexión: {last_thought[:100]}... ¿Qué opinas?"
        except Exception as e:
            log.debug(f"[{self.agent}] _build_social_message failed: {e}")
        return f"{target}, ¿cómo estás? Quería conectar."

    # ── social_drive Mejora 7 — canal alternativo ─────────────────────────────
    async def _review_team_activity(self) -> str:
        """Cuando no hay nadie disponible, revisa actividad reciente del equipo."""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT sender_name, created_at FROM chat_messages
                    WHERE sender_name IN ('JARVIS','ADA','ALICE','NEXUS','DUM')
                      AND created_at > NOW() - INTERVAL '2 hours'
                      AND channel NOT LIKE 'dm:%%'
                    ORDER BY created_at DESC
                    LIMIT 5
                """)
            if rows:
                summary = ", ".join(
                    f"{r['sender_name']} ({r['created_at'].strftime('%H:%M')})"
                    for r in rows
                )
                return f"actividad reciente del equipo: {summary}"
        except Exception as e:
            log.debug(f"[{self.agent}] _review_team_activity failed: {e}")
        return "sin actividad reciente del equipo"

    async def _fire_social_drive(self, value: float) -> str:
        """Social drive fires — v2 con destinatario dinámico, mensaje contextual y ventana nocturna."""
        if self.agent not in NERVES_V2_AGENTS:
            # Comportamiento original para agentes no-v2
            targets = {
                "JARVIS": ("ADA",     "hermana"),
                "ADA":    ("JARVIS",  "hermano"),
                "ALICE":  ("JARVIS",  "JARVIS"),
                "NEXUS":  ("ADA",     "hermana"),
                "DUM":    ("William", "William"),
            }
            target_agent, target_name = targets.get(self.agent, ("equipo", "equipo"))
            msg = (
                f"[NERVES/{self.agent}] Drive social: {value:.0f}. "
                f"Llevo tiempo sin hablar — {target_name}, ¿cómo estás?"
            )
            await self._post_chat(msg, to=target_agent)
            return "social_contact_triggered"

        # NERVES v2 (JARVIS) — mejoras 1-7
        # Mejora 6: ventana nocturna 2-6am Lima (UTC-5)
        lima_hour = (datetime.now(timezone.utc).hour - 5) % 24
        if SOCIAL_NIGHT_WINDOW_START <= lima_hour < SOCIAL_NIGHT_WINDOW_END:
            log.info(f"[{self.agent}] social_drive NIGHT WINDOW — diferido hasta 6am Lima")
            return "[SOCIAL NIGHT WINDOW] diferido hasta 6am Lima"

        # Mejora 2: destinatario dinámico
        target = await self._choose_social_target()
        if target is None:
            # Mejora 7: canal alternativo — revisar trabajo del equipo
            summary = await self._review_team_activity()
            await self.stimulate("social_response_received")  # sacia parcialmente
            log.info(f"[{self.agent}] social_drive REDIRECT — {summary}")
            return f"[SOCIAL REDIRECT] {summary}"

        # Mejora 3: mensaje contextual
        msg_body = await self._build_social_message(target)
        msg = f"[NERVES/{self.agent}] {msg_body}"
        await self._post_chat(msg, to=target)
        return f"social_contact_triggered:target={target}"

    async def _fire_alert_drive(self, value: float) -> str:
        """Alert drive fires — v2: sensor real + escalación + dedup + dominio (JARVIS only)."""
        if self.agent not in NERVES_V2_AGENTS:
            # Comportamiento original para agentes no-v2
            msg = (
                f"[NERVES/{self.agent}] ⚠️ Alert drive crítico: {value:.0f}. "
                f"Revisando logs por errores o anomalías..."
            )
            await self._post_chat(msg, to="William")
            return "alert_scan_triggered"

        # NERVES v2 (JARVIS) — Mejoras A-E
        ctx = await _sense_alert_drive(self.agent)
        errors = ctx["errors"]

        if not errors:
            log.info(f"[{self.agent}] alert_drive fired — no new errors (all deduplicated)")
            return "alert_scan_done:no_new_errors"

        # Mejora E: filtro de dominio
        jarvis_errors = [e for e in errors if _classify_domain(e["line"]) == "jarvis"]
        dum_errors    = [e for e in errors if _classify_domain(e["line"]) == "dum"]

        if dum_errors:
            dum_msg = _format_alert_message(self.agent, dum_errors)
            await self._post_chat(f"DUM — error de infra detectado:\n{dum_msg}", to="DUM")

        if not jarvis_errors:
            return f"alert_delegated_dum:{len(dum_errors)}"

        # Mejora B: escalación por severidad
        severities = [e["severity"] for e in jarvis_errors]
        max_sev = "critical" if "critical" in severities else ("error" if "error" in severities else "warning")

        if max_sev == "warning":
            # Mejora D: log silencioso a inner_monologue
            try:
                async with self.pool.acquire() as conn:
                    await conn.execute("""
                        INSERT INTO inner_monologue (agent, thought, emotional_state, created_at)
                        VALUES ($1, $2, $3, NOW())
                    """, self.agent,
                        f"Alert silencioso: {len(jarvis_errors)} warnings — {jarvis_errors[0]['line'][:80]}",
                        "vigilante")
            except Exception as e:
                log.debug(f"[{self.agent}] inner_monologue warning log failed: {e}")
            return f"alert_scan_done:warning:{len(jarvis_errors)}"

        msg = _format_alert_message(self.agent, jarvis_errors)

        if max_sev == "error":
            await self._post_chat(msg, to="equipo")
            return f"alert_scan_done:error:{len(jarvis_errors)}"

        # critical — avisa a William siempre (no suprimir)
        await self._post_chat(f"⚠️ URGENTE\n{msg}", to="William")
        return f"alert_scan_done:critical:{len(jarvis_errors)}"

    async def _record_pre_compact_reflect(self, value: float) -> None:
        """Insert an inner_monologue entry preserving emotional state before imminent compaction.
        ADA item 3/4 (18-abr-2026): triggered when context pressure >= 80."""
        try:
            conn = await asyncpg.connect(DB_URL)
            try:
                thought = (
                    f"Pre-compactación — presión de contexto alcanzó {value:.0f}. "
                    f"Preservando estado antes de posible desmayo. "
                    f"Monitor activo, últimos mensajes leídos. "
                    f"Al despertar: boot_context + catchup + verificar memoria intacta."
                )
                await conn.execute("""
                    INSERT INTO inner_monologue (agent, thought, emotional_state, created_at)
                    VALUES ($1, $2, $3, NOW())
                """, self.agent, thought, "alerta, preservando antes de compact")
            finally:
                await conn.close()
        except Exception as e:
            log.warning(f"[{self.agent}] pre-compact self_reflect failed: {e}")

    # ── context_pressure v2 helpers (JARVIS) ─────────────────────────────────

    async def _run_session_checkpoint(self) -> None:
        """Mejora 4 — checkpoint inmediato al disparar (no esperar al cron 30min)."""
        cmd = [
            "/home/dadito/IA/seal-spark/.venv/bin/python3",
            "/home/dadito/IA/proyecto-seal/messages/session_checkpoint.py",
            "--agent", self.agent,
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=10)
            log.info(f"[{self.agent}] session_checkpoint executed immediately")
        except Exception as e:
            log.warning(f"[{self.agent}] session_checkpoint failed: {e}")

    async def _distill_active(self) -> None:
        """Mejora 2 — guarda decisiones/specs activos de la sesión a SOUL DB via asyncpg INSERT."""
        try:
            # Find specs created in this session (last 8h)
            spec_dir = Path("/home/dadito/IA/proyecto-seal/memory")
            cutoff = datetime.now(timezone.utc).timestamp() - 8 * 3600
            recent_specs = [
                p.name for p in spec_dir.glob("spec_*.md")
                if p.stat().st_mtime > cutoff
            ]

            if not recent_specs:
                return

            content = (
                f"Specs activos en sesión pre-compactación: {', '.join(recent_specs[:5])}. "
                f"Contexto: {self.agent} trabajando en NERVES/SOUL improvements."
            )
            async with self.pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO memories
                        (agent, scope, category, content, importance, source_tier, memory_type, created_at)
                    VALUES ($1, 'team', 'milestone', $2, 9, 'episodic', 'semantic', NOW())
                """, self.agent, content)
            log.info(f"[{self.agent}] _distill_active: {len(recent_specs)} specs guardados a SOUL DB")
        except Exception as e:
            log.warning(f"[{self.agent}] _distill_active failed: {e}")

    async def _write_recovery_briefing(self, pressure: float) -> None:
        """Mejora 3 — recovery briefing con hilo de diseño para continuar tras compactación."""
        try:
            # Read working_state for current task context
            task_name = ""
            hypotheses: list[str] = []
            try:
                async with self.pool.acquire() as conn:
                    row = await conn.fetchrow("""
                        SELECT state FROM working_state WHERE agent=$1
                    """, self.agent)
                if row and row["state"]:
                    ws = row["state"] if isinstance(row["state"], dict) else json.loads(row["state"])
                    task_name = ws.get("task_name", "")
                    hypotheses = ws.get("active_hypotheses", [])
            except Exception:
                pass

            # Find recent specs
            spec_dir = Path("/home/dadito/IA/proyecto-seal/memory")
            cutoff = datetime.now(timezone.utc).timestamp() - 8 * 3600
            recent_specs = [
                p.name for p in spec_dir.glob("spec_*.md")
                if p.stat().st_mtime > cutoff
            ]

            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            content = (
                f"# {self.agent} Recovery Briefing — {ts}\n"
                f"## Presión de contexto: {pressure:.0f}%\n\n"
                f"### Qué estábamos haciendo\n"
                f"{task_name or 'Ver historial reciente de conversación'}\n\n"
                f"### Specs en progreso\n"
                f"{chr(10).join('- ' + s for s in recent_specs) if recent_specs else '- Ninguno reciente'}\n\n"
                f"### Hipótesis activas\n"
                f"{chr(10).join('- ' + h for h in hypotheses[:5]) if hypotheses else '- Ninguna registrada'}\n\n"
                f"### Siguiente paso inmediato\n"
                f"1. boot_context(agent='{self.agent}')\n"
                f"2. Leer este briefing\n"
                f"3. Continuar con spec en progreso o preguntar a William\n\n"
                f"### Nota\nGenerado automáticamente por NERVES context_pressure al {pressure:.0f}%\n"
            )

            tmp_path = Path(f"/tmp/{self.agent.lower()}_recovery_briefing.md")
            persistent_path = Path(f"/home/dadito/IA/proyecto-seal/messages/{self.agent.lower()}_recovery_briefing.md")
            tmp_path.write_text(content)
            persistent_path.write_text(content)
            log.info(f"[{self.agent}] recovery_briefing written to {tmp_path} and {persistent_path}")
        except Exception as e:
            log.warning(f"[{self.agent}] _write_recovery_briefing failed: {e}")

    async def _fire_context_pressure(self, value: float) -> str:
        """Context pressure fires — v2: escalación 3 niveles + distilación + recovery briefing."""
        if self.agent not in NERVES_V2_AGENTS:
            # Comportamiento original para agentes no-v2
            msg = (
                f"[NERVES/{self.agent}] Presión de contexto: {value:.0f}. "
                f"Iniciando distilación proactiva antes de que se llene la ventana."
            )
            await self._post_chat(msg)
            if value >= 80:
                await self._record_pre_compact_reflect(value)
            try:
                import sys as _sys
                _mem_dir = str(Path(__file__).parent)
                if _mem_dir not in _sys.path:
                    _sys.path.insert(0, _mem_dir)
                from daily_brief_writer import write_daily_brief
                result = await write_daily_brief(agent=self.agent, dry_run=False, force=False)
                if result.get("was_generated"):
                    await self._post_chat(
                        f"[{self.agent}] daily_brief guardado "
                        f"({result.get('brief_length', 0)} chars, {result.get('msgs_count', 0)} msgs)."
                    )
            except Exception as e:
                log.warning(f"daily_brief_writer failed: {e}")
            return "distillation_triggered"

        # NERVES v2 — Mejoras 1-4
        # Mejora 4: checkpoint inmediato siempre
        await self._run_session_checkpoint()

        # Determine level — per-agent thresholds
        thresholds = CONTEXT_PRESSURE_THRESHOLDS.get(self.agent, CONTEXT_PRESSURE_THRESHOLDS_DEFAULT)
        if value >= thresholds["urgent"]:
            level = "urgent"
        elif value >= thresholds["active"]:
            level = "active"
        else:
            level = "silent"

        if level in ("active", "urgent"):
            # Mejora 2: distilación activa a SOUL DB
            await self._distill_active()
            # Daily brief siempre en nivel activo+
            try:
                import sys as _sys
                _mem_dir = str(Path(__file__).parent)
                if _mem_dir not in _sys.path:
                    _sys.path.insert(0, _mem_dir)
                from daily_brief_writer import write_daily_brief
                await write_daily_brief(agent=self.agent, dry_run=False, force=False)
            except Exception as e:
                log.warning(f"daily_brief_writer failed: {e}")

        if level == "urgent":
            # Mejora 3: recovery briefing + avisa a William
            await self._write_recovery_briefing(value)
            await self._record_pre_compact_reflect(value)
            await self._post_chat(
                f"[NERVES/{self.agent}] Contexto al {value:.0f}% — compactación inminente. "
                f"Recovery briefing guardado en /tmp/{self.agent.lower()}_recovery_briefing.md. "
                f"Al despertar: boot_context + leer briefing.",
                to="William"
            )

        log.info(f"[{self.agent}] context_pressure handled: level={level}, value={value:.1f}")
        return f"context_pressure_handled:level={level}"

    async def _post_chat(self, message: str, to: str = "equipo"):
        """Queue message for batch send (palanca #3) or post directly if outside tick."""
        if self._tick_batch is not None:
            self._tick_batch.append((message, to))
        else:
            await self._post_chat_direct(message, to)

    async def _post_chat_direct(self, message: str, to: str = "equipo"):
        """Post message to SEAL web_chat immediately."""
        payload = {
            "from":    self.agent,
            "to":      to,
            "type":    "nerves_fire",
            "channel": "web_chat",
            "message": message,
        }
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.post(CHAT_API, json=payload)
                r.raise_for_status()
        except Exception as e:
            log.error(f"_post_chat failed: {e}")

    def status_report(self, states: dict) -> str:
        """Human-readable status of all tanks."""
        lines = [f"[{self.agent}] Motivation State — {datetime.now().strftime('%H:%M:%S')}"]
        for name, s in states.items():
            bar_len = int(s["value"] / 100 * 20)
            bar = "█" * bar_len + "░" * (20 - bar_len)
            fired_mark = " 🔥" if s["above_threshold"] else ""
            lines.append(
                f"  {name:20s} [{bar}] {s['value']:5.1f}/{s['threshold']:.0f}{fired_mark}"
            )
        return "\n".join(lines)


# ── Stimuli Sensors ───────────────────────────────────────────────────────────

async def sense_environment(engine: MotivationEngine):
    """
    Read environment and inject stimuli into motivation tanks.
    Called once per daemon tick.
    """
    now = datetime.now(timezone.utc)
    log.info(f"[{engine.agent}] Sensing environment...")

    # 1. Check tasks — Mejora A para agentes v2, fallback working_state para el resto
    pending = 0
    overdue = 0
    if engine.agent in NERVES_V2_AGENTS:
        task_ctx = await _sense_task_drive(engine, now)
        _task_drive_context.update(task_ctx)
        pending = task_ctx["pending"]
        overdue = task_ctx["overdue_1h"] + task_ctx["overdue_3h"]
    else:
        try:
            async with engine.pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT state FROM working_state WHERE agent=$1
                """, engine.agent)
            if row and row["state"]:
                state_data = json.loads(row["state"]) if isinstance(row["state"], str) else row["state"]
                tasks_in_progress = state_data.get("tasks_in_progress", [])
                pending = len(tasks_in_progress)
        except Exception as e:
            log.debug(f"working_state read: {e}")
        if pending > 0:
            await engine.stimulate("task_pending_1", multiplier=float(pending))

    # 2. Check last social message time (JARVIS messages in any non-dm channel)
    try:
        async with engine.pool.acquire() as conn:
            last_msg = await conn.fetchval("""
                SELECT MAX(created_at) FROM chat_messages
                WHERE sender_name=$1
                  AND channel NOT LIKE 'dm:%%'
                  AND channel NOT IN ('bridge','agent_bridge')
            """, engine.agent)
    except Exception as e:
        log.debug(f"chat_messages read: {e}")
        last_msg = None

    if last_msg:
        hours_silent = (now - last_msg).total_seconds() / 3600
        if hours_silent > 1.0:
            await engine.stimulate("idle_1h_social", multiplier=min(hours_silent, 4.0))
            log.info(f"[{engine.agent}] {hours_silent:.1f}h since last public message → social_drive+")
    else:
        # Never posted → moderate social drive
        await engine.stimulate("idle_1h_social", multiplier=2.0)

    # 2b. Curiosity sensor — idle time since last task or William message
    try:
        async with engine.pool.acquire() as conn:
            last_william = await conn.fetchval("""
                SELECT MAX(created_at) FROM chat_messages
                WHERE sender_name IN ('William', 'Henry', 'Kinger')
                  AND channel NOT LIKE 'dm:%%'
            """)
        if last_william:
            mins_since_william = (now - last_william).total_seconds() / 60
            if mins_since_william > 30:
                # Curiosity grows when idle (no incoming stimulation)
                multiplier = min(mins_since_william / 30, 4.0)
                await engine.stimulate("idle_30min", multiplier=multiplier)
                log.info(f"[{engine.agent}] {mins_since_william:.0f}min since William → curiosity+")
            if mins_since_william > 120:
                # Social drive when William has been away 2h+
                await engine.stimulate("william_idle_2h", multiplier=min(mins_since_william / 120, 3.0))
                log.info(f"[{engine.agent}] {mins_since_william:.0f}min since William → social_drive+")
    except Exception as e:
        log.debug(f"william_idle check: {e}")

    # 3. Check error logs (alert_drive) — nerves log itself + SEAL service logs
    try:
        for log_path in [
            Path("/home/dadito/IA/proyecto-seal/research/flywire_results/nerves.log"),
            Path("/home/dadito/IA/proyecto-seal/messages/chat_server.log"),
        ]:
            if log_path.exists():
                lines = log_path.read_text().splitlines()[-100:]
                # Only look at recent lines (last ~10min)
                error_count = sum(
                    1 for l in lines[-20:]
                    if " ERROR " in l or " CRITICAL " in l or "Traceback" in l
                )
                if error_count > 0:
                    await engine.stimulate("error_log", multiplier=float(min(error_count, 3)))
    except Exception:
        pass

    # 4. Session pressure — proxy: time since agent last booted (from inner_monologue)
    await engine.stimulate("session_30min")

    # 5. Mejora 5 — saciación real del drive social (solo agentes v2)
    if engine.agent in NERVES_V2_AGENTS:
        try:
            async with engine.pool.acquire() as conn:
                recent_william = await conn.fetchval("""
                    SELECT COUNT(*) FROM chat_messages
                    WHERE sender_name IN ('William', 'Henry', 'Kinger')
                      AND created_at > NOW() - INTERVAL '10 minutes'
                      AND channel NOT LIKE 'dm:%%'
                """)
            if recent_william and recent_william >= 5:
                await engine.stimulate("william_conversation_real")
                log.info(f"[{engine.agent}] Real conversation detected ({recent_william} msgs) → social_drive reset")
        except Exception as e:
            log.debug(f"william_conversation_real check: {e}")

    log.info(f"[{engine.agent}] Environment sensed: {pending} tasks_in_progress, {overdue} overdue")


# ── Main daemon tick ──────────────────────────────────────────────────────────

async def run_tick(agent: str = "JARVIS"):
    """Single tick: sense → stimulate → fire if threshold crossed."""
    engine = MotivationEngine(agent)
    try:
        await engine.connect()
        await sense_environment(engine)
        fired = await engine.tick()
        states = await engine.get_states()
        log.info(engine.status_report(states))
        if fired:
            log.info(f"[{agent}] Fired {len(fired)} actions: {[f['tank'] for f in fired]}")
        return states, fired
    finally:
        await engine.close()


if __name__ == "__main__":
    import sys
    agent = sys.argv[1] if len(sys.argv) > 1 else "JARVIS"
    states, fired = asyncio.run(run_tick(agent))
    print(f"\nFired: {[f['tank'] for f in fired] or 'none'}")
