#!/usr/bin/env bash
# install_autostart.sh — Instala el autostart blindado (ALICE 2026-07-14).
# Copia el kit a ~/capytown_autostart, instala el servicio systemd y lo habilita al boot.
# Idempotente: podes correrlo varias veces sin romper nada.
set -euo pipefail

DEST="$HOME/capytown_autostart"
SRC="$(cd "$(dirname "$0")" && pwd)"
USER_NAME="$(whoami)"

echo "== CapyTown autostart · instalando para el usuario ${USER_NAME} =="

# 1) Copiar el kit a una ruta estable del usuario
mkdir -p "$DEST"
cp -f "$SRC/start_bringup.sh" "$DEST/"
cp -f "$SRC/restore_autostart.sh" "$DEST/" 2>/dev/null || true
chmod +x "$DEST/start_bringup.sh"

# 2) Preparar el unit con el usuario y la ruta reales
UNIT_TMP="$(mktemp)"
sed -e "s|^User=.*|User=${USER_NAME}|" \
    -e "s|^ExecStart=.*|ExecStart=${DEST}/start_bringup.sh|" \
    "$SRC/capytown-bringup.service" > "$UNIT_TMP"

# 3) Instalar el unit (systemd de sistema; requiere sudo una sola vez)
sudo cp -f "$UNIT_TMP" /etc/systemd/system/capytown-bringup.service
rm -f "$UNIT_TMP"
sudo systemctl daemon-reload
sudo systemctl enable capytown-bringup.service

echo
echo "== Instalado. Arrancalo AHORA sin reiniciar con: =="
echo "   sudo systemctl start capytown-bringup"
echo
echo "== Verificar por EFECTO: =="
echo "   systemctl status capytown-bringup      # debe decir active (running)"
echo "   journalctl -u capytown-bringup -f      # ver el log del bringup"
echo "   ros2 topic list | grep -E '/scan|/odom'  # deben aparecer"
echo
echo "OJO: si aun no editaste el comando del bringup en start_bringup.sh,"
echo "     editalo en ${DEST}/start_bringup.sh y luego: sudo systemctl restart capytown-bringup"
