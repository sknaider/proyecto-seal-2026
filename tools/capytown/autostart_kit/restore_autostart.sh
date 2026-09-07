#!/usr/bin/env bash
# restore_autostart.sh — RE-instala el autostart si alguien lo vuelve a borrar (ALICE 2026-07-14).
# Robot publico = otros lo tocan. Si un dia el /scan, /odom o los servos no arrancan al boot,
# corre ESTO y en 10 segundos vuelve. Guarda este kit tambien en un USB/carpeta segura.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"

echo "== Restaurando autostart CapyTown =="
if [ ! -f "/etc/systemd/system/capytown-bringup.service" ]; then
  echo "-> El servicio no existe (lo borraron). Re-instalando..."
  bash "$SRC/install_autostart.sh"
else
  echo "-> El servicio existe. Re-habilitando y arrancando..."
  sudo systemctl daemon-reload
  sudo systemctl enable capytown-bringup.service
fi
sudo systemctl restart capytown-bringup.service
echo
echo "== Estado: =="
systemctl status capytown-bringup --no-pager | head -8 || true
echo
echo "Verifica: ros2 topic list | grep -E '/scan|/odom'"
