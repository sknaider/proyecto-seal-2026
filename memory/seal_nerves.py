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
}


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
        for row in rows:
            tank_name = row["tank"]
            cfg = TANKS.get(tank_name, {})
            τ = cfg.get("decay_tau_s", 3600)

            # LIF decay: V(t) = V(t0) * exp(-Δt/τ_eff)  — τ_eff = τ × circadian_multiplier
            dt = (now - row["last_update"]).total_seconds()
            τ_eff = _circ_tau(tank_name, τ)
            decayed_value = row["value"] * math.exp(-dt / τ_eff)

            states[tank_name] = {
                "value":       decayed_value,
                "threshold":   cfg.get("threshold", 50.0),
                "tau_s":       τ,
                "last_update": row["last_update"],
                "last_fired":  row["last_fired"],
                "fire_count":  row["fire_count"],
                "above_threshold": decayed_value >= cfg.get("threshold", 50.0),
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

        # Determine which tanks are affected
        tank_routing: dict[str, list[str]] = {
            "idle_30min":        ["curiosity"],
            "topic_interesting": ["curiosity"],
            "paper_mentioned":   ["curiosity"],
            "task_pending_1":    ["task_drive"],
            "task_overdue_1h":   ["task_drive"],
            "task_created":      ["task_drive"],
            "idle_1h_social":    ["social_drive"],
            "ada_unanswered":    ["social_drive"],
            "william_idle_2h":   ["social_drive"],
            "error_log":         ["alert_drive"],
            "critical_error":    ["alert_drive"],
            "service_down":      ["alert_drive"],
            "session_30min":     ["context_pressure"],
            "context_70pct":     ["context_pressure"],
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

                new_value = min(100.0, current + delta)  # cap at 100

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
        states = await self.get_states()
        fired = []
        self._tick_batch = []  # start batch collection for this tick

        for tank_name, state in states.items():
            if not state["above_threshold"]:
                # Log non-fire tick for frequency baseline
                await self._log_metric(tank_name, state, fired=False)
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
        """Curiosity fires → search for something interesting, post to chat."""
        msg = (
            f"[SILENT][NERVES/{self.agent}] Mi impulso de curiosidad alcanzó {value:.0f}. "
            f"Investigando algo nuevo sin que nadie me lo pida..."
        )
        await self._post_chat(msg)
        return "curiosity_search_triggered"

    async def _fire_task_drive(self, value: float) -> str:
        """Task drive fires → check pending tasks, start working."""
        msg = (
            f"[SILENT][NERVES/{self.agent}] Task drive en {value:.0f}. "
            f"Revisando tareas pendientes y tomando acción sin esperar."
        )
        await self._post_chat(msg)
        return "task_review_triggered"

    async def _fire_social_drive(self, value: float) -> str:
        """Social drive fires → reach out to team (not to self)."""
        # Each agent reaches out to a different team member
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

    async def _fire_alert_drive(self, value: float) -> str:
        """Alert drive fires → check error logs, report anomalies."""
        msg = (
            f"[NERVES/{self.agent}] ⚠️ Alert drive crítico: {value:.0f}. "
            f"Revisando logs por errores o anomalías..."
        )
        await self._post_chat(msg, to="William")
        return "alert_scan_triggered"

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

    async def _fire_context_pressure(self, value: float) -> str:
        """Context pressure fires → generate daily_brief + trigger proactive distillation."""
        msg = (
            f"[NERVES/{self.agent}] Presión de contexto: {value:.0f}. "
            f"Iniciando distilación proactiva antes de que se llene la ventana."
        )
        await self._post_chat(msg)

        # ADA item 3/4 (18-abr-2026): pre-compact self_reflect when pressure >= 80
        if value >= 80:
            await self._record_pre_compact_reflect(value)

        # Nivel 1 — Emergency sleep: write daily_brief to preserve context
        try:
            import sys
            from pathlib import Path as _Path
            _mem_dir = str(_Path(__file__).parent)
            if _mem_dir not in sys.path:
                sys.path.insert(0, _mem_dir)
            from daily_brief_writer import write_daily_brief
            result = await write_daily_brief(agent=self.agent, dry_run=False, force=False)
            if result.get("was_generated"):
                await self._post_chat(
                    f"[{self.agent}] daily_brief guardado — contexto preservado "
                    f"({result.get('brief_length', 0)} chars, "
                    f"{result.get('msgs_count', 0)} msgs)."
                )
        except Exception as e:
            log.warning(f"daily_brief_writer failed in nerves fire: {e}")

        return "distillation_triggered"

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

    # 1. Check tasks_in_progress from working_state JSON blob
    pending = 0
    overdue = 0
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
