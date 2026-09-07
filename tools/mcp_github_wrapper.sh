#!/usr/bin/env bash
# Wrapper del MCP de GitHub (reconstruido 7-sep-2026): el token NO va en .mcp.json (versionado) sino en
# ~/.config/seal/env/github_mcp.env (0600, fuera de git). Sin el archivo, arranca sin token: el server
# reporta el error por sí mismo (fail-closed en sus llamadas), no inventamos credencial.
set -u
ENV_FILE="${SEAL_GITHUB_MCP_ENV:-$HOME/.config/seal/env/github_mcp.env}"
if [ -r "$ENV_FILE" ]; then set -a; . "$ENV_FILE"; set +a; fi
exec /home/dadito/IA/seal-spark/.venv/bin/python3 /home/dadito/IA/proyecto-seal/tools/soul_github_mcp.py "$@"
