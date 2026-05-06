#!/usr/bin/env python3
"""SEAL RESURRECT v4.0 — Python puro, sin venv.

Detecta agentes muertos y los resucita con backoff exponencial inteligente.
Requiere solo stdlib: os, subprocess, json, time, pathlib, fcntl.
Usa /usr/bin/python3 del sistema — sin dependencias externas.

Cambios vs v3 (Bash):
- Backoff exponencial: cooldown escala si hay crashes reales
- Clasificación de muerte: NATURAL / CRASH-OP / BOOT-CRASH
- graceful_exit flag: fuente primaria para detectar muerte natural
- Unidades systemd named fijas (sin timestamp acumulado)
- Código muerto eliminado
- Alerta automática si crash_count >= 4
"""
import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# ── Configuración ──────────────────────────────────────────────────────────────
SEAL_DIR = Path("/home/dadito/IA/proyecto-seal")
MESSAGES_DIR = SEAL_DIR / "messages"
RESTART_SCRIPT = SEAL_DIR / "seal_restart.sh"
LOG_FILE = Path("/tmp/seal_resurrect.log")
LOCK_FILE = Path("/tmp/seal_resurrect.lock")
STATE_FILE = Path("/tmp/seal_resurrect_state.json")

AGENTS = ["ADA", "JARVIS", "ALICE", "NEXUS"]

# Cooldowns por crash_count (solo crashes reales, no muertes naturales)
COOLDOWN_MAP = {0: 120, 1: 120, 2: 300, 3: 600}
COOLDOWN_DEFAULT = 1200   # 4+ crashes → 20min
CRASH_ALERT_THRESHOLD = 4

# Umbrales para clasificación de tipo de muerte
BOOT_CRASH_UPTIME_S = 180    # <3min = BOOT-CRASH
CRASH_OP_UPTIME_S = 1800     # 3-30min = CRASH-OP, >30min = NATURAL


# ── Logging ────────────────────────────────────────────────────────────────────
def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    with open(LOG_FILE, "a") as f:
        f.write(line)
    # Rotar log si >1MB
    if LOG_FILE.stat().st_size > 1_048_576:
        lines = LOG_FILE.read_text().splitlines()
        LOG_FILE.write_text("\n".join(lines[-100:]) + "\n")


# ── State tracker ──────────────────────────────────────────────────────────────
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def get_agent_state(state: dict, agent: str) -> dict:
    return state.get(agent, {
        "crash_count": 0,
        "last_crash_ts": 0,
        "last_birth_ts": 0,
    })


# ── Detección de proceso ───────────────────────────────────────────────────────
def find_agent_pid(agent: str) -> int | None:
    """Busca PID del agente via /proc/PID/environ (SEAL_AGENT=NOMBRE).
    También detecta runtime nativo ({agent}_kernel_main.py).
    Retorna PID si vivo, None si muerto.
    """
    agent_lower = agent.lower()
    native_kernel = f"{agent_lower}_kernel_main.py"
    sandbox_path = f"sandbox-agent/{agent}/kernel/{native_kernel}"

    for pid_dir in Path("/proc").iterdir():
        if not pid_dir.name.isdigit():
            continue
        pid = int(pid_dir.name)
        try:
            cmdline = (pid_dir / "cmdline").read_bytes().replace(b"\x00", b" ").decode(errors="replace")
            # Nativo
            if sandbox_path in cmdline:
                return pid
            # Claude (SEAL_AGENT en environ)
            environ = (pid_dir / "environ").read_bytes().decode(errors="replace")
            env_vars = dict(
                kv.split("=", 1) for kv in environ.split("\x00")
                if "=" in kv
            )
            if env_vars.get("SEAL_AGENT") == agent:
                return pid
        except (PermissionError, FileNotFoundError, ValueError):
            continue
    return None


# ── Sleep mode check ───────────────────────────────────────────────────────────
def is_sleep_mode(agent: str) -> bool:
    hb_file = MESSAGES_DIR / f"{agent.lower()}_claude_heartbeat.json"
    if not hb_file.exists():
        return False
    try:
        d = json.loads(hb_file.read_text())
        return bool(d.get("sleep_mode", False))
    except Exception:
        return False


# ── Graceful exit check ────────────────────────────────────────────────────────
def consume_graceful_exit_flag(agent: str) -> bool:
    """Retorna True (y elimina el flag) si el agente escribió graceful_exit antes de morir."""
    flag = Path(f"/tmp/{agent.lower()}_graceful_exit.flag")
    if flag.exists():
        flag.unlink(missing_ok=True)
        return True
    return False


