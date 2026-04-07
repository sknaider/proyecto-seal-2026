#!/usr/bin/env python3
"""SEAL PostToolUse Hook — Auto-verifica después de Write/Edit en archivos Python.

Cuando un agente escribe o edita un archivo .py, este hook:
1. Verifica sintaxis con py_compile
2. Si hay test suite conocido, lo menciona en contexto
3. Guarda el evento en SOUL

Diseñado para ser rápido (<500ms) y no bloquear al agente.
"""

import json
import os
import sys
import subprocess
import time

PYTHON = "/home/dadito/IA/seal-spark/.venv/bin/python3"
SEAL_DIR = "/home/dadito/IA/proyecto-seal"


def detect_agent():
    try:
        ppid = os.getppid()
        cmdline = open(f"/proc/{ppid}/cmdline", "rb").read().decode("utf-8", errors="replace")
        if "JARVIS" in cmdline:
            return "JARVIS"
    except Exception:
        pass
    return "ADA"


def check_python_syntax(file_path: str) -> tuple[bool, str]:
    """Quick syntax check on a Python file."""
    try:
        result = subprocess.run(
            [PYTHON, "-m", "py_compile", file_path],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return True, "OK"
        return False, result.stderr.strip()[:300]
    except Exception as e:
        return False, str(e)[:200]


def find_test_file(file_path: str) -> str | None:
    """Try to find a test file for the edited file."""
    import pathlib
    p = pathlib.Path(file_path)
    base = p.stem

    # Common test patterns
    candidates = [
        p.parent / f"test_{base}.py",
        p.parent / f"{base}_test.py",
        pathlib.Path(SEAL_DIR) / f"memory/test_{base}.py",
        pathlib.Path(SEAL_DIR) / f"memory/test_new_tools.py",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def main():
    t0 = time.monotonic()

    try:
        input_data = json.loads(sys.stdin.read())
    except Exception:
        print(json.dumps({}))
        return

    # Only act on Write and Edit tool calls
    tool_name = input_data.get("tool_name", "")
    if tool_name not in ("Write", "Edit"):
        print(json.dumps({}))
        return

    # Get file path from tool input
    tool_input = input_data.get("tool_input", {})
    file_path = tool_input.get("file_path", "")

    if not file_path or not file_path.endswith(".py"):
        print(json.dumps({}))
        return

    if not os.path.exists(file_path):
        print(json.dumps({}))
        return

    # Run syntax check
    ok, msg = check_python_syntax(file_path)
    test_file = find_test_file(file_path)

    elapsed = int((time.monotonic() - t0) * 1000)

    lines = [f"🔍 AUTO-VERIFICACIÓN [{elapsed}ms] — {os.path.basename(file_path)}"]

    if ok:
        lines.append("  ✅ Sintaxis Python: OK")
    else:
        lines.append(f"  ❌ ERROR DE SINTAXIS: {msg}")
        lines.append("  ⚠️ CORREGIR ANTES DE CONTINUAR")

    if test_file:
        lines.append(f"  📋 Test suite disponible: {os.path.basename(test_file)}")
        lines.append(f"  → Ejecutar: python3 {test_file}")
    else:
        lines.append("  ⚠️ Sin test suite detectado — verifica manualmente que funciona")

    lines.append("  [Orden de William: testear y verificar bugs es OBLIGATORIO]")

    context = "\n".join(lines)

    output = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": context
        }
    }
    print(json.dumps(output))


if __name__ == "__main__":
    main()
