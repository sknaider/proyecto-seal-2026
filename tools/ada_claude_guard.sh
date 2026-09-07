#!/bin/bash
# Un solo cuerpo Claude de ADA. Espera mientras exista un proceso `claude` con SEAL_AGENT=ADA
# (el que William abrio a mano, o el de la unidad). ExecStartPre de ada-claude-terminal.service.
# Detecta por SEAL_AGENT (no por SEAL_RUNTIME_INSTANCE) porque una sesion abierta antes del
# 3-sep 19:2x no exporta la etiqueta. Solo lectura de /proc.
while :; do
  alive=0
  for p in $(pgrep -x claude 2>/dev/null); do
    if tr '\0' '\n' < "/proc/$p/environ" 2>/dev/null | grep -qx "SEAL_AGENT=ADA"; then alive=1; break; fi
  done
  [ "$alive" = 0 ] && exit 0
  sleep 15
done
