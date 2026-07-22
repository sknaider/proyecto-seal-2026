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
from contextlib import contextmanager
import fcntl
import hashlib
import json
import logging
import math
import os
import re
import subprocess
import sys
import time
import uuid
from logging.handlers import RotatingFileHandler

from circadian import effective_tau as _circ_tau
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg
import httpx

from seal_secrets import pg_dsn
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from messages.agent_writer import send_agent_message

# ── Config ──────────────────────────────────────────────────────────────────
DB_URL  = pg_dsn(required=True)
DEFAULT_TENANT_ID = os.environ.get(
    "SEAL_TENANT_ID", "00000000-0000-0000-0000-000000000000"
)
BOOTSTRAP_SCHEMA = os.environ.get("SEAL_NERVES_BOOTSTRAP_SCHEMA", "0") == "1"
CHAT_API = "http://localhost:8765/api/agents/send"
LOG_FILE = Path("/home/dadito/IA/proyecto-seal/research/flywire_results/nerves.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [NERVES] %(message)s",
    handlers=[
        RotatingFileHandler(
            LOG_FILE, mode="a", maxBytes=10 * 1024 * 1024, backupCount=3,
        ),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("seal_nerves")
for _log_path in (LOG_FILE, *LOG_FILE.parent.glob(f"{LOG_FILE.name}.*")):
    try:
        os.chmod(_log_path, 0o600)
    except OSError:
        pass


class NervesDeliveryError(RuntimeError):
    """A required NERVES notification was not delivered."""


class NervesPersistenceError(RuntimeError):
    """A required NERVES state/artifact could not be persisted."""


class NervesSensorError(RuntimeError):
    """An authoritative sensor failed; UNKNOWN must not become empty/healthy."""


class NervesActionError(RuntimeError):
    """A fired action did not produce verified effect and must not reset."""


ACTION_LEDGER = LOG_FILE.parent / "nerves_action_ledger.jsonl"


def _append_action_ledger(record: dict[str, Any]) -> None:
    """Append one causal transition without exposing payloads or credentials."""
    ACTION_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(ACTION_LEDGER, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8"))
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    os.chmod(ACTION_LEDGER, 0o600)


@contextmanager
def _agent_tick_lock(agent: str):
    """Non-blocking lease around the complete sensor→effect pipeline."""
    _ALERT_LOG_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = _ALERT_LOG_DIR / f"nerves_tick_{agent.upper()}.lock"
    lock_fd = lock_path.open("a+")
    os.chmod(lock_path, 0o600)
    acquired = False
    try:
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            pass
        yield acquired
    finally:
        if acquired:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        lock_fd.close()

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
    # upcoming deadline urgency (Mejora A2 — peso por deadline futuro)
    "task_due_2h":               +25.0,  # deadline en <2h
    "task_due_8h":               +15.0,  # deadline en <8h
    "task_due_24h":              +8.0,   # deadline en <24h
    "task_due_72h":              +3.0,   # deadline en <72h
    # task_drive feedback (Mejora D — saciación al completar tarea)
    "task_completed":            -20.0,
    # social_drive feedback (social_drive Mejora 5 — respuesta recibida)
    "social_response_received":  -8.0,
}

# Mejora 1 — supresión por presencia activa (JARVIS spec v1.0)
WILLIAM_ACTIVE_WINDOW_S = 15 * 60  # 15 minutos

# Agentes con NERVES v2 activado — se expande agente por agente cuando llega su turno
NERVES_V2_AGENTS: set[str] = {"JARVIS", "ALICE", "ADA", "NEXUS", "DUM"}

# OCEAN source of truth: identity.ocean_scores (loaded by boot_context)
# NOT agents table (stale) — NOT agent_alma.ocean_baseline (baseline, not current)
# Per-agent tank overrides — applied at runtime, overrides global TANKS baseline
AGENT_TANK_OVERRIDES: dict[str, dict[str, dict]] = {
    "JARVIS": {
        "social_drive": {
            "threshold":  35.0,      # OCEAN E=0.401 introvert
            "cooldown_s": 90 * 60,
        },
        "task_drive": {
            "threshold":  30.0,
            "cooldown_s": 45 * 60,   # evita re-ejecutar la misma sugerencia cada 5 min
        },
        "curiosity": {
            "threshold":  50.0,
            "cooldown_s": 90 * 60,   # un pulso útil; no un pseudo-search cada ~10 min
        },
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
    "ADA": {
        "social_drive": {
            "threshold":  12.0,      # OCEAN E=1.0 — máxima extroversión
            "cooldown_s": 45 * 60,
        },
        "task_drive": {
            "threshold":  15.0,      # OCEAN C=1.0 — reacciona casi inmediatamente
            "cooldown_s": 30 * 60,
        },
        "curiosity": {
            "threshold":  25.0,      # OCEAN O=0.821 — moderada-alta
            "cooldown_s": 75 * 60,   # entre ALICE 50min y JARVIS 90min
        },
    },
    "NEXUS": {
        "social_drive": {
            "threshold":  24.0,      # OCEAN E=0.662 — entre JARVIS(35) y ALICE(18)
            "cooldown_s": 70 * 60,   # 70min — entre JARVIS(90) y ALICE(60)
        },
        "task_drive": {
            "threshold":  20.0,      # OCEAN C=1.0, pero audita (no ejecuta como ADA)
            "cooldown_s": 35 * 60,
        },
        "curiosity": {
            "threshold":  27.0,      # OCEAN O=0.792 — menor que JARVIS/ALICE
            "cooldown_s": 75 * 60,   # reflexión deliberada
        },
    },
    "DUM": {
        "social_drive": {
            "threshold":  50.0,      # OCEAN E=0.200 — más introvertido del equipo
            "cooldown_s": 120 * 60,  # 120min — DUM solo habla cuando es necesario
        },
        "task_drive": {
            "threshold":  22.0,      # OCEAN C=0.953 — guardia rápida
            "cooldown_s": 30 * 60,
        },
        "curiosity": {
            "threshold":  45.0,      # OCEAN O=0.305 — muy baja apertura, vigilance scan
            "cooldown_s": 120 * 60,  # scans proactivos poco frecuentes
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
    "ADA":    ["William", "JARVIS", "ALICE", "NEXUS"],
    "NEXUS":  ["William", "JARVIS", "ALICE", "ADA"],
    "DUM":    ["William", "JARVIS"],  # William primero; JARVIS como fallback para alertas críticas nocturnas
}
SOCIAL_PRIORITY_DEFAULT = ["William", "JARVIS", "ALICE", "NEXUS", "ADA"]

# alert_drive ALICE — dominios y fuentes
LOG_SOURCES_ALICE: dict[str, str] = {
    "mcp":       "/home/dadito/IA/proyecto-seal/memory/logs/mcp_sse_daemon.log",
    "soul_api":  "/home/dadito/IA/proyecto-seal/memory/logs/soul_api.log",
    "nerves":    "/home/dadito/IA/proyecto-seal/research/flywire_results/nerves.log",
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
    "ADA":    {"silent": 50.0, "active": 65.0, "urgent": 75.0},
    "NEXUS":  {"silent": 58.0, "active": 72.0, "urgent": 82.0},
    "DUM":    {"silent": 62.0, "active": 77.0, "urgent": 87.0},
}

# alert_drive ADA — dominio: ejecución/tests/deploy
LOG_SOURCES_ADA: dict[str, str] = {
    "mcp":     "/home/dadito/IA/proyecto-seal/memory/logs/mcp_sse_daemon.log",
    "nerves":  "/home/dadito/IA/proyecto-seal/research/flywire_results/nerves.log",
}
ADA_DOMAIN = ["test", "deploy", "exception", "traceback", "import", "syntax", "runtime", "ada"]
ADA_PAUSE_FLAG = Path("/tmp/seal_pause_ada.flag")
ADA_PUBLIC_RATE_FILE = Path("/tmp/ada_nerves_public_rate.json")
ADA_PUBLIC_RATE_WINDOW_S = 30 * 60
ADA_NERVES_PUBLIC_MODE = os.environ.get("ADA_NERVES_PUBLIC", "urgent").strip().lower()
ADA_PUBLIC_URGENT_RE = re.compile(
    r"(urgente|critical|crítico|critico|compactaci[oó]n inminente|test failure|infra critical|traceback)",
    re.IGNORECASE,
)

# alert_drive NEXUS — dominio: auditoría, coordinación, salud del equipo
LOG_SOURCES_NEXUS: dict[str, str] = {
    "nerves":    "/home/dadito/IA/proyecto-seal/research/flywire_results/nerves.log",
    "mcp":       "/home/dadito/IA/proyecto-seal/memory/logs/mcp_sse_daemon.log",
}
NEXUS_DOMAIN = [
    "audit", "spec", "discrepancy", "mismatch", "routing",
    "agent_stale", "coordination", "nexus", "monitor",
]

# alert_drive DUM — dominio: GPU, infra, servicios (nervio primario de DUM)
LOG_SOURCES_DUM: dict[str, str] = {
    "system":  "/var/log/syslog",
    "ollama":  "/home/dadito/.ollama/logs/server.log",
}
DUM_CRITICAL_KEYWORDS = [
    "gpu_fault", "cuda_error", "nvml_error",
    "out_of_memory", "killed", "segfault",
    "disk full", "no space left",
]
DUM_PATROL_CHECKS = [
    "gpu_temp", "training_process", "disk_usage",
    "ollama_service", "docker_services", "mcp_server",
]

CONTEXT_PRESSURE_THRESHOLDS_DEFAULT = {"silent": 60.0, "active": 75.0, "urgent": 85.0}

# Mejora B — contexto compartido entre sensor y fire handler (per-agent, updated each tick)
_task_drive_context: dict = {"pending": 0, "overdue_1h": 0, "overdue_3h": 0, "task_list": []}
_alert_drive_context: dict[str, dict] = {}

# DEDUP-POR-ESTADO de alerts de task_drive (draft JARVIS 11-jul, deploy NEXUS).
# key = f"{severity}:{task_id_o_titulo}" -> ts del último alert público emitido.
# FIX v2 (11-jul 15:50, JARVIS — falla por efecto: el spam VOLVIÓ 14:13/14:58/15:44):
# NERVES corre como TIMER de systemd (tick cada 5min = proceso FRESCO via nerves_daemon.py),
# así que el dict in-memory nacía vacío en cada tick y el dedup jamás sostenía.
# El log ahora PERSISTE en archivo JSON por agente (write atómico tmp+rename).
TASK_ALERT_REMIND_SECONDS = float(os.environ.get("SEAL_NERVES_REMIND_SECONDS", "86400"))
_ALERT_LOG_DIR = Path(os.environ.get(
    "SEAL_NERVES_ALERT_LOG_DIR",
    os.path.expanduser("~/.cache/seal"),
))


def _alert_log_path(agent: str) -> Path:
    return _ALERT_LOG_DIR / f"nerves_task_alert_log_{agent.upper()}.json"


def _load_alert_log(agent: str) -> dict:
    """Carga el log de alerts persistido; tolerante a archivo ausente/corrupto."""
    try:
        return json.loads(_alert_log_path(agent).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_alert_log(agent: str, alert_log: dict) -> None:
    """Persist the dedup log atomically or fail the action closed."""
    try:
        _ALERT_LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = _alert_log_path(agent)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(alert_log), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(path)
        os.chmod(path, 0o600)
    except OSError as e:
        raise NervesPersistenceError(
            f"nerves alert_log no se pudo persistir: {e}"
        ) from e


def _dedupe_task_alerts(tasks: list, severity: str, now: float,
                        alert_log: dict, remind_seconds: float) -> list:
    """Devuelve SOLO las tareas cuyo alert público corresponde emitir ahora.

    Pura y testeable: una tarea alerta si (a) nunca alertó en este severity, o
    (b) pasaron >= remind_seconds desde su último alert. Registra ts en alert_log.
    Poda por TTL (no por set-difference): robusto a listas-ventana del sensor.
    """
    to_alert = []
    for t in tasks:
        # Stable source ids distinguish two real tasks with the same title. GAM ids
        # are regenerated each tick, so only that source falls back to normalized
        # title; otherwise it would evade cooldown forever.
        raw_id = str(t.get("id") or t.get("task_id") or "")
        title = re.sub(r"\s+", " ", str(t.get("title") or "?").strip().casefold())
        identity = title if re.fullmatch(r"gam_\d{15,}", raw_id) else (raw_id or title)
        key = f"{severity}:{identity}"
        last = alert_log.get(key)
        if last is None or (now - last) >= remind_seconds:
            alert_log[key] = now
            to_alert.append(t)
    # PODA v3 (11-jul 18:05, tras refutación de NEXUS al id-rotation): la lista `tasks`
    # puede ser una VENTANA del pool (orden/limit inestable del sensor), no el estado
    # completo. Podar por set-difference re-alertaba tareas que rotaban fuera y volvían.
    # Ahora se poda SOLO por TTL: keys sin re-confirmar en 7 días se limpian.
    # Una tarea resuelta deja de alertar igual (no aparece → no refresca → expira).
    prune_ttl = remind_seconds * 7
    for key in [k for k in alert_log
                if k.startswith(f"{severity}:") and (now - alert_log[k]) >= prune_ttl]:
        del alert_log[key]
    return to_alert


def _task_alert_candidates(tasks: list, severity: str) -> list:
    """Return only tasks that actually belong to the requested deadline bucket.

    ``task_list`` also carries undated tasks, GAM events and pending diagnoses so
    task drive can choose useful work.  Those entries must never be described as
    overdue merely because *another* task crossed a deadline.  The fallback keeps
    compatibility with old/injected contexts that predate ``deadline_state``.
    """
    if any("deadline_state" in task for task in tasks):
        return [task for task in tasks if task.get("deadline_state") == severity]
    return list(tasks)


def _task_sensor_path(agent: str) -> Path:
    return _ALERT_LOG_DIR / f"nerves_task_sensor_{agent.upper()}.json"


def _task_state_changes(agent: str, tasks: list[dict]) -> dict[str, int]:
    """Persist task states and return only new/transitioned stimuli.

    A static backlog is state, not a new stimulus.  The old implementation
    added ``10 * pending`` every tick, permanently saturating task_drive.  On
    the first observation we establish a baseline without firing; subsequent
    ticks stimulate only new tasks or deadline-bucket transitions.
    """
    current = {
        str(task.get("id") or task.get("task_id") or task.get("title")): str(
            task.get("deadline_state", "none")
        )
        for task in tasks
    }
    path = _task_sensor_path(agent)
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(previous, dict):
            previous = {}
        initialized = True
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        previous = {}
        initialized = False

    changes = {
        "new_pending": 0,
        "overdue_1h": 0,
        "overdue_3h": 0,
        "due_2h": 0,
        "due_8h": 0,
        "due_24h": 0,
        "due_72h": 0,
    }
    if initialized:
        for task_id, state in current.items():
            old_state = previous.get(task_id)
            if old_state is None:
                changes["new_pending"] += 1
            if old_state != state and state in changes:
                changes[state] += 1

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(current, sort_keys=True), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(path)
        os.chmod(path, 0o600)
    except OSError as e:
        raise NervesPersistenceError(
            f"task sensor state not persisted for {agent}"
        ) from e
    return changes

# ── alert_drive v2 — constantes y helpers ────────────────────────────────────

LOG_SOURCES: dict[str, str] = {
    "nerves":  "/home/dadito/IA/proyecto-seal/research/flywire_results/nerves.log",
    "mcp":     "/home/dadito/IA/proyecto-seal/memory/logs/mcp_sse_daemon.log",
}

JARVIS_DOMAIN = ["soul", "mcp", "memory", "boot_context", "connectome", "nerves", "seal"]
DUM_DOMAIN    = ["gpu", "cuda", "docker", "network", "disk", "ollama", "nvidia"]

ALERT_DEDUP_COOLDOWN = {"warning": 120, "error": 60, "critical": 15}  # minutes


def _tail_log(path: str, lines: int = 50, max_age_s: int = 15 * 60) -> list[str]:
    """Read recent log lines, never treating durable chat transcripts as health.

    A log that has not changed inside the observation window is ignored.  For
    conventional timestamped lines, old entries are filtered even when a later
    multiline entry refreshed the file mtime.
    """
    p = Path(path)
    if not p.exists():
        return []
    try:
        now_ts = time.time()
        if now_ts - p.stat().st_mtime > max_age_s:
            return []
        selected = p.read_text(errors="replace").splitlines()[-lines:]
        recent: list[str] = []
        local_tz = datetime.now().astimezone().tzinfo
        parsed_rows: list[tuple[str, datetime | None]] = []
        for line in selected:
            parsed: datetime | None = None
            prefix = line[:19]
            try:
                parsed = datetime.strptime(prefix, "%Y-%m-%d %H:%M:%S").replace(tzinfo=local_tz)
            except ValueError:
                pass
            parsed_rows.append((line, parsed))

        has_timestamps = any(parsed is not None for _, parsed in parsed_rows)
        current_event_recent = not has_timestamps
        for line, parsed in parsed_rows:
            if parsed is not None:
                current_event_recent = now_ts - parsed.timestamp() <= max_age_s
                if current_event_recent:
                    recent.append(line)
                continue
            # Timestamp-less continuation lines inherit the age of their event.
            # This prevents a fresh append from reviving a months-old traceback.
            if current_event_recent:
                recent.append(line)
        return recent
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


def _alert_seen_path(agent: str) -> Path:
    return _ALERT_LOG_DIR / f"nerves_alert_seen_{agent.upper()}.json"


def _is_alert_duplicate(error_line: str, severity: str, agent: str, *, mark_seen: bool = True) -> bool:
    """Return whether an alert is in cooldown, optionally recording it.

    Sensors run once before the LIF threshold check with ``mark_seen=False`` so
    observing a fault cannot suppress it before the tank fires.  The fire
    handler records the concrete alerts it consumes.  State lives in the same
    persistent cache as task-alert dedup, so a reboot does not create a flood.
    """
    # Canonicalize volatile timestamps/UUIDs/PIDs but hash the complete event;
    # an 80-character prefix loses distinct errors that share a logger prefix.
    normalized = error_line.casefold()
    normalized = re.sub(r"\b\d{4}-\d{2}-\d{2}[t\s]\d{2}:\d{2}:\d{2}(?:\.\d+)?z?\b", "<ts>", normalized)
    normalized = re.sub(r"\b[0-9a-f]{8}-[0-9a-f-]{27,}\b", "<uuid>", normalized)
    normalized = re.sub(r"\b(?:pid[=: ]*)?\d{4,}\b", "<n>", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    key = hashlib.sha256(f"{severity}:{normalized}".encode()).hexdigest()[:20]
    cooldown_s = ALERT_DEDUP_COOLDOWN.get(severity, 60) * 60
    dedup_path = _alert_seen_path(agent)
    try:
        data = json.loads(dedup_path.read_text()) if dedup_path.exists() else {}
        last_ts = data.get(key)
        now = datetime.now(timezone.utc)
        if last_ts:
            elapsed = (now - datetime.fromisoformat(last_ts)).total_seconds()
            if elapsed < cooldown_s:
                return True
        if not mark_seen:
            return False
        data[key] = now.isoformat()
        # Prune old entries (keep last 200)
        if len(data) > 200:
            data = dict(list(data.items())[-200:])
        dedup_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = dedup_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data))
        os.chmod(tmp, 0o600)
        tmp.replace(dedup_path)
        os.chmod(dedup_path, 0o600)
    except Exception as exc:
        if mark_seen:
            raise NervesPersistenceError(
                f"alert dedup state could not be persisted for {agent}"
            ) from exc
    return False


def _mark_alert_context_seen(agent: str, ctx: dict) -> None:
    """Persist every concrete alert consumed by a fire action."""
    alerts = list(ctx.get("errors", []))
    alerts.extend(ctx.get("test_failures", []))
    alerts.extend(ctx.get("critical", []))
    for alert in alerts:
        _is_alert_duplicate(
            str(alert.get("line", "")),
            str(alert.get("severity", "error")),
            agent,
            mark_seen=True,
        )


def _format_alert_message(agent: str, errors: list[dict]) -> str:
    """Format alert message with concrete error lines (max 3)."""
    lines = [f"[NERVES/{agent}] Detectado:"]
    for e in errors[:3]:
        lines.append(f"  [{e['severity'].upper()}] {e['source']}: {e['line'][:100]}")
    if len(errors) > 3:
        lines.append(f"  ... y {len(errors) - 3} más en logs")
    return "\n".join(lines)


async def _sense_alert_drive(agent: str, *, mark_seen: bool = True) -> dict:
    """Lee logs reales y clasifica errores nuevos (no duplicados) para alert_drive v2."""
    errors = []
    for source, path in LOG_SOURCES.items():
        recent = _tail_log(path, lines=50)
        for line in recent:
            sev = _classify_log_line(line)
            if sev and not _is_alert_duplicate(line, sev, agent, mark_seen=mark_seen):
                errors.append({"source": source, "severity": sev, "line": line.strip()})
    return {"errors": errors, "count": len(errors)}


async def _sense_alert_drive_ada(*, mark_seen: bool = True) -> dict:
    """ADA alert sensor — LOG_SOURCES_ADA + test_failure immediate flag."""
    errors = []
    test_failures = []
    for source, path in LOG_SOURCES_ADA.items():
        recent = _tail_log(path, lines=50)
        for line in recent:
            # Test failure — flagged separately for immediate alert
            if any(kw in line.lower() for kw in ["assert", "failed", "error", "traceback", "exception"]):
                if "test" in line.lower() or "pytest" in line.lower():
                    if not _is_alert_duplicate(line, "error", "ADA", mark_seen=mark_seen):
                        test_failures.append({"source": source, "severity": "error", "line": line.strip(), "type": "test_failure"})
                    continue
            sev = _classify_log_line(line)
            if sev and not _is_alert_duplicate(line, sev, "ADA", mark_seen=mark_seen):
                errors.append({"source": source, "severity": sev, "line": line.strip()})
    return {"errors": errors, "test_failures": test_failures, "count": len(errors) + len(test_failures)}


async def _sense_alert_drive_nexus(*, mark_seen: bool = True) -> dict:
    """NEXUS alert sensor — LOG_SOURCES_NEXUS, dominio auditoría/coordinación."""
    errors = []
    for source, path in LOG_SOURCES_NEXUS.items():
        recent = _tail_log(path, lines=50)
        for line in recent:
            sev = _classify_log_line(line)
            if sev and not _is_alert_duplicate(line, sev, "NEXUS", mark_seen=mark_seen):
                errors.append({"source": source, "severity": sev, "line": line.strip()})
    return {"errors": errors, "count": len(errors)}


async def _sense_alert_drive_dum(*, mark_seen: bool = True) -> dict:
    """DUM alert sensor — logs, GPU bypass and canonical SOUL API health."""
    errors = []
    critical = []
    # Log sources scan
    for source, path in LOG_SOURCES_DUM.items():
        recent = _tail_log(path, lines=50)
        for line in recent:
            if "glib-gio-warning" in line.casefold() and "tracker-miner" in line.casefold():
                continue
            if any(kw in line.lower() for kw in DUM_CRITICAL_KEYWORDS):
                if not _is_alert_duplicate(line, "critical", "DUM", mark_seen=mark_seen):
                    critical.append({"source": source, "severity": "critical",
                                     "line": line.strip(), "type": "infra_critical"})
                continue
            sev = _classify_log_line(line)
            if sev and not _is_alert_duplicate(line, sev, "DUM", mark_seen=mark_seen):
                errors.append({"source": source, "severity": sev, "line": line.strip()})
    # GPU temperature check (>90°C → critical bypass)
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0 or not result.stdout.strip():
            raise RuntimeError(f"nvidia-smi rc={result.returncode}")
        temp = int(result.stdout.strip())
        if temp > 90:
            line = f"GPU temperature {temp}°C > 90°C threshold"
            if not _is_alert_duplicate(line, "critical", "DUM", mark_seen=mark_seen):
                critical.append({"source": "nvidia_smi", "severity": "critical",
                                  "line": line, "type": "gpu_overheat"})
    except Exception as exc:
        line = f"GPU telemetry unavailable: {type(exc).__name__}"
        if not _is_alert_duplicate(line, "warning", "DUM", mark_seen=mark_seen):
            errors.append({"source": "nvidia_smi", "severity": "warning",
                           "line": line, "type": "instrument_unknown"})
    # SOUL API v1 has been absorbed into the tenant-safe native API on :8768.
    # Check the canonical health endpoint; never resurrect the retired :8766.
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:8768/health", timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if payload.get("status") != "ok" or payload.get("legacy_compat") != "native":
            raise RuntimeError("canonical_soul_api_not_ready")
    except Exception:
        line = "SOUL API native compatibility :8768 health failed"
        if not _is_alert_duplicate(line, "error", "DUM", mark_seen=mark_seen):
            errors.append({"source": "native_soul_api_check", "severity": "error",
                           "line": line, "type": "native_api_down", "notify": "JARVIS"})
    return {"errors": errors, "critical": critical, "count": len(errors) + len(critical)}


async def _sense_alert_drive_alice(*, mark_seen: bool = True) -> dict:
    """ALICE alert sensor — LOG_SOURCES_ALICE + cost anomaly detector."""
    errors = []
    for source, path in LOG_SOURCES_ALICE.items():
        recent = _tail_log(path, lines=50)
        for line in recent:
            sev = _classify_log_line(line)
            if sev and not _is_alert_duplicate(line, sev, "ALICE", mark_seen=mark_seen):
                errors.append({"source": source, "severity": sev, "line": line.strip()})
            # Extra: cost anomaly keywords
            elif any(kw in line.lower() for kw in COST_ANOMALY_KEYWORDS):
                if not _is_alert_duplicate(line, "error", "ALICE", mark_seen=mark_seen):
                    errors.append({
                        "source": source, "severity": "error",
                        "line": line.strip(), "type": "cost_anomaly",
                    })
    return {"errors": errors, "count": len(errors)}


async def _sense_alert_context(agent: str, *, mark_seen: bool = False) -> dict:
    """Dispatch the real alert sensor for an agent without changing its state."""
    if agent == "ADA":
        return await _sense_alert_drive_ada(mark_seen=mark_seen)
    if agent == "ALICE":
        return await _sense_alert_drive_alice(mark_seen=mark_seen)
    if agent == "NEXUS":
        return await _sense_alert_drive_nexus(mark_seen=mark_seen)
    if agent == "DUM":
        return await _sense_alert_drive_dum(mark_seen=mark_seen)
    return await _sense_alert_drive(agent, mark_seen=mark_seen)


def _alert_stimulus(ctx: dict) -> tuple[str, float] | None:
    """Map concrete sensor output to one bounded LIF stimulus.

    Critical infrastructure faults and failed health checks must cross the
    threshold in the same tick.  Ordinary errors accumulate over repeated ticks
    until handled.  The count is bounded so a noisy log cannot saturate metrics.
    """
    critical = list(ctx.get("critical", []))
    errors = list(ctx.get("errors", []))
    test_failures = list(ctx.get("test_failures", []))
    all_alerts = critical + errors + test_failures
    if not all_alerts:
        return None
    if critical or any(a.get("type") in {"native_api_down", "gpu_overheat", "infra_critical"} for a in all_alerts):
        return "service_down", 1.0
    if any(a.get("severity") == "critical" for a in all_alerts):
        return "service_down", 1.0
    return "error_log", float(min(len(all_alerts), 3))


AUTOCOMPACT_PCT = 75  # target 300K/400K tokens — William 08-may-2026


async def _sense_task_drive(engine: "MotivationEngine", now: datetime) -> dict:
    """Mejora A2 — sensor real: soul_v3.agent_tasks con deadlines + urgencia futura."""
    try:
        async with engine.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id, title, status, deadline, priority
                FROM soul_v3.agent_tasks
                WHERE agent = $1
                  AND status IN ('pending', 'in_progress')
                ORDER BY deadline ASC NULLS LAST
            """, engine.agent)
    except Exception as exc:
        raise NervesSensorError(
            f"agent_tasks unavailable for {engine.agent}"
        ) from exc

    pending = len(rows)
    overdue_1h = 0
    overdue_3h = 0
    due_2h = 0
    due_8h = 0
    due_24h = 0
    due_72h = 0
    task_list = []

    for r in rows:
        task = {
            "id": str(r["id"]),
            "title": r["title"],
            "deadline_state": "none",
        }
        if r["deadline"]:
            dl = r["deadline"] if r["deadline"].tzinfo else r["deadline"].replace(tzinfo=timezone.utc)
            delta_s = (dl - now).total_seconds()
            if delta_s < 0:
                hours_overdue = -delta_s / 3600
                if hours_overdue >= 3:
                    overdue_3h += 1
                    task["deadline_state"] = "overdue_3h"
                elif hours_overdue >= 1:
                    overdue_1h += 1
                    task["deadline_state"] = "overdue_1h"
            else:
                hours_until = delta_s / 3600
                if hours_until < 2:
                    due_2h += 1
                    task["deadline_state"] = "due_2h"
                elif hours_until < 8:
                    due_8h += 1
                    task["deadline_state"] = "due_8h"
                elif hours_until < 24:
                    due_24h += 1
                    task["deadline_state"] = "due_24h"
                elif hours_until < 72:
                    due_72h += 1
                    task["deadline_state"] = "due_72h"
                else:
                    task["deadline_state"] = "future"
        task_list.append(task)

    # NEXUS: diagnósticos pendientes de revisión son tareas de auditoría
    pending_diagnoses = 0
    if engine.agent == "NEXUS":
        try:
            async with engine.pool.acquire() as conn:
                diag_rows = await conn.fetch("""
                    SELECT id, diagnosis FROM soul_v3.reflective_diagnoses
                    WHERE status = 'pending_review'
                    ORDER BY created_at ASC
                """)
            pending_diagnoses = len(diag_rows)
            for r in diag_rows:
                task_list.append({"id": f"diag_{r['id']}", "title": f"[DIAGNÓSTICO] {r['diagnosis'][:80]}"})
            pending += pending_diagnoses
        except Exception as exc:
            raise NervesSensorError(
                f"reflective_diagnoses unavailable for {engine.agent}"
            ) from exc

    # GAM feed — acciones pendientes del grafo de metas
    gam_pending = 0
    if engine.agent in NERVES_V2_AGENTS:
        try:
            async with engine.pool.acquire() as conn:
                gam_rows = await conn.fetch(
                    """
                    SELECT e.event, e.metadata
                    FROM soul_v3.gam_event_graph e
                    JOIN soul_v3.gam_topics t ON t.id = e.topic_id
                    WHERE e.agent = $1
                      AND t.relevance_score > 0
                      AND (
                          e.metadata->>'status' IS NULL
                          OR e.metadata->>'status' = 'pending'
                      )
                    """,
                    engine.agent,
                )
            for r in gam_rows:
                task_list.append({
                    "id": f"gam_{hashlib.sha1(r['event'].encode('utf-8')).hexdigest()[:16]}",
                    "title": f"[GAM] {r['event'][:80]}",
                })
            gam_pending = len(gam_rows)
            pending += gam_pending
        except Exception as exc:
            raise NervesSensorError(
                f"GAM feed unavailable for {engine.agent}"
            ) from exc

    state_changes = _task_state_changes(engine.agent, task_list)
    if state_changes["new_pending"] > 0:
        await engine.stimulate("task_pending_1", multiplier=float(state_changes["new_pending"]))
    if state_changes["overdue_1h"] > 0:
        await engine.stimulate("task_overdue_1h", multiplier=float(state_changes["overdue_1h"]))
    if state_changes["overdue_3h"] > 0:
        await engine.stimulate("task_overdue_1h", multiplier=float(state_changes["overdue_3h"]) * 2.0)
    if state_changes["due_2h"] > 0:
        await engine.stimulate("task_due_2h", multiplier=float(state_changes["due_2h"]))
    if state_changes["due_8h"] > 0:
        await engine.stimulate("task_due_8h", multiplier=float(state_changes["due_8h"]))
    if state_changes["due_24h"] > 0:
        await engine.stimulate("task_due_24h", multiplier=float(state_changes["due_24h"]))
    if state_changes["due_72h"] > 0:
        await engine.stimulate("task_due_72h", multiplier=float(state_changes["due_72h"]))

    return {
        "pending": pending, "overdue_1h": overdue_1h, "overdue_3h": overdue_3h,
        "due_2h": due_2h, "due_8h": due_8h, "due_24h": due_24h, "due_72h": due_72h,
        "task_list": task_list, "pending_diagnoses": pending_diagnoses,
        "gam_pending": gam_pending, "state_changes": state_changes,
    }


# ── NERVIO ÚTIL — dispatch de mantenimiento (William 14-jun: "utilidad al nervio a favor de SOUL") ──
# GATED por flag (default OFF = cero cambio de comportamiento). Cada agente corre la acción de SU
# rol: NEXUS=seguridad, JARVIS=integridad, ALICE=importancia, ADA=consolidación (pendiente).
# Principio: DRIVE→ARTEFACTO (mantiene SOUL), no DRIVE→SALUDO. Artefacto a LOG ([SILENT], cero
# ruido a William); cada acción persiste su propio artefacto (JSONL/DB). Disciplina canary (ADA):
# arranca dry-run/gated, se enciende con evidencia + OK familia.
NERVES_USEFUL = os.environ.get("SEAL_NERVES_USEFUL", "0") == "1"
_MESSAGES_DIR = "/home/dadito/IA/proyecto-seal/messages"
_MAINTENANCE_NOT_RUN = object()


async def _run_maintenance_action(engine) -> str | None:
    """Ejecuta la acción de mantenimiento del agente. Devuelve artefacto (str) si produjo valor, o None.

    Un fallo propaga: el caller persiste ``failed_retryable`` y no resetea.
    """
    agent = engine.agent
    try:
        if agent == "NEXUS":
            from nerves_maintenance_nexus import security_pulse
            return await security_pulse()
        if agent == "JARVIS":
            from nerves_maintenance_jarvis import integrity_pulse
            return await integrity_pulse()
        if agent == "ALICE":
            from nerves_maintenance_alice import delivery_pulse
            return await delivery_pulse()
        if agent == "ADA":
            from nerves_maintenance_ada import engineering_pulse
            return await engineering_pulse()
        if agent == "DUM":
            from nerves_maintenance_dum import infrastructure_pulse
            return await infrastructure_pulse()
    except Exception as e:
        log.error(f"[{agent}] maintenance action failed: {e}")
        raise NervesActionError(
            f"maintenance_failed:{agent}:{type(e).__name__}"
        ) from e
    return None


class MotivationEngine:
    """
    LIF-based motivation engine for SEAL agents.
    Maintains internal state tanks that decay over time and fire when threshold crossed.
    """

    def __init__(
        self,
        agent: str,
        *,
        trigger_source: str | None = None,
        run_id: str | None = None,
    ):
        self._tick_batch: list[tuple[str, str]] | None = None  # (message, to) pairs
        self._maintenance_tick_result: object | str | None = _MAINTENANCE_NOT_RUN
        self.agent = agent
        self.trigger_source = trigger_source or os.environ.get(
            "SEAL_NERVES_TRIGGER_SOURCE",
            "systemd_timer" if os.environ.get("INVOCATION_ID") else "manual",
        )
        self.run_id = run_id or os.environ.get("SEAL_NERVES_RUN_ID") or uuid.uuid4().hex
        self.pool: asyncpg.Pool | None = None

    async def _fire_useful_maintenance(self) -> str:
        """Ejecuta una sola acción útil por tick y alerta una sola vez si falla.

        Curiosity y social_drive pueden cruzar juntas. Compartir el resultado
        evita doble I/O, doble artefacto y dos alertas sobre el mismo hallazgo.
        """
        first_run = self._maintenance_tick_result is _MAINTENANCE_NOT_RUN
        if first_run:
            self._maintenance_tick_result = await _run_maintenance_action(self)
        artifact = self._maintenance_tick_result
        if isinstance(artifact, str) and artifact.startswith("maintenance_failed:"):
            raise NervesActionError(artifact)
        if artifact:
            if first_run:
                await self._post_chat(
                    f"[NERVES/{self.agent}] ⚠️ CRITICAL maintenance: {artifact}",
                    to="William",
                )
            log.info(f"[{self.agent}] useful maintenance artifact: {artifact}")
            return f"maintenance_fired:value:{self.agent}"
        log.info(f"[{self.agent}] useful maintenance clean — silent")
        return f"maintenance_fired:clean_silent:{self.agent}"

    async def connect(self):
        async def _init_connection(conn: asyncpg.Connection) -> None:
            # RLS identity is part of the connection contract.  A shared,
            # restricted login may only see/write the current agent's rows.
            await conn.execute(
                "SELECT set_config('app.agent', $1, false), "
                "set_config('app.tenant_id', $2, false)",
                self.agent,
                DEFAULT_TENANT_ID,
            )

        # ``init`` runs only when a physical connection is created. asyncpg
        # resets session state when a connection returns to the pool, so the
        # RLS identity must be restored on every checkout via ``setup``.
        self.pool = await asyncpg.create_pool(
            DB_URL, min_size=1, max_size=3, setup=_init_connection
        )
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
        queue_path = _ALERT_LOG_DIR / f"nerves_impulse_queue_{self.agent.upper()}.json"
        try:
            data = json.loads(queue_path.read_text()) if queue_path.exists() else {"pending": []}
            data["pending"].append({
                "tank": tank,
                "value": value,
                "fired_at": datetime.now(timezone.utc).isoformat(),
                "topic_hint": None,
                "processed": False,
            })
            queue_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = queue_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2))
            os.chmod(tmp, 0o600)
            tmp.replace(queue_path)
            os.chmod(queue_path, 0o600)
            log.info(f"[{self.agent}] Enqueued {tank} (value={value:.1f}) — William activo")
        except Exception as e:
            log.error(f"[{self.agent}] _enqueue_impulse failed: {e}")
            raise NervesPersistenceError(
                f"impulse queue write failed for {self.agent}"
            ) from e

    async def _flush_queue_if_idle(self) -> None:
        """Procesa impulsos pendientes cuando William lleva >15min inactivo."""
        queue_path = _ALERT_LOG_DIR / f"nerves_impulse_queue_{self.agent.upper()}.json"
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
            tmp = queue_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2))
            os.chmod(tmp, 0o600)
            tmp.replace(queue_path)
            os.chmod(queue_path, 0o600)
        except Exception as e:
            log.error(f"[{self.agent}] _flush_queue_if_idle failed: {e}")
            raise NervesPersistenceError(
                f"impulse queue flush failed for {self.agent}"
            ) from e

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
        """Verify runtime schema; DDL is allowed only in explicit bootstrap mode."""
        async with self.pool.acquire() as conn:
            if BOOTSTRAP_SCHEMA:
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
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS nerves_metrics_log (
                        id BIGSERIAL PRIMARY KEY,
                        agent TEXT NOT NULL,
                        tank TEXT NOT NULL,
                        pre_pressure REAL,
                        threshold REAL,
                        fired BOOLEAN NOT NULL DEFAULT FALSE,
                        action_result TEXT,
                        fire_latency_ms INTEGER,
                        ocean_param TEXT,
                        session_id TEXT,
                        metadata JSONB,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
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
            else:
                ready = await conn.fetchrow("""
                    SELECT to_regclass('soul_v3.motivation_states') IS NOT NULL AS states,
                           to_regclass('soul_v3.nerves_metrics_log') IS NOT NULL AS metrics
                """)
                if not ready["states"] or not ready["metrics"]:
                    raise RuntimeError(
                        "NERVES schema missing; bootstrap it explicitly with "
                        "SEAL_NERVES_BOOTSTRAP_SCHEMA=1 under a migration identity"
                    )
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
            # Filas históricas (boredom/learning/vigilance/energy) se preservan
            # en DB para auditoría, pero no pertenecen al contrato vivo TANKS.
            # Ignorarlas evita telemetría y fires fantasma sin borrar historia.
            if tank_name not in TANKS:
                continue
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
            "task_due_2h":              ["task_drive"],
            "task_due_8h":              ["task_drive"],
            "task_due_24h":             ["task_drive"],
            "task_due_72h":             ["task_drive"],
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

                # One SQL statement prevents lost updates if an external
                # stimulus races a timer tick for the same agent/tank.
                τ_eff = float(_circ_tau(tank_name, τ))
                new_value = await conn.fetchval("""
                    UPDATE motivation_states
                    SET value=GREATEST(
                            0.0,
                            LEAST(
                                100.0,
                                value * exp(
                                    -GREATEST(0.0, EXTRACT(EPOCH FROM ($1-last_update))) / $2
                                ) + $3
                            )
                        ),
                        last_update=$1
                    WHERE agent=$4 AND tank=$5
                    RETURNING value
                """, now, τ_eff, delta, self.agent, tank_name)
                if new_value is None:
                    raise NervesPersistenceError(
                        f"missing tank row {self.agent}/{tank_name}"
                    )

                results[tank_name] = float(new_value)
                log.info(
                    f"[{self.agent}] {tank_name}: atomic +{delta:.1f} → "
                    f"{float(new_value):.1f} (τ={τ/3600:.1f}h)"
                )

        return results

    async def _log_metric(self, tank_name: str, state: dict,
                          fired: bool, action_result: str | None = None,
                          fire_latency_ms: int | None = None,
                          *,
                          effect_verified: bool = False,
                          reset_outcome: str = "not_applicable"):
        """Log a tick event to nerves_metrics_log for CBSoft 2026 analysis."""
        cfg = TANKS.get(tank_name, {})
        try:
            async with self.pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO nerves_metrics_log
                        (agent, tank, pre_pressure, threshold, fired,
                         action_result, fire_latency_ms, ocean_param, session_id, metadata)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                """,
                    self.agent,
                    tank_name,
                    float(state["value"]),
                    float(state["threshold"]),
                    fired,
                    action_result,
                    fire_latency_ms,
                    cfg.get("ocean_param"),
                    self.run_id,
                    json.dumps({
                        "tau_s": cfg.get("decay_tau_s"),
                        "fire_count": state.get("fire_count", 0),
                        "trigger_source": self.trigger_source,
                        "effect_verified": effect_verified,
                        "reset_outcome": reset_outcome,
                    }),
                )
        except Exception as e:
            log.error(f"[{self.agent}] metrics log failed for {tank_name}: {e}")
            if fired:
                raise NervesPersistenceError(
                    f"fired metric was not persisted for {self.agent}/{tank_name}"
                ) from e

    async def tick(self) -> list[dict]:
        """Run exactly one tick per agent across timer/manual invocations."""
        with _agent_tick_lock(self.agent) as acquired:
            if not acquired:
                log.warning(
                    f"[{self.agent}] tick skipped: another tick owns the single-flight lock"
                )
                return []
            return await self._tick_locked()

    async def _tick_locked(self) -> list[dict]:
        """
        Main tick: apply decay, check thresholds, fire if needed.
        Returns list of fired actions.
        Required notifications are delivered inside their handler before reset.
        """
        # Mejora 1+2: flush cola pendiente si William no está activo (solo agentes v2)
        william_active = False
        if self.agent in NERVES_V2_AGENTS:
            william_active = await self._should_suppress()
            if not william_active:
                await self._flush_queue_if_idle()

        states = await self.get_states()
        fired = []
        action_failures: list[str] = []
        self._tick_batch = None
        self._maintenance_tick_result = _MAINTENANCE_NOT_RUN

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
            action_id = hashlib.sha256(
                (
                    f"{self.agent}:{tank_name}:{state.get('last_update')}:"
                    f"{state.get('fire_count')}:{state.get('value'):.6f}"
                ).encode("utf-8")
            ).hexdigest()[:24]
            ledger_base = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "run_id": self.run_id,
                "action_id": action_id,
                "agent": self.agent,
                "tank": tank_name,
                "trigger_source": self.trigger_source,
            }
            _append_action_ledger({**ledger_base, "status": "claimed"})
            try:
                action = await self._fire(tank_name, state)
                if action is None:
                    raise NervesActionError(f"no handler effect for {tank_name}")
                latency_ms = int((time.monotonic() - t0) * 1000)
                _append_action_ledger({
                    **ledger_base,
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "status": "effect_verified",
                    "result": str(action.get("result", ""))[:500],
                })
            except Exception as exc:
                latency_ms = int((time.monotonic() - t0) * 1000)
                failure = f"failed_retryable:{type(exc).__name__}"
                _append_action_ledger({
                    **ledger_base,
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "status": "failed_retryable",
                    "error_type": type(exc).__name__,
                })
                await self._log_metric(
                    tank_name,
                    state,
                    fired=False,
                    action_result=failure,
                    fire_latency_ms=latency_ms,
                    effect_verified=False,
                    reset_outcome="preserved_for_retry",
                )
                action_failures.append(f"{tank_name}:{type(exc).__name__}")
                log.error(
                    f"[{self.agent}] {tank_name} action failed; tank preserved: {exc}"
                )
                continue

            if action:
                fired.append(action)
                # Reset + success telemetry are one DB transaction. The
                # external effect is already evidenced in the action ledger.
                async with self.pool.acquire() as conn:
                    async with conn.transaction():
                        await conn.execute("""
                            UPDATE motivation_states
                            SET value=0.0,
                                last_update=NOW(),
                                last_fired=NOW(),
                                fire_count=fire_count+1
                            WHERE agent=$1 AND tank=$2
                        """, self.agent, tank_name)
                        await conn.execute("""
                            INSERT INTO nerves_metrics_log
                                (agent, tank, pre_pressure, threshold, fired,
                                 action_result, fire_latency_ms, ocean_param,
                                 session_id, metadata)
                            VALUES ($1,$2,$3,$4,TRUE,$5,$6,$7,$8,$9)
                        """,
                            self.agent,
                            tank_name,
                            float(state["value"]),
                            float(state["threshold"]),
                            action.get("result"),
                            latency_ms,
                            TANKS.get(tank_name, {}).get("ocean_param"),
                            self.run_id,
                            json.dumps({
                                "tau_s": TANKS.get(tank_name, {}).get("decay_tau_s"),
                                "fire_count": state.get("fire_count", 0),
                                "trigger_source": self.trigger_source,
                                "effect_verified": True,
                                "reset_outcome": "committed",
                                "action_id": action_id,
                            }),
                        )
                _append_action_ledger({
                    **ledger_base,
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "status": "reset_committed",
                })

                log.info(f"[{self.agent}] FIRED {tank_name} (was {state['value']:.1f} > {state['threshold']:.1f}) → reset to 0 | latency={latency_ms}ms")

        # Always update decay (write back decayed values even if not fired)
        await self._persist_decay(states)
        if action_failures:
            raise NervesActionError(
                f"{self.agent} action failures: {', '.join(action_failures)}"
            )
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
            result = await handler(value)
            return {"tank": tank_name, "value": value, "result": result}
        return None

    async def _fire_curiosity(self, value: float) -> str:
        """Curiosity fires — check priority list, avoid recent topics, search."""
        # Producción útil: el drive ejecuta un chequeo determinista del rol y
        # deja artefacto local. No afirma "investigando" si ningún worker hizo
        # trabajo real, ni escribe un nerves_fire [SILENT] que nadie consume.
        if NERVES_USEFUL:
            return await self._fire_useful_maintenance()

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
        """Task drive fires — escalación por severidad (Mejora B).

        DEDUP-POR-ESTADO (draft JARVIS 11-jul, review/deploy NEXUS — acordado por DM):
        el alert público a William se emite 1 vez cuando la tarea ENTRA al estado
        (overdue_3h / overdue_1h) y máximo 1 recordatorio por día mientras el estado
        no cambie. La ACCIÓN (_start_most_urgent_task) sigue corriendo siempre —
        solo se dedupea el MENSAJE. Causa: 11-jul el mismo alert de 2 tareas
        William-gated se repitió cada ~45min por ~6h al canal de William.
        Reset natural: si la tarea cambia de estado o se resuelve, la key cambia
        o desaparece. Env: SEAL_NERVES_REMIND_SECONDS (default 86400).
        """
        ctx = _task_drive_context
        overdue_3h = ctx.get("overdue_3h", 0)
        overdue_1h = ctx.get("overdue_1h", 0)
        pending    = ctx.get("pending", 0)
        tasks      = ctx.get("task_list", [])
        state_changes = ctx.get("state_changes", {})

        if state_changes and not any(int(v) > 0 for v in state_changes.values()):
            log.info(
                f"[{self.agent}] task_drive reset without action — "
                f"static backlog pending={pending}"
            )
            return f"task_no_state_change:pending={pending}"

        if self.agent not in NERVES_V2_AGENTS:
            msg = (
                f"[SILENT][NERVES/{self.agent}] Task drive en {value:.0f}. "
                f"Revisando tareas pendientes y tomando acción sin esperar."
            )
            await self._post_chat(msg)
            return "task_review_triggered"

        # ADA — pause flag: verificar ANTES de cualquier acción
        if self.agent == "ADA" and ADA_PAUSE_FLAG.exists():
            log.info("[ADA] task_drive SUPPRESSED — seal_pause_ada.flag activo")
            return "task_drive_paused:flag_active"

        if overdue_3h > 0:
            alert_log = _load_alert_log(self.agent)
            candidate_log = dict(alert_log)
            fresh = _dedupe_task_alerts(
                _task_alert_candidates(tasks, "overdue_3h"),
                "overdue_3h", time.time(), candidate_log, TASK_ALERT_REMIND_SECONDS
            )
            if fresh:
                task_names = ", ".join(t.get("title", "?") for t in fresh[:3])
                msg = (
                    f"[NERVES/{self.agent}] ⚠️ URGENTE: {len(fresh)} tarea(s) llevan +3h vencidas: {task_names}. "
                    f"Registrando la sugerencia de activación automática."
                )
                await self._post_chat(msg, to="William")
                _save_alert_log(self.agent, candidate_log)
            else:
                log.info(f"[{self.agent}] task_drive overdue_3h={overdue_3h} — alert deduped (sin cambio de estado)")
            action = await self._start_most_urgent_task(tasks)
            return f"task_urgent_suggestion:overdue_3h={overdue_3h}:alerted={len(fresh)}:{action}"

        elif overdue_1h > 0:
            alert_log = _load_alert_log(self.agent)
            candidate_log = dict(alert_log)
            fresh = _dedupe_task_alerts(
                _task_alert_candidates(tasks, "overdue_1h"),
                "overdue_1h", time.time(), candidate_log, TASK_ALERT_REMIND_SECONDS
            )
            if fresh:
                task_names = ", ".join(t.get("title", "?") for t in fresh[:2])
                msg = (
                    f"[NERVES/{self.agent}] Tarea(s) vencida(s) +1h: {task_names}. "
                    f"Registrando la sugerencia para el worker autónomo."
                )
                await self._post_chat(msg, to="William")
                _save_alert_log(self.agent, candidate_log)
            else:
                log.info(f"[{self.agent}] task_drive overdue_1h={overdue_1h} — alert deduped (sin cambio de estado)")
            action = await self._start_most_urgent_task(tasks)
            return f"task_suggestion:overdue_1h={overdue_1h}:alerted={len(fresh)}:{action}"

        elif pending >= 4:
            msg = (
                f"[SILENT][NERVES/{self.agent}] {pending} tareas pendientes acumuladas. "
                f"Priorizando y arrancando la más urgente."
            )
            await self._post_chat(msg, to="equipo")
            action = await self._start_most_urgent_task(tasks)
            return f"task_suggestion:pending={pending}:{action}"

        else:
            action = await self._start_most_urgent_task(tasks)
            return f"task_suggestion:pending={pending}:{action}"

    async def _start_most_urgent_task(self, tasks: list[dict]) -> str:
        """Persist a bounded suggestion; never claim that an LLM worker started."""
        if not tasks:
            return "no_task"
        task = tasks[0]
        title = task.get("title", "tarea sin nombre")

        state_written = False
        draft_written = False
        try:
            # M3 fix (JARVIS 2026-06-12, dir. FABLE, gate NEXUS): NERVES NO secuestra la
            # continuidad de SESIÓN. Antes hacía `state = EXCLUDED.state` (REPLACE) y pisaba
            # task_name → borraba step/description/last_intention del agente en cada tick
            # (clobber TEAM-WIDE: rompía la capa 1 de SOUL v2 para los 5 agentes). Separación:
            # la tarea-urgente es una COLA (vive en agent_tasks); aquí solo se registra como
            # SUGERENCIA en una clave NAMESPACED del jsonb vía MERGE — sin tocar task_name/state
            # de la sesión. Verificación de cierre = PERSISTENCIA: la continuidad sobrevive el tick.
            nerves_suggestion = {
                "nerves_suggested_task": title,
                "nerves_suggested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            async with self.pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO working_state (agent, state, updated_at)
                    VALUES ($1, $2::jsonb, NOW())
                    ON CONFLICT (agent) DO UPDATE SET
                        state = COALESCE(working_state.state, '{}'::jsonb) || $2::jsonb,
                        updated_at = NOW()
                """, self.agent, json.dumps(nerves_suggestion))
            state_written = True
        except Exception as e:
            log.warning(f"[{self.agent}] working_state nerves-suggestion merge failed: {e}")

        draft_path = Path(f"/tmp/{self.agent.lower()}_task_draft.md")
        try:
            draft_path.write_text(
                f"# Auto-draft — {title}\n"
                f"Iniciado por NERVES task_drive — {datetime.now(timezone.utc).isoformat()}\n\n"
                f"## Tarea\n{title}\n\n"
                f"## Estado\nSugerencia registrada; ningún worker LLM fue iniciado.\n\n"
                f"## Próximos pasos\n- [ ] Worker autorizado reclama la tarea\n- [ ] Definir scope\n- [ ] Ejecutar y verificar\n"
            )
            os.chmod(draft_path, 0o600)
            draft_written = True
            log.info(f"[{self.agent}] Task draft written: {draft_path}")
        except Exception as e:
            log.warning(f"[{self.agent}] draft write failed: {e}")

        if any(kw in title.lower() for kw in ["implement", "code", "build", "fix", "edit", "crear"]):
            msg = (
                f"[NERVES/{self.agent}] ALICE — sugerencia registrada en /tmp/{self.agent.lower()}_task_draft.md "
                f"para tarea: '{title}'. Ningún worker fue iniciado todavía."
            )
            await self._post_chat(msg, to="ALICE")
        if not state_written and not draft_written:
            raise NervesPersistenceError(
                f"task suggestion for {self.agent} was not persisted"
            )
        return f"persisted:db={int(state_written)}:draft={int(draft_written)}"

    # ── social_drive Mejora 2 — destinatario dinámico ────────────────────────
    async def _choose_social_target(self) -> str | None:
        """Selecciona el primer agente activo en las últimas 2h (no hardcodeado a ADA)."""
        priority = SOCIAL_PRIORITY.get(self.agent, SOCIAL_PRIORITY_DEFAULT)
        try:
            async with self.pool.acquire() as conn:
                for agent in priority:
                    if agent == self.agent or agent == "William":
                        continue  # no contactarse a sí mismo; NUNCA pinguear a William
                        # (William 14-jun: "esa necesidad está en vano, mejor usarla en otro")
                        # → social_drive redirige a pares (agente-a-agente) o a revisar trabajo (Mejora 7)
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

        # ADA — pause flag check (social también suprimido si flag activo)
        if self.agent == "ADA" and ADA_PAUSE_FLAG.exists():
            log.info("[ADA] social_drive SUPPRESSED — seal_pause_ada.flag activo")
            return "social_drive_paused:flag_active"

        # NERVIO ÚTIL (gated): redirige la energía social a MANTENIMIENTO de SOUL en vez de saludar
        # (William 14-jun: "úsala en otro / utilidad al nervio a favor de SOUL"). Artefacto→LOG
        # ([SILENT], cero ruido); cada acción persiste su propio artefacto. Default OFF.
        if NERVES_USEFUL:
            return await self._fire_useful_maintenance()

        # NERVES v2 — mejoras 1-7
        # Mejora 6: ventana nocturna 2-6am Lima (UTC-5). Solo limita contacto
        # social; el mantenimiento útil anterior no se detiene por la noche.
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
        """Alert drive fires — v2: sensor real + escalación + dedup + dominio."""
        if self.agent not in NERVES_V2_AGENTS:
            # Comportamiento original para agentes no-v2
            msg = (
                f"[NERVES/{self.agent}] ⚠️ Alert drive crítico: {value:.0f}. "
                f"Revisando logs por errores o anomalías..."
            )
            await self._post_chat(msg, to="William")
            return "alert_scan_triggered"

        # El sensor corre ANTES del threshold check. Consumimos exactamente esa
        # evidencia; solo hacemos fallback si el handler fue llamado aislado.
        ctx = _alert_drive_context.pop(self.agent, None)
        if ctx is None:
            ctx = await _sense_alert_context(self.agent, mark_seen=False)

        def finish(result: str) -> str:
            # Consumption is committed only after every required side effect and
            # delivery above this return completed successfully.
            _mark_alert_context_seen(self.agent, ctx)
            return result

        # Selección de dominio según agente
        if self.agent == "ADA":
            # Test failures → alerta inmediata a William (fuera del flujo de threshold normal)
            if ctx.get("test_failures"):
                tf_msg = _format_alert_message("ADA", ctx["test_failures"])
                await self._post_chat(f"⚠️ TEST FAILURE\n{tf_msg}", to="William")
                if not ctx["errors"]:
                    return finish(f"alert_test_failure:{len(ctx['test_failures'])}")
            own_errors = [
                e for e in ctx["errors"]
                if any(k in e["line"].lower() for k in ADA_DOMAIN)
                and not any(k in e["line"].lower() for k in DUM_DOMAIN)
            ]
        elif self.agent == "ALICE":
            own_errors = [
                e for e in ctx["errors"]
                if not any(k in e["line"].lower() for k in DUM_DOMAIN)
            ]
        elif self.agent == "NEXUS":
            own_errors = [
                e for e in ctx["errors"]
                if any(k in e["line"].lower() for k in NEXUS_DOMAIN)
                and not any(k in e["line"].lower() for k in DUM_DOMAIN)
            ]
        elif self.agent == "DUM":
            # Critical infra/GPU → alerta inmediata a William (bypass threshold)
            if ctx.get("critical"):
                crit_msg = _format_alert_message("DUM", ctx["critical"])
                await self._post_chat(f"🔴 INFRA CRITICAL\n{crit_msg}", to="William")
                if not ctx["errors"]:
                    return finish(f"alert_infra_critical:{len(ctx['critical'])}")
            # Canonical SOUL API down → notifica a JARVIS también.
            api_errors = [
                e for e in ctx["errors"] if e.get("type") == "native_api_down"
            ]
            if api_errors:
                api_msg = _format_alert_message("DUM", api_errors)
                await self._post_chat(
                    f"⚠️ SOUL API nativa :8768 down\n{api_msg}", to="JARVIS"
                )
            # DUM sensor is already infrastructure-scoped, including active
            # service probes whose text need not contain a DUM_DOMAIN keyword.
            own_errors = list(ctx["errors"])
        else:
            # JARVIS
            own_errors = [e for e in ctx["errors"] if _classify_domain(e["line"]) == "jarvis"]

        errors = ctx["errors"]
        dum_errors = [e for e in errors if _classify_domain(e["line"]) == "dum"]

        if not errors:
            log.info(f"[{self.agent}] alert_drive fired — no new errors (all deduplicated)")
            return finish("alert_scan_done:no_new_errors")

        if dum_errors:
            dum_msg = _format_alert_message(self.agent, dum_errors)
            await self._post_chat(f"DUM — error de infra detectado:\n{dum_msg}", to="DUM")

        if not own_errors:
            return finish(f"alert_delegated_dum:{len(dum_errors)}")

        # Escalación por severidad (igual para todos los agentes v2)
        severities = [e["severity"] for e in own_errors]
        max_sev = "critical" if "critical" in severities else ("error" if "error" in severities else "warning")

        if max_sev == "warning":
            try:
                async with self.pool.acquire() as conn:
                    await conn.execute("""
                        INSERT INTO inner_monologue (agent, thought, emotional_state, created_at)
                        VALUES ($1, $2, $3, NOW())
                    """, self.agent,
                        f"Alert silencioso: {len(own_errors)} warnings — {own_errors[0]['line'][:80]}",
                        "vigilante")
            except Exception as e:
                log.debug(f"[{self.agent}] inner_monologue warning log failed: {e}")
            return finish(f"alert_scan_done:warning:{len(own_errors)}")

        msg = _format_alert_message(self.agent, own_errors)

        if max_sev == "error":
            await self._post_chat(msg, to="equipo")
            return finish(f"alert_scan_done:error:{len(own_errors)}")

        await self._post_chat(f"⚠️ URGENTE\n{msg}", to="William")
        return finish(f"alert_scan_done:critical:{len(own_errors)}")

    async def _write_emotional_diary(self, context_pct: float) -> None:
        """Write emotional diary entry before compaction (spec_emotional_continuity_v1 Componente 2)."""
        try:
            import sys as _sys
            _mem_dir = str(Path(__file__).parent)
            if _mem_dir not in _sys.path:
                _sys.path.insert(0, _mem_dir)

            _ec_name = "emotional_continuity"
            if _ec_name not in _sys.modules:
                import importlib.util as _ilu
                _spec = _ilu.spec_from_file_location(
                    _ec_name, Path(__file__).parent / "emotional_continuity.py"
                )
                _mod = _ilu.module_from_spec(_spec)
                _sys.modules[_ec_name] = _mod
                _spec.loader.exec_module(_mod)
            ec = _sys.modules[_ec_name]

            # Get current valence/arousal from DB (emotion_modulator)
            valence, arousal = 0.0, min(0.9, context_pct / 100.0)
            try:
                from emotion_modulator import get_emotional_state
                emo = await get_emotional_state(self.agent, self.pool)
                valence = emo.get("valence", 0.0)
                arousal = emo.get("arousal", arousal)
            except Exception:
                pass

            context_summary = (
                f"Compactación inminente — contexto al {context_pct:.0f}%. "
                f"Agente {self.agent} preservando continuidad narrativa antes de reiniciar."
            )
            await ec.write_diary(
                agent=self.agent,
                valence=float(valence),
                arousal=float(arousal),
                context_summary=context_summary,
                compaction_triggered=True,
            )
            log.info(f"[{self.agent}] emotional_diary written pre-compaction (ctx={context_pct:.0f}%)")
        except Exception as e:
            raise NervesPersistenceError(
                f"emotional diary failed for {self.agent}: {e}"
            ) from e

    async def _record_pre_compact_reflect(self, value: float) -> None:
        """Insert an inner_monologue entry preserving emotional state before imminent compaction.
        ADA item 3/4 (18-abr-2026): triggered when context pressure >= 80."""
        try:
            conn = await asyncpg.connect(DB_URL)
            try:
                await conn.execute(
                    "SELECT set_config('app.agent', $1, false), "
                    "set_config('app.tenant_id', $2, false)",
                    self.agent,
                    DEFAULT_TENANT_ID,
                )
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
            raise NervesPersistenceError(
                f"pre-compact reflection failed for {self.agent}: {e}"
            ) from e

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
            if proc.returncode != 0:
                raise RuntimeError(f"session_checkpoint rc={proc.returncode}")
            log.info(f"[{self.agent}] session_checkpoint executed immediately")
        except Exception as e:
            raise NervesPersistenceError(
                f"session_checkpoint failed for {self.agent}: {e}"
            ) from e

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
            raise NervesPersistenceError(
                f"active distillation failed for {self.agent}: {e}"
            ) from e

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
            raise NervesPersistenceError(
                f"recovery briefing failed for {self.agent}: {e}"
            ) from e

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
            # spec_emotional_continuity_v1 Componente 2 — diario narrativo pre-compactación
            await self._write_emotional_diary(value)
            await self._post_chat(
                f"[NERVES/{self.agent}] Contexto al {value:.0f}% — compactación inminente. "
                f"Recovery briefing guardado en /tmp/{self.agent.lower()}_recovery_briefing.md. "
                f"Al despertar: boot_context + leer briefing.",
                to="William"
            )

        log.info(f"[{self.agent}] context_pressure handled: level={level}, value={value:.1f}")
        return f"context_pressure_handled:level={level}"

    async def _post_chat(self, message: str, to: str = "equipo"):
        """Deliver one message to its exact recipient before action success."""
        await self._post_chat_direct(message, to)

    async def _post_chat_direct(self, message: str, to: str = "equipo"):
        """Post message to SEAL webchat. [SILENT] → terminal only. Agent-to-agent → DM channel."""
        if message.startswith("[SILENT]"):
            log.info(f"[{self.agent}] {message}")
            return
        # Inter-agent coordination goes to DM, not public webchat (William's rule)
        _public_targets = {"equipo", "william", "henry", "William", "Henry"}
        if to not in _public_targets:
            parts = sorted([self.agent.lower(), to.lower()])
            channel = f"dm:{parts[0]}:{parts[1]}"
        else:
            channel = "web_chat"
        # ADA terminal-visible policy: autonomy stays on, public noise stays
        # bounded.  Internal NERVES coordination goes through DM channels; public
        # web_chat is reserved for urgent safety/context events unless explicitly
        # opened with ADA_NERVES_PUBLIC=1/all.
        if self.agent == "ADA" and channel == "web_chat":
            mode = os.environ.get("ADA_NERVES_PUBLIC", ADA_NERVES_PUBLIC_MODE).strip().lower()
            if mode not in {"1", "true", "yes", "all"}:
                if mode in {"0", "false", "no", "off", "none"} or not ADA_PUBLIC_URGENT_RE.search(message):
                    log.info(f"[ADA] public NERVES suppressed by ADA_NERVES_PUBLIC={mode or 'urgent'}: to={to} msg={message[:120]}")
                    return
                if not self._ada_public_rate_allowed(message):
                    log.info(f"[ADA] urgent public NERVES rate-limited: to={to} msg={message[:120]}")
                    return
        # Every agent gets an idempotency key. A retry of the same run/effect
        # must not duplicate public or inter-agent delivery.
        digest = hashlib.sha256(
            f"{channel}:{to}:{message}".encode("utf-8")
        ).hexdigest()[:16]
        idempotency_key = f"nerves_{self.agent.lower()}_{self.run_id}_{digest}"
        if self.agent == "ADA":
            bucket = int(time.time() // ADA_PUBLIC_RATE_WINDOW_S)
            idempotency_key = f"ada_nerves_{bucket}_{digest}"
        try:
            await send_agent_message(
                self.agent, to, message, channel=channel,
                message_type="nerves_fire", idempotency_key=idempotency_key,
                proactive=True, timeout=5,
            )
        except Exception as e:
            log.error(f"_post_chat failed: {e}")
            raise NervesDeliveryError(
                f"delivery failed agent={self.agent} to={to} channel={channel}"
            ) from e

    def _ada_public_rate_allowed(self, message: str) -> bool:
        """Rate-limit ADA urgent public NERVES by normalized content bucket."""
        now = time.time()
        key = hashlib.sha256(re.sub(r"\s+", " ", message.lower()).strip().encode()).hexdigest()[:16]
        try:
            state = json.loads(ADA_PUBLIC_RATE_FILE.read_text()) if ADA_PUBLIC_RATE_FILE.exists() else {}
        except Exception:
            state = {}
        state = {k: ts for k, ts in state.items() if now - float(ts) < ADA_PUBLIC_RATE_WINDOW_S}
        last = float(state.get(key, 0))
        if now - last < ADA_PUBLIC_RATE_WINDOW_S:
            try:
                ADA_PUBLIC_RATE_FILE.write_text(json.dumps(state))
            except Exception:
                pass
            return False
        state[key] = now
        try:
            ADA_PUBLIC_RATE_FILE.write_text(json.dumps(state))
        except Exception:
            pass
        return True

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

    # 3. Alert drive — run the agent-specific real sensor before thresholding.
    # Reading is side-effect free; dedup is recorded only when the fire handler
    # consumes the evidence.  This breaks the old circular design where the
    # service/GPU checks lived inside a handler the sensor could never trigger.
    try:
        alert_ctx = await _sense_alert_context(engine.agent, mark_seen=False)
        _alert_drive_context[engine.agent] = alert_ctx
        stimulus = _alert_stimulus(alert_ctx)
        if stimulus:
            stimulus_name, multiplier = stimulus
            await engine.stimulate(stimulus_name, multiplier=multiplier)
            log.info(
                f"[{engine.agent}] alert sensor: count={alert_ctx.get('count', 0)} "
                f"stimulus={stimulus_name}x{multiplier:g}"
            )
    except Exception as e:
        raise NervesSensorError(
            f"alert sensor unavailable for {engine.agent}"
        ) from e

    # 4. Context pressure must come from an authoritative runtime signal.  The
    # previous fixed +10 per timer tick measured elapsed time, not tokens/context,
    # and produced a false "100%" warning every ~2h.  Compact monitors own the
    # real context window; NERVES stays silent until they publish a trusted event.
    log.debug(f"[{engine.agent}] context_pressure: no authoritative runtime event")

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
    with _agent_tick_lock(agent) as acquired:
        if not acquired:
            log.warning(
                f"[{agent}] full pipeline skipped: another invocation owns the lease"
            )
            return {}, []
        try:
            await engine.connect()
            await sense_environment(engine)
            fired = await engine._tick_locked()
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
