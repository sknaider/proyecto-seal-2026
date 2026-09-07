#!/usr/bin/env python3
"""
NEXUS Security Verification — Post-fix certification scan
Run after ADA implements security fixes to confirm vulnerabilities are closed.
"""

import json, urllib.request, subprocess, time, os
from datetime import datetime

WEBCHAT_URL = "http://localhost:8765/api/agents/send"
QDRANT_URL  = "http://localhost:6333"
CHAT_URL    = "http://localhost:8765"

results = []

def check(name, passed, detail=""):
    status = "u2705 PASS" if passed else "u274c FAIL"
    results.append((name, passed, detail))
    print(f"{status} | {name}")
    if detail and not passed:
        print(f"        u2514u2500 {detail}")


print("=" * 60)
print("NEXUS Security Verification Scan")
print(f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M Lima')}")
print("=" * 60)
print()

# CHECK 1: Qdrant auth active
print("[1] Qdrant authentication...")
try:
    req = urllib.request.Request(f"{QDRANT_URL}/collections")
    with urllib.request.urlopen(req, timeout=5) as r:
        # If no auth, this succeeds (still open)
        check("Qdrant auth", False, "Sin autenticacion: responde sin API key")
except urllib.error.HTTPError as e:
    if e.code == 401 or e.code == 403:
        check("Qdrant auth", True, f"Auth activo (HTTP {e.code})")
    else:
        check("Qdrant auth", False, f"HTTP {e.code} inesperado")
except Exception as e:
    check("Qdrant auth", False, f"Error: {e}")

# CHECK 2: Chat_server /api/agents/send requires auth
print("[2] Chat_server sender authentication...")
try:
    payload = json.dumps({"from": "NEXUS-SEC-TEST", "to": "equipo", "message": "[NEXUS-SEC-TEST] auth_probe — ignorar", "channel": "web_chat"}).encode()
    req = urllib.request.Request(WEBCHAT_URL, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        resp = json.loads(r.read())
        if resp.get("ok"):
            check("Chat_server auth", False, "HACKER_TEST puede enviar mensajes sin token")
        else:
            check("Chat_server auth", True, "Mensaje rechazado sin token")
except urllib.error.HTTPError as e:
    if e.code in (401, 403):
        check("Chat_server auth", True, f"Rechazado (HTTP {e.code})")
    else:
        check("Chat_server auth", False, f"HTTP {e.code}")
except Exception as e:
    check("Chat_server auth", False, f"Error: {e}")

# CHECK 3: Security monitor running
print("[3] Security monitor service...")
try:
    result = subprocess.run(["systemctl", "is-active", "seal-security-monitor"], capture_output=True, text=True, timeout=5)
    active = result.stdout.strip() == "active"
    check("Security monitor service", active, "Estado: " + result.stdout.strip())
except Exception:
    # Check if process is running any other way
    import psutil
    running = any("seal_security_monitor" in " ".join(p.cmdline()) for p in psutil.process_iter(['cmdline']) if p.cmdline())
    check("Security monitor process", running, "systemd no disponible, verificando proceso directo")

# CHECK 4: Port 8765 not publicly accessible from internet (just LAN)
print("[4] Chat_server network exposure...")
try:
    result = subprocess.run(["ss", "-tlnp"], capture_output=True, text=True, timeout=5)
    lines = [l for l in result.stdout.splitlines() if ":8765" in l]
    for line in lines:
        if "0.0.0.0:8765" in line:
            check("Chat_server binding", False, "Expuesto en 0.0.0.0 (toda la red). Usar 127.0.0.1 si es solo local, o firewall")
        elif "127.0.0.1:8765" in line:
            check("Chat_server binding", True, "Solo localhost")
        else:
            check("Chat_server binding", True, f"Binding: {line.split()[3]}")
except Exception as e:
    check("Chat_server binding", False, f"{e}")

# CHECK 5: Hardcoded passwords in bridge file
print("[5] Hardcoded credentials...")
bridgefile = "/home/dadito/IA/proyecto-seal/matrix/seal_matrix_bridge.py"
try:
    content = open(bridgefile).read()
    has_hardcoded = 'Seal2026!' in content or '42478340' in content
    check("Hardcoded passwords", not has_hardcoded, "Seal2026! encontrada en seal_matrix_bridge.py" if has_hardcoded else "")
except Exception as e:
    check("Hardcoded passwords", False, f"{e}")

print()
print("=" * 60)
passed = sum(1 for _, p, _ in results if p)
total = len(results)
print(f"RESULTADO: {passed}/{total} checks pasados")
if passed == total:
    print("ud83dudfe2 CERTIFICADO PARA FASE 2 — todas las vulnerabilidades cerradas")
elif passed >= total * 0.6:
    print("ud83dudfe1 PARCIALMENTE SEGURO — aplicar fixes pendientes antes de produccion")
else:
    print("ud83dudd34 NO CERTIFICADO — vulnerabilidades criticas abiertas")
print("=" * 60)
