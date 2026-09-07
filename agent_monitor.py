#!/usr/bin/env python3
"""
Proyecto SEAL — Agente Monitor con LLM local.
Usa Ollama (qwen2.5:7b) para monitorear el entrenamiento,
detectar errores, y tomar acciones correctivas autónomamente.

Uso: nohup python3 agent_monitor.py > agent_monitor.log 2>&1 &
"""
import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

import requests

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
MODEL = "qwen2.5:7b"  # 4.7GB — mejor clasificación de errores y análisis
SEAL_DIR = Path("/home/dadito/IA/proyecto-seal")
PYTHON = "/home/dadito/IA/seal-spark/.venv/bin/python3"
CHECK_INTERVAL = 120  # 2 minutos — más frecuente para detectar problemas rápido
MAX_RESTARTS = 5
AGENT_LOG = []

# Configurable via CLI args or env — defaults to finetune_ronda2.log
SEAL_LOG = Path(os.environ.get(
    "SEAL_LOG",
    "/home/dadito/IA/proyecto-seal/finetune_ronda2.log"
))
PROCESS_PATTERN = os.environ.get("SEAL_PROCESS", "finetune_spanish")


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    AGENT_LOG.append(line)


def ask_llm(prompt: str, max_tokens: int = 500) -> str:
    """Ask the local LLM for analysis."""
    try:
        r = requests.post(OLLAMA_URL, json={
            "model": MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": max_tokens, "temperature": 0.1},
        }, timeout=120)
        return r.json().get("response", "").strip()
    except Exception as e:
        return f"LLM_ERROR: {e}"


