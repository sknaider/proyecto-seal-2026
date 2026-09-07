#!/usr/bin/env bash
# start_bringup.sh — Lanzador del BRINGUP del robot CapyTown (ALICE, 2026-07-14).
# Arranca los nodos base que alguien borro del autostart: /scan (LiDAR), /odom y servos.
# Corre en BASH (no lxterminal). Lo invoca el servicio systemd capytown-bringup.service.
#
# ============================================================================
#  >>> EDITA SOLO LA LINEA "BRINGUP_CMD" DE ABAJO <<<
#  Pon el comando/launch EXACTO que arrancaba scan+odom+servos en tu robot.
#  Lo obtienes del Paso 1 (grep en autostart/.bashrc/rc.local, o history | grep launch).
#  Ejemplos tipicos Yahboom ROSMASTER / MicroROS-Pi5:
#     ros2 launch yahboomcar_bringup bringup.launch.py
#     ros2 launch rosmaster_driver driver.launch.py
#  Si eran VARIOS launch, ponlos separados por "&" y deja el "wait" del final.
# ============================================================================

set -u

# --- 1) Entorno ROS (ajusta la version/distro si no es humble) -------------
export ROS_DISTRO="${ROS_DISTRO:-humble}"
source /opt/ros/${ROS_DISTRO}/setup.bash 2>/dev/null || true

# Overlay del workspace del robot (ajusta la ruta si tu ws no es ~/ros2_ws)
for WS in "$HOME/ros2_ws" "$HOME/yahboomcar_ws" "$HOME/capytown_ws"; do
  if [ -f "$WS/install/setup.bash" ]; then
    source "$WS/install/setup.bash"
    break
  fi
done

# Dominio ROS (si el "no entra el domain" era esto, ajustalo; default 0)
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"

# --- 2) EL comando del bringup ---------------------------------------------
# Por defecto AUTO-DETECTA el launch del bringup de tu robot (no tenes que saberlo).
# Si preferis fijarlo a mano, defini BRINGUP_CMD antes de correr (o edita el fallback).
if [ -z "${BRINGUP_CMD:-}" ]; then
  # Candidatos tipicos Yahboom ROSMASTER / MicroROS-Pi5 (paquete launch) — el primero que EXISTA gana.
  CANDIDATOS=(
    "yahboomcar_bringup bringup.launch.py"
    "yahboomcar_bringup yahboomcar_bringup.launch.py"
    "rosmaster_driver driver.launch.py"
    "yahboomcar_nav bringup.launch.py"
    "ms200_scan ms200_scan.launch.py"
  )
  for cand in "${CANDIDATOS[@]}"; do
    pkg="${cand%% *}"; lf="${cand##* }"
    share="$(ros2 pkg prefix "$pkg" 2>/dev/null)/share/$pkg"
    if [ -n "$share" ] && find "$share" -name "$lf" 2>/dev/null | grep -q .; then
      BRINGUP_CMD="ros2 launch $cand"
      echo "[capytown-bringup] auto-detectado: $BRINGUP_CMD"
      break
    fi
  done
fi
# Fallback si nada matcheo (edita esta linea con TU comando si hiciera falta):
BRINGUP_CMD="${BRINGUP_CMD:-ros2 launch yahboomcar_bringup bringup.launch.py}"

echo "[capytown-bringup] $(date) lanzando: ${BRINGUP_CMD}"
echo "[capytown-bringup] ROS_DISTRO=${ROS_DISTRO} ROS_DOMAIN_ID=${ROS_DOMAIN_ID}"

# --- 3) Ejecuta (exec para que systemd lo supervise como el proceso hijo) ---
exec ${BRINGUP_CMD}
