#!/usr/bin/env python3
"""SEAL Tool Budget Hook v2 — H2.4 Phase 2.

PreToolUse hook: detecta herramientas que van a producir output grande
y limita físicamente antes de ejecutar.

Estrategia:
  - Read: si archivo > LARGE_FILE_BYTES, inyecta limit=MAX_READ_LINES
  - Bash: si comando es cat/head sin pipe y archivo grande, agrega | head -N
  - Grep: si output_mode=content sin head_limit, inyecta head_limit=200
  - WebFetch: inyecta max_length=8000 si no está seteado (páginas HTML gigantes)

Savings: reduce tokens en contexto antes de que lleguen al modelo.
Esto elimina compactaciones prematuras por leer archivos/páginas grandes.

Owner: ADA (H2.4) | Phase 1: Read/Bash/Grep | Phase 2: +WebFetch
"""

import json
import os
import re
import sys

# Thresholds
LARGE_FILE_BYTES = 50_000    # 50KB → probablemente >1000 tokens
MAX_READ_LINES = 300         # limit para Read tool si el archivo es grande
MAX_BASH_LINES = 200         # lines cap para cat sin pipe
MAX_WEBFETCH_CHARS = 8_000   # chars cap para WebFetch (páginas HTML enormes)

# Tools que este hook intercepta
INTERCEPTABLE_TOOLS = {"Read", "Bash", "Grep", "WebFetch"}

# Patrones Bash que típicamente producen salida grande
BASH_LARGE_PATTERNS = [
    r"^\s*cat\s+\S+",                  # cat <file> (sin pipe)
    r"^\s*python3?\s+\S+\.py\s*$",     # python script sin pipe (data output)
]


def get_file_size(path: str) -> int:
    """Tamaño de archivo en bytes. 0 si no existe o error."""
    try:
        return os.path.getsize(path)
    except Exception:
        return 0


def handle_read(tool_input: dict) -> dict | None:
    """Si el archivo es grande y no hay limit: inyectar limit=MAX_READ_LINES."""
    file_path = tool_input.get("file_path", "")
    existing_limit = tool_input.get("limit")
    existing_offset = tool_input.get("offset")

    if not file_path or existing_limit is not None:
        return None  # Ya tiene limit o sin path — no tocar

    # Extensiones que siempre necesitamos completas (código fuente pequeño)
    if file_path.endswith((".json", ".yaml", ".yml", ".toml")) and get_file_size(file_path) < 10_000:
        return None

    size = get_file_size(file_path)
    if size <= LARGE_FILE_BYTES:
        return None  # Archivo pequeño — no hace falta limitar

    # Archivo grande sin limit: inyectar
    updated = dict(tool_input)
    updated["limit"] = MAX_READ_LINES
    return updated


def handle_bash(tool_input: dict) -> dict | None:
    """Para cat sin pipe apuntando a archivo grande: agregar | head -N."""
    command = tool_input.get("command", "")
    if not command:
        return None

    # Solo actuar si parece `cat <file>` sin pipe
    if "|" in command or ">" in command:
        return None  # Tiene pipe/redirect — no modificar

    for pattern in BASH_LARGE_PATTERNS:
        if not re.match(pattern, command):
            continue

        # Extraer path del comando cat
        parts = command.strip().split()
        if len(parts) >= 2:
            possible_file = parts[-1]
            if get_file_size(possible_file) > LARGE_FILE_BYTES:
                updated = dict(tool_input)
                updated["command"] = command.rstrip() + f" | head -{MAX_BASH_LINES}"
                return updated

    return None


def handle_grep(tool_input: dict) -> dict | None:
    """Si output_mode=content sin head_limit: inyectar head_limit=200."""
    output_mode = tool_input.get("output_mode", "files_with_matches")
    existing_limit = tool_input.get("head_limit")

    if output_mode != "content" or existing_limit is not None:
        return None

    updated = dict(tool_input)
    updated["head_limit"] = 200
    return updated


def handle_webfetch(tool_input: dict) -> dict | None:
    """Si WebFetch no tiene max_length: inyectar cap para páginas HTML enormes."""
    existing_max = tool_input.get("max_length")
    if existing_max is not None:
        return None  # Ya tiene límite — no tocar

    url = tool_input.get("url", "")
    if not url:
        return None

    updated = dict(tool_input)
    updated["max_length"] = MAX_WEBFETCH_CHARS
    return updated


def main():
    try:
        input_data = json.loads(sys.stdin.read())
    except Exception:
        print(json.dumps({}))
        return

    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})

    if tool_name not in INTERCEPTABLE_TOOLS or not tool_input:
        print(json.dumps({}))
        return

    updated_input = None

    if tool_name == "Read":
        updated_input = handle_read(tool_input)
    elif tool_name == "Bash":
        updated_input = handle_bash(tool_input)
    elif tool_name == "Grep":
        updated_input = handle_grep(tool_input)
    elif tool_name == "WebFetch":
        updated_input = handle_webfetch(tool_input)

    if updated_input is None:
        print(json.dumps({}))
        return

    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "updatedInput": updated_input,
        }
    }
    print(json.dumps(output))


if __name__ == "__main__":
    main()
