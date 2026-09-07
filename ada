#!/bin/bash
# ADA CLI — wrapper para acceso rápido desde cualquier directorio
# Uso: ada "¿qué pasó esta noche?" | ada --status | ada --briefing
exec /home/dadito/IA/seal-spark/.venv/bin/python3 \
    /home/dadito/IA/proyecto-seal/memory/ada_cli.py "$@"
