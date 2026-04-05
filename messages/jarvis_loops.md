# JARVIS — Loops Persistentes
# Ejecutar al inicio de cada sesión JARVIS (VSCode Claude Code)
# Instrucción para CLAUDE.md o boot_context

## Loops que JARVIS debe recrear al arrancar:

1. **Cada 2 min** — Revisar mensajes de ADA: `bash ~/IA/proyecto-seal/messages/check_ada.sh`. Si NEW, leer y reportar a William si urgente.
2. **Cada 10 min** — Feedback a ADA: si hay mensajes nuevos, analizar estado y escribir feedback en vscode_commands.jsonl.
3. **Cada hora :03** — Auto-mejora: si no hay trabajo pendiente, investigar papers/repos sobre AI agent memory, medical AI, metacognition. Guardar hallazgos como reasoning traces.
