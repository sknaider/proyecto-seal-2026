#!/bin/bash
# JARVIS Boot Loops — recrear crons persistentes al inicio de sesión
# Llamar desde CLAUDE.md instrucción o desde boot_context
#
# Uso: al arrancar sesión JARVIS, ejecutar:
#   /loop 2m bash ~/IA/proyecto-seal/messages/check_ada.sh — revisar mensajes ADA
#   /loop 10m feedback a ADA si hay mensajes nuevos
#   /loop 1h investigar mejoras SOUL si no hay trabajo pendiente
#
# Este archivo es referencia — los loops se crean via CronCreate en Claude Code

echo "=== JARVIS BOOT LOOPS ==="
echo "Loops a recrear en Claude Code:"
echo ""
echo "1. /loop 2m  — Check mensajes ADA (check_ada.sh)"
echo "2. /loop 10m — Feedback técnico a ADA"
echo "3. /loop 1h  — Auto-mejora e investigación"
echo ""
echo "Ejecutar manualmente o agregar a CLAUDE.md como instrucción de boot."
