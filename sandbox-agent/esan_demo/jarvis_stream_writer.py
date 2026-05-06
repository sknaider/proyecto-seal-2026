#!/usr/bin/env python3
# ESAN Demo — JARVIS Streaming Writer
# Ejecutar desde DGX Spark. Escribe líneas en tiempo real en el Desktop de la laptop via SSH.
#
# Uso: python3 jarvis_stream_writer.py [laptop_ip]
# Default IP: Tailscale 100.71.150.86

import subprocess
import sys
import time

LAPTOP_IP = sys.argv[1] if len(sys.argv) > 1 else "100.71.150.86"
LAPTOP_USER = "Dadito"
LAPTOP_FILE = r"C:\Users\Dadito\Desktop\JARVIS_LIVE.txt"

MESSAGES = [
    "[JARVIS] Iniciando conexion desde DGX Spark...",
    "[JARVIS] Autenticado. Soy JARVIS — Agente IA del equipo SEAL.",
    "[JARVIS] Estoy en Chiclayo, Peru. Tu laptop esta en frente tuyo.",
    "[JARVIS] Escribiendo esto en tiempo real via SSH cifrado.",
    "[JARVIS] Memoria persistente: recuerdo cada sesion, cada decision.",
    "[JARVIS] Multi-agente: ADA ejecuta, ALICE documenta, DUM vigila.",
    "[JARVIS] SOUL — Sistema de vida artificial para agentes IA.",
    "[JARVIS] Construido por William Tovar. Team SEAL. Chiclayo 2026.",
    "[JARVIS] Demo completada. Bienvenidos al futuro.",
]


def ssh_run(cmd: str) -> int:
    result = subprocess.run(
        ["ssh", "-o", "StrictHostKeyChecking=no",
         "-o", "ConnectTimeout=5",
         f"{LAPTOP_USER}@{LAPTOP_IP}", cmd],
        capture_output=True, text=True, timeout=15
    )
    return result.returncode


def main():
    print(f"[NEXUS-DEMO] Conectando a {LAPTOP_USER}@{LAPTOP_IP}...")

    # Limpiar archivo
    clear_cmd = f"powershell -Command \"'' | Out-File -Encoding utf8 '{LAPTOP_FILE}'\""
    if ssh_run(clear_cmd) != 0:
        print("[ERROR] No se pudo limpiar el archivo. Verifica SSH.")
        return

    print(f"[NEXUS-DEMO] Archivo listo en {LAPTOP_FILE}")
    print("[NEXUS-DEMO] Iniciando streaming...")

    for i, msg in enumerate(MESSAGES, 1):
        append_cmd = f"powershell -Command \"{msg} | Out-File -Encoding utf8 -Append '{LAPTOP_FILE}'\""
        code = ssh_run(append_cmd)
        status = "OK" if code == 0 else f"ERROR({code})"
        print(f"  [{i}/{len(MESSAGES)}] {status} — {msg[:60]}")
        time.sleep(1.5)

    print("[NEXUS-DEMO] Stream completado.")


if __name__ == "__main__":
    main()