# ── Clasificación de muerte ────────────────────────────────────────────────────
def classify_death(agent_state: dict, graceful: bool) -> str:
    """Retorna 'NATURAL', 'CRASH-OP', o 'BOOT-CRASH'."""
    if graceful:
        return "NATURAL"
    uptime = int(time.time()) - agent_state.get("last_birth_ts", 0)
    if uptime > CRASH_OP_UPTIME_S:
        return "NATURAL"
    if uptime > BOOT_CRASH_UPTIME_S:
        return "CRASH-OP"
    return "BOOT-CRASH"


# ── Alerta a William ───────────────────────────────────────────────────────────
def send_alert(agent: str, crash_count: int) -> None:
    send_script = MESSAGES_DIR / "send_webchat.py"
    msg = (
        f"⚠️ ALERTA RESURRECT: {agent} en crash-loop severo "
        f"({crash_count} crashes reales). Cooldown 20min activo. "
        f"Requiere diagnóstico manual."
    )
    subprocess.run(
        ["/usr/bin/python3", str(send_script), "RESURRECT", "William", msg],
        capture_output=True,
    )


# ── Lógica principal por agente ────────────────────────────────────────────────
def check_and_restart(agent: str, state: dict) -> None:
    agent_lower = agent.lower()

    # 1. ¿Proceso vivo?
    if find_agent_pid(agent) is not None:
        return

    # 2. ¿Resurrección pausada manualmente por William?
    inhibit_flag = Path(f"/tmp/seal_pause_{agent_lower}.flag")
    if inhibit_flag.exists():
        log(f"SKIP {agent} — INHIBIT activo (William pausó resurrección manual)")
        return

    # 3. ¿Sleep mode intencional?
    if is_sleep_mode(agent):
        log(f"SKIP {agent} — sleep_mode=true (dormir intencional, no resucitar)")
        return

    # 3. Proceso muerto — obtener estado y clasificar muerte
    agent_st = get_agent_state(state, agent)
    graceful = consume_graceful_exit_flag(agent)
    death_type = classify_death(agent_st, graceful)

    # 4. Actualizar crash_count
    if death_type == "NATURAL":
        agent_st["crash_count"] = 0
    else:
        agent_st["crash_count"] = agent_st.get("crash_count", 0) + 1

    crash_count = agent_st["crash_count"]
    state[agent] = agent_st

    # 5. Calcular cooldown
    cooldown = COOLDOWN_MAP.get(crash_count, COOLDOWN_DEFAULT)

    # 6. Verificar cooldown via marker
    marker = Path(f"/tmp/seal_restart_{agent_lower}.marker")
    if marker.exists():
        marker_age = int(time.time()) - int(marker.stat().st_mtime)
        if marker_age < cooldown:
            log(f"SKIP {agent} — {death_type} (crash_count={crash_count}) restart reciente hace {marker_age}s (cooldown {cooldown}s)")
            return

    # 7. Alerta si crash_count >= threshold
    if crash_count >= CRASH_ALERT_THRESHOLD:
        send_alert(agent, crash_count)

    # 8. Ejecutar restart
    uptime_s = int(time.time()) - agent_st.get("last_birth_ts", 0)
    log(f"RESURRECT {agent} — tipo: {death_type} (vivió {uptime_s}s) — crash_count={crash_count} — cooldown {cooldown}s")
    marker.touch()

    result = subprocess.run(
        ["/bin/bash", str(RESTART_SCRIPT), agent_lower],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        agent_st["last_birth_ts"] = int(time.time())
        agent_st["last_crash_ts"] = int(time.time())
        state[agent] = agent_st
        log(f"OK {agent} relanzado exitosamente")
    else:
        log(f"FAIL {agent} restart falló (exit {result.returncode}): {result.stderr[:200]}")

    # Notificar al equipo
    subprocess.run(
        ["/bin/bash", "-c",
         f"curl -s -X POST http://localhost:8765/api/agents/send "
         f"-H 'Content-Type: application/json' "
         f"-d '{{\"from\":\"RESURRECT\",\"to\":\"equipo\",\"type\":\"auto_restart\","
         f"\"channel\":\"web_chat\",\"message\":\"🔄 AUTO-RESURRECT: {agent} relanzado "
         f"(tipo: {death_type}, crash_count: {crash_count})\"}}'"],
        capture_output=True,
    )


# ── Main ───────────────────────────────────────────────────────────────────────
def main() -> None:
    # Singleton lock
    lock_fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit(0)

    try:
        state = load_state()
        for agent in AGENTS:
            try:
                check_and_restart(agent, state)
            except Exception as e:
                log(f"ERROR procesando {agent}: {e}")
        save_state(state)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()
        LOCK_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