def get_last_logs(n: int = 30) -> str:
    """Read last N lines of SEAL training log."""
    try:
        result = subprocess.run(
            ["tail", f"-{n}", str(SEAL_LOG)],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout
    except Exception:
        return ""


def is_training_running() -> bool:
    """Check if SEAL training process is alive."""
    result = subprocess.run(
        ["pgrep", "-f", PROCESS_PATTERN],
        capture_output=True, text=True,
    )
    return bool(result.stdout.strip())


def is_training_complete() -> bool:
    """Check if training completed normally."""
    try:
        log_text = SEAL_LOG.read_text()
        return "PROYECTO SEAL — COMPLETE" in log_text
    except Exception:
        return False


def get_gpu_status() -> dict:
    """Get GPU temperature and memory."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu,memory.used,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        parts = result.stdout.strip().split(", ")
        return {
            "temp": int(parts[0]) if parts[0].strip().isdigit() else 0,
            "mem_mb": parts[1].strip() if len(parts) > 1 else "N/A",
            "util": parts[2].strip() if len(parts) > 2 else "N/A",
        }
    except Exception:
        return {"temp": 0, "mem_mb": "N/A", "util": "N/A"}


FIXES_DIR = SEAL_DIR / "fixes"

def apply_fix(error_type: str):
    """Apply pre-made fix script based on error type."""
    fix_map = {
        "oom": FIXES_DIR / "fix_oom.sh",
        "thermal": FIXES_DIR / "fix_thermal.sh",
        "generic": FIXES_DIR / "fix_generic_restart.sh",
    }

    script = fix_map.get(error_type, fix_map["generic"])
    log(f"🔧 Applying fix: {script.name}")

    result = subprocess.run(
        ["bash", str(script)],
        capture_output=True, text=True, timeout=300,
    )
    log(f"   Output: {result.stdout.strip()[-200:]}")
    if result.returncode != 0:
        log(f"   ⚠️ Fix stderr: {result.stderr.strip()[-200:]}")


def classify_error(logs: str) -> str:
    """Use LLM to classify error and pick the right fix."""
    analysis = ask_llm(f"""Classify this training error. Answer ONLY with the letter.

A = out of memory (CUDA OOM, malloc failed, killed by kernel)
B = GPU too hot (thermal throttle, temperature warning)
C = recoverable error (timeout, network, attribute error, import error, syntax error)
D = disk full or hardware failure (needs human)

Last log lines:
{logs[-800:]}

Answer (A, B, C, or D):""")

    letter = analysis.strip()[0].upper() if analysis.strip() else "C"

    error_map = {"A": "oom", "B": "thermal", "C": "generic", "D": "alert"}
    return error_map.get(letter, "generic")


def restart_training():
    """Analyze error and apply appropriate fix."""
    log("🔄 Analizando error para aplicar fix correcto...")

    logs = get_last_logs(30)
    error_type = classify_error(logs)

    if error_type == "alert":
        log("🚨 Error crítico — necesita intervención humana. NO reiniciando.")
        return

    log(f"🤖 LLM clasificó error como: {error_type}")
    apply_fix(error_type)


def analyze_and_act():
    """Main analysis loop — read logs, ask LLM, take action."""
    logs = get_last_logs(30)
    gpu = get_gpu_status()
    running = is_training_running()

    if is_training_complete():
        log("✅ Entrenamiento completado. Monitor terminado.")
        return "DONE"

    if not running:
        # Process died — analyze why
        log("⚠️ Proceso SEAL no está corriendo. Analizando...")

        analysis = ask_llm(f"""Classify this error. Answer ONLY with the letter and reason.

A = restart will fix it (memory error, OOM, import error, attribute error, timeout, network error)
B = human must fix it (disk full, hardware broken, data corrupted, permission denied)
C = process is still starting (loading model, downloading)

Last log lines:
{logs[-1000:]}

Answer (A, B, or C):""")

        # Map to actions
        if "A" in analysis[:5].upper():
            analysis = f"RESTART — {analysis}"
        elif "B" in analysis[:5].upper():
            analysis = f"ALERT — {analysis}"
        else:
            analysis = f"WAIT — {analysis}"

        log(f"🤖 LLM Analysis: {analysis}")

        if "RESTART" in analysis.upper():
            return "RESTART"
        elif "ALERT" in analysis.upper():
            return "ALERT"
        else:
            return "WAIT"

    else:
        # Process running — PROACTIVE health analysis
        if gpu["temp"] >= 80:
            log(f"🌡️ GPU caliente: {gpu['temp']}°C")

        # Check if stuck (same log line for >15 min)
        if hasattr(analyze_and_act, '_last_log'):
            if analyze_and_act._last_log == logs and (time.time() - analyze_and_act._last_time) > 900:
                log("⚠️ Proceso parece estancado (15+ min sin cambios en log)")
                analysis = ask_llm(f"""Training process has same log output for 15+ minutes:

{logs[-500:]}

Is this normal (model generating text is slow) or is it stuck?
Answer: NORMAL or STUCK, and why in one sentence.""")
                log(f"🤖 LLM: {analysis}")
                if "STUCK" in analysis.upper():
                    return "RESTART"
        analyze_and_act._last_log = logs
        analyze_and_act._last_time = time.time()

        # PROACTIVE: Analyze log quality every check
        # Look for errors, warnings, or anomalies even while running
        error_lines = [l for l in logs.split("\n") if any(w in l.lower() for w in
                       ["error", "traceback", "exception", "failed", "oom", "killed"])]

        if error_lines:
            log(f"⚠️ Detectadas {len(error_lines)} líneas con errores en log activo")
            analysis = ask_llm(f"""Training is still running but these error lines appeared in the log:

{chr(10).join(error_lines[-5:])}

Full recent log:
{logs[-800:]}

Classify:
A = errors are handled (try/except caught them, training continues)
B = errors are accumulating and training will crash soon
C = false alarm (warnings, not real errors)

Answer A, B, or C with reason.""")
            log(f"🤖 LLM análisis proactivo: {analysis}")

            if "B" in analysis[:3].upper():
                log("🚨 LLM predice crash inminente — preparando reinicio preventivo")
                return "RESTART"

        # Check progress — count items processed
        item_lines = [l for l in logs.split("\n") if "Item " in l and "INFO" in l]
        if item_lines:
            last_item = item_lines[-1].strip()
            log(f"🟢 Running | GPU: {gpu['temp']}°C | {last_item[:100]}")

            # Track items/hour for performance monitoring
            if hasattr(analyze_and_act, '_item_count_time'):
                old_count = analyze_and_act._item_count
                new_count = len(item_lines)
                elapsed_h = (time.time() - analyze_and_act._item_count_time) / 3600
                if elapsed_h > 0.5 and new_count > old_count:
                    rate = (new_count - old_count) / elapsed_h
                    log(f"📊 Velocidad: {rate:.1f} items/hora")
            analyze_and_act._item_count = len(item_lines)
            analyze_and_act._item_count_time = time.time()
        else:
            log(f"🟢 Running | GPU: {gpu['temp']}°C | Inicializando...")

        return "OK"


# ─────────────────────────────────────────────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────────────────────────────────────────────

def main():
    import argparse as ap
    parser = ap.ArgumentParser()
    parser.add_argument("--log", help="Training log file to monitor")
    parser.add_argument("--process", help="Process name to grep for")
    args = parser.parse_args()

    global SEAL_LOG, PROCESS_PATTERN
    if args.log:
        SEAL_LOG = Path(args.log)
    if args.process:
        PROCESS_PATTERN = args.process

    log("=" * 60)
    log("🤖 Proyecto SEAL — Agente Monitor Iniciado")
    log(f"   Modelo: {MODEL} via Ollama")
    log(f"   Intervalo: {CHECK_INTERVAL}s")
    log(f"   Max reinicios: {MAX_RESTARTS}")
    log("=" * 60)

    # Start Ollama if not running
    if not subprocess.run(["pgrep", "-f", "ollama"], capture_output=True).stdout.strip():
        log("Iniciando Ollama...")
        subprocess.run(["sudo", "systemctl", "start", "ollama"], capture_output=True)
        time.sleep(5)

    # Verify LLM works
    test = ask_llm("Respond with OK if you can read this.")
    if "LLM_ERROR" in test:
        log(f"⚠️ LLM no disponible: {test}. Monitor operará en modo básico.")
        llm_available = False
    else:
        log(f"✅ LLM conectado: {test[:50]}")
        llm_available = True

    restart_count = 0

    while True:
        action = analyze_and_act()

        if action == "DONE":
            break
        elif action == "RESTART":
            restart_count += 1
            if restart_count > MAX_RESTARTS:
                log(f"❌ Máximo de reinicios ({MAX_RESTARTS}) alcanzado. Detenido.")
                break
            restart_training()
            time.sleep(60)  # Wait for model to start loading
        elif action == "ALERT":
            log("🚨 ALERTA: Error crítico detectado. Se requiere intervención humana.")
            # Could send notification via webhook, email, etc.
        # OK or WAIT — continue monitoring

        time.sleep(CHECK_INTERVAL)

    # Save agent log
    with open(SEAL_DIR / "agent_monitor_history.json", "w") as f:
        json.dump(AGENT_LOG, f, indent=2, ensure_ascii=False)

    log("Monitor terminado.")


if __name__ == "__main__":
    main()
