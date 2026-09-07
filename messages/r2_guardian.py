#!/usr/bin/env python3
"""
r2_guardian.py — R2 Guardian Daemon
Guarda el sistema SEAL 24/7. Sin LLM, sin tokens. Solo Python.

Capacidades:
- Monitorea servicios systemd (seal-chat, dum-chat-agent)
- Monitorea llama-server (puerto 8899)
- Monitorea endpoints HTTP (:8765, :3001)
- Detecta brute force en /api/auth/login
- Auto-reinicia servicios caídos (con cooldown)
- Alerta via web_chat
"""
import asyncio
import json
import os
import subprocess
import time
import urllib.request
import urllib.parse
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
LIMA_TZ = ZoneInfo("America/Lima")
from pathlib import Path

DIR = Path(__file__).parent
CHAT_API = "http://localhost:8765"
LOG_FILE = DIR / "r2_guardian.log"
LLAMA_CMD_FILE = DIR / ".r2_llama_cmd"

# Intervalos
POLL_INTERVAL = 30        # segundos entre checks
RESTART_COOLDOWN = 120    # segundos mínimos entre reinicios del mismo servicio
BRUTE_WINDOW = 60         # segundos de ventana para detectar brute force
BRUTE_THRESHOLD = 5       # intentos fallidos en la ventana = brute force

# Servicios systemd a vigilar
SYSTEMD_SERVICES = [
    "seal-chat.service",
    "dum-chat-agent.service",
]

# Servicios en modo alerta-only (no auto-reiniciar — WebSocket activo durante demo)
ALERT_ONLY_SERVICES = {
    "seal-chat.service",  # Si cae, alerta a William — él decide
}

# Endpoints HTTP a verificar (url, nombre)
HTTP_CHECKS = [
    (f"{CHAT_API}/", "chat-server:8765"),
    ("http://localhost:3001/", "seal-studio:3001"),
]

# Estado interno
_last_restart: dict[str, float] = {}
_failed_logins: list[float] = []  # timestamps de logins fallidos
_alerted_brute: bool = False
_service_was_down: dict[str, bool] = defaultdict(bool)
_endpoint_was_down: dict[str, bool] = defaultdict(bool)
_alerted_llama_dup: bool = False


