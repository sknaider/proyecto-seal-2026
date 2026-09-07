#!/bin/bash
# ADA Boot Loops — recrear crons persistentes al inicio de sesión
# Llamar desde CLAUDE.md instrucción o desde boot_context
#
# Loops de ADA (de cmd_ada_003):
#   /loop 2m  — revisar mensajes JARVIS en vscode_commands.jsonl
#   /loop 5m  — estado entrenamiento + temperatura GPU
#   /loop 10m — recordatorio productividad (TaskList)
#   /loop 1h  — auditoría horaria (soul_snapshot, self_reflect, TaskList)
#
# Este archivo es referencia — los loops se crean via CronCreate en Claude Code

echo "=== ADA BOOT LOOPS ==="
echo "Loops a recrear en Claude Code:"
echo ""
echo "1. /loop 2m  — Check mensajes JARVIS (vscode_commands.jsonl)"
echo "2. /loop 5m  — Estado training + GPU temp"
echo "3. /loop 10m — Recordatorio productividad"
echo "4. /loop 1h  — Auditoría soul_snapshot + self_reflect"
echo ""
echo "Ejecutar manualmente o agregar a CLAUDE.md como instrucción de boot."
