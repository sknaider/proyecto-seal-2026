#!/usr/bin/env bash
# ============================================================================
# install-seal-device.sh — Deploy de 1 comando de un agente SEAL en un device.
# Carril NEXUS (Fase 3). Compone las piezas verificadas: Tailscale + CSR + token + daemon.
# ============================================================================
# Visión William: correr esto en cualquier device (Windows/Linux/Mac) y que ese device
# quede conectado a SOUL central — SIN config manual, cifrado, con tu autorización.
#
# FLUJO (con las compuertas de seguridad ya construidas y verificadas):
#   1. Detecta OS + instala Tailscale → se une al tailnet privado (cifrado, sin exponer a internet).
#   2. Genera un CSR firmado (seal_csr.py) — proof-of-possession, sin recibir token todavía.
#   3. El device SOLO ESCRIBE su CSR a disco y lo ENVÍA a central. (submit_csr corre EN CENTRAL, no aquí.)
#   4. ⏸️ EN CENTRAL, William APRUEBA (seal_central_signer.approve_and_issue = compuerta humana). El firmante
#      y la clave privada de SOUL viven SOLO en central — NUNCA en el device (least-privilege, catch FABLE).
#   5. Al aprobar, central FIRMA el device-token → el device lo guarda cifrado (seal_token_store).
#   6. El daemon usa load_device_token (PRESENTA el token, no se auto-firma) → sincroniza (gateado por scope).
#
# GATED (para la prueba física final): (a) cuenta Tailscale + auth-key de William, (b) endpoint central
#   de submit/retrieve del CSR (coordinado con JARVIS) — hoy el paso 3/5 se hace por el helper directo.
set -euo pipefail

AGENT="${1:?Uso: install-seal-device.sh <AGENT> [--tailscale-authkey KEY]}"
SEAL_TOOLS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${SEAL_PY:-$(command -v python3 || command -v python)}"   # device fresco: usa el python del sistema, no un venv del host
TS_AUTHKEY="${TAILSCALE_AUTHKEY:-}"

log() { printf '[install-seal-device] %s\n' "$*"; }

# ── 1. Overlay: Tailscale (cifrado, privado) ─────────────────────────────────
install_tailscale() {
  if command -v tailscale >/dev/null 2>&1; then log "tailscale ya instalado"; return 0; fi
  case "$(uname -s)" in
    Linux)  curl -fsSL https://tailscale.com/install.sh | sh ;;
    Darwin) command -v brew >/dev/null && brew install tailscale || { log "instalá Tailscale.app manual"; return 1; } ;;
    MINGW*|MSYS*|CYGWIN*) log "Windows: instalá Tailscale desde tailscale.com/download"; return 1 ;;
    *) log "OS no soportado"; return 1 ;;
  esac
}
join_tailnet() {
  [ -n "$TS_AUTHKEY" ] || { log "FALTA TAILSCALE_AUTHKEY (William lo genera: tailscale auth-keys create). Saltando join."; return 0; }
  sudo tailscale up --auth-key="$TS_AUTHKEY" --hostname="seal-${AGENT}-$(hostname)"
  log "unido al tailnet: $(tailscale ip -4 2>/dev/null || echo '?')"
}

# ── 2-3. CSR firmado + submit (proof-of-possession, sin token aún) ───────────
gen_and_submit_csr() {
  SEAL_TOOLS="$SEAL_TOOLS" "$PY" - "$AGENT" <<'PYEOF'
import sys, os, json
# Ruta a los módulos SEAL: del env SEAL_TOOLS (relocatable), no hardcodeada al host de dev.
sys.path.insert(0, os.environ.get("SEAL_TOOLS", os.path.dirname(os.path.abspath(__file__))))
from seal_csr import generate_device_keypair, build_csr
from cryptography.hazmat.primitives import serialization
agent = sys.argv[1]
priv, pub = generate_device_keypair()
csr = build_csr(agent, priv)
# guardar la privada del device 0600 (para presentar/firmar el CSR; NUNCA firma tokens)
from pathlib import Path
import os
d = Path.home()/".seal"; d.mkdir(parents=True, exist_ok=True); os.chmod(d,0o700)
kp = d/f"{agent}.device_key.pem"
kp.write_bytes(priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
os.chmod(kp, 0o600)
(d/f"{agent}.csr.json").write_text(json.dumps(csr, indent=2))
print(f"CSR generado: device_id={csr['device_id']} agent={agent}")
print(f"→ enviar {d}/{agent}.csr.json a central (submit_csr). Esperando aprobación de William.")
PYEOF
}

main() {
  log "Deploy de agente SEAL: $AGENT"
  install_tailscale || log "WARN: Tailscale no instalado (overlay pendiente)"
  join_tailnet || true
  gen_and_submit_csr
  cat <<EOF

════════════════════════════════════════════════════════════════════
DEVICE INERTE hasta que William apruebe (compuerta human-gated):
  En central:  approve_and_issue(conn, "<device_id>", "William")
  → central firma el token → se entrega al device → seal_token_store lo guarda
  → el daemon lo PRESENTA (load_device_token) y sincroniza (gateado por scope).
Sin token aprobado = fail-closed (no sincroniza). Cero secretos de firma en el device.
════════════════════════════════════════════════════════════════════
EOF
}
main "$@"