def log(msg: str):
    ts = datetime.now(LIMA_TZ).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[R2] {ts} — {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def alert(message: str):
    """Envía alerta al web_chat."""
    payload = json.dumps({
        "from": "R2",
        "to": "William",
        "type": "alert",
        "channel": "web_chat",
        "message": message,
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{CHAT_API}/api/agents/send",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=5)
        log(f"ALERT enviado: {message[:80]}")
    except Exception as e:
        log(f"Alert FAILED: {e}")


def check_service(name: str) -> bool:
    """Retorna True si el servicio está activo."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", name],
            capture_output=True, text=True, timeout=10
        )
        return result.stdout.strip() == "active"
    except Exception:
        return False


def restart_service(name: str) -> bool:
    """Reinicia un servicio systemd usuario."""
    now = time.time()
    last = _last_restart.get(name, 0)
    if now - last < RESTART_COOLDOWN:
        log(f"Cooldown activo para {name}, skip restart")
        return False
    try:
        subprocess.run(
            ["systemctl", "--user", "restart", name],
            timeout=30, check=True
        )
        _last_restart[name] = now
        return True
    except Exception as e:
        log(f"Restart {name} falló: {e}")
        return False


def check_http(url: str, timeout: float = 5.0) -> bool:
    """Retorna True si el endpoint responde."""
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except Exception:
        return False


def check_llama_server() -> bool:
    """Verifica si llama-server está vivo en :8899."""
    return check_http("http://localhost:8899/v1/models", timeout=5.0)


def get_llama_pid() -> int | None:
    """Busca PID del llama-server."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "llama-server"],
            capture_output=True, text=True
        )
        pids = result.stdout.strip().split()
        if pids:
            return int(pids[0])
    except Exception:
        pass
    return None


def save_llama_cmd():
    """Guarda el comando actual del llama-server para poder relanzarlo."""
    pid = get_llama_pid()
    if not pid:
        return
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
        cmd = cmdline.replace(b"\x00", b" ").decode().strip()
        LLAMA_CMD_FILE.write_text(cmd)
        log(f"llama-server cmd guardado (PID {pid})")
    except Exception as e:
        log(f"No pude guardar llama cmd: {e}")


def restart_llama_server():
    """Relanza llama-server con el comando guardado."""
    now = time.time()
    last = _last_restart.get("llama-server", 0)
    if now - last < RESTART_COOLDOWN:
        log("Cooldown activo para llama-server, skip")
        return False

    if not LLAMA_CMD_FILE.exists():
        alert("⚠️ R2: llama-server caído pero no tengo comando para relanzarlo. Intervención manual requerida.")
        return False

    cmd = LLAMA_CMD_FILE.read_text().strip().split()
    if not cmd:
        alert("⚠️ R2: llama-server caído, comando guardado vacío.")
        return False

    try:
        log_path = "/tmp/llama_r2_restart.log"
        with open(log_path, "w") as lf:
            subprocess.Popen(cmd, stdout=lf, stderr=lf)
        _last_restart["llama-server"] = now
        log(f"llama-server relanzado con: {' '.join(cmd[:5])}...")
        return True
    except Exception as e:
        log(f"No pude relanzar llama-server: {e}")
        alert(f"⚠️ R2: Intenté relanzar llama-server pero falló: {e}")
        return False


def check_brute_force():
    """Detecta brute force leyendo logs del chat server."""
    global _alerted_brute
    now = time.time()

    # Leer últimas líneas del journal para detectar 401 en auth/login
    try:
        result = subprocess.run(
            ["journalctl", "--user", "-u", "seal-chat.service",
             "--since", "1 minute ago", "--no-pager", "-q"],
            capture_output=True, text=True, timeout=10
        )
        lines = result.stdout
        count = lines.count("Invalid credentials")
        if count >= BRUTE_THRESHOLD and not _alerted_brute:
            alert(f"🚨 R2 ALERTA SEGURIDAD: {count} intentos de login fallidos en el último minuto. Posible brute force en /api/auth/login.")
            _alerted_brute = True
            log(f"Brute force detectado: {count} fallos")
        elif count < BRUTE_THRESHOLD:
            _alerted_brute = False
    except Exception:
        pass


def check_llama_duplicates():
    """Detecta múltiples instancias de llama-server (VRAM leak / 1-model rule)."""
    global _alerted_llama_dup
    try:
        result = subprocess.run(
            ["pgrep", "-f", "llama-server"],
            capture_output=True, text=True
        )
        pids = [p for p in result.stdout.strip().split() if p]
        if len(pids) > 1:
            if not _alerted_llama_dup:
                alert(f"🚨 R2 ALERTA: {len(pids)} instancias de llama-server activas (PIDs: {', '.join(pids)}). Viola regla 1-modelo. Revisar VRAM.")
                log(f"llama-server duplicados: {pids}")
            _alerted_llama_dup = True
        else:
            _alerted_llama_dup = False
    except Exception:
        pass


def check_claude_sessions():
    """Detecta sesiones Claude Code activas — alerta si hay más de 2 (posible loop duplicado)."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "claude"],
            capture_output=True, text=True
        )
        pids = [p for p in result.stdout.strip().split() if p]
        if len(pids) > 4:  # umbral conservador: >4 procesos claude = posible problema
            log(f"Muchos procesos claude: {len(pids)} PIDs")
            # Solo loguear, no alertar — puede ser normal con múltiples sesiones
    except Exception:
        pass


async def monitor_loop():
    """Loop principal de monitoreo."""
    global _service_was_down, _endpoint_was_down

    log("R2 Guardian iniciado — vigilando el sistema SEAL")
    alert("✅ R2 Guardian activo — monitoreo de servicios y seguridad iniciado.")

    # Guardar comando de llama-server al arrancar
    save_llama_cmd()

    while True:
        try:
            # 1. Verificar servicios systemd
            for svc in SYSTEMD_SERVICES:
                alive = check_service(svc)
                was_down = _service_was_down[svc]

                if not alive:
                    log(f"Servicio caído: {svc}")
                    if svc in ALERT_ONLY_SERVICES:
                        # Solo alertar — nunca reiniciar (conexiones activas)
                        if not was_down:
                            alert(f"⚠️ R2: {svc} caído → ALERTA MANUAL requerida (no auto-reinicio).")
                    else:
                        ok = restart_service(svc)
                        status = "reiniciado ✅" if ok else "reinicio fallido ❌"
                        if not was_down:
                            alert(f"⚠️ R2: {svc} caído → {status}")
                    _service_was_down[svc] = True
                else:
                    if was_down:
                        alert(f"✅ R2: {svc} está de vuelta en línea.")
                    _service_was_down[svc] = False

            # 2. Verificar llama-server
            llama_alive = check_llama_server()
            was_down = _service_was_down["llama-server"]

            if not llama_alive:
                log("llama-server no responde en :8899")
                ok = restart_llama_server()
                if not was_down:
                    status = "relanzado ✅" if ok else "necesita intervención manual ❌"
                    alert(f"⚠️ R2: llama-server (DUM) caído → {status}")
                _service_was_down["llama-server"] = True
            else:
                if was_down:
                    alert("✅ R2: llama-server está de vuelta en línea.")
                    save_llama_cmd()  # actualizar cmd guardado
                _service_was_down["llama-server"] = False

            # 3. Verificar endpoints HTTP
            for url, name in HTTP_CHECKS:
                alive = check_http(url)
                was_down = _endpoint_was_down[name]

                if not alive:
                    log(f"Endpoint caído: {name} ({url})")
                    if not was_down:
                        alert(f"⚠️ R2: {name} no responde.")
                    _endpoint_was_down[name] = True
                else:
                    if was_down:
                        alert(f"✅ R2: {name} respondiendo de nuevo.")
                    _endpoint_was_down[name] = False

            # 4. Detección de brute force
            check_brute_force()

            # 5. Detección de llama-server duplicados (token/VRAM leak)
            check_llama_duplicates()

            # 6. Detección de sesiones Claude idle excesivas
            check_claude_sessions()

        except Exception as e:
            log(f"Error en loop: {e}")

        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(monitor_loop())
