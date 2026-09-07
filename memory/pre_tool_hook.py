#!/usr/bin/env python3
"""
SEAL PreToolUse Hook — runs BEFORE every Claude Code tool call.

When explicitly enabled, the routine fast path must complete in <200ms and
ambiguous risky Bash commands may use the local intent classifier for at most
15 seconds. The new gate is fail-closed but remains opt-in until William gives
the separate activation approval required by the handoff. Only stdlib imports.

Tiers:
  1. MCP Identity Gate — validates SEAL_AGENT for seal-memory MCP calls
  2. Bash Safety — blocks clearly dangerous shell commands
  3. Passthrough — everything else exits immediately
"""

import json
import os
import sys
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

LIMA_TZ = ZoneInfo("America/Lima")


def deny(reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def allow_with_updated_input(updated_input: dict, context: str = "") -> dict:
    result = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": updated_input,
        }
    }
    if context:
        result["hookSpecificOutput"]["additionalContext"] = context
    return result


# ---------------------------------------------------------------------------
# Tier 2: Dangerous bash patterns
# ---------------------------------------------------------------------------
BASH_BLOCKLIST = [
    # rm -rf / or rm -rf /* (but not rm -rf ./somedir)
    # El ancla ORIGINAL era `\s*$` y no veía el fin de un argumento
    # entrecomillado: `eval "<borrado> /"` termina en comilla, no en fin de
    # cadena, así que el patrón no matcheaba y el guardián lo dejaba pasar.
    # Hallado el 4-sep midiendo MI PROPIO arreglo: mis brazos de `eval` usaban
    # el patrón de SQL y pasaban; con el de borrado, no. El test verde no cubría
    # el caso que importaba.
    (r'\brm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+)?-[a-zA-Z]*r[a-zA-Z]*\s+/\s*(?:["\']|$)',
     "rm -rf / is blocked — catastrophic filesystem deletion"),
    (r'\brm\s+(-[a-zA-Z]*r[a-zA-Z]*\s+)?-[a-zA-Z]*f[a-zA-Z]*\s+/\s*(?:["\']|$)',
     "rm -rf / is blocked — catastrophic filesystem deletion"),
    (r'\brm\s+-[a-zA-Z]*rf[a-zA-Z]*\s+/\*',
     "rm -rf /* is blocked — catastrophic filesystem deletion"),
    # git push --force to main/master
    (r'\bgit\s+push\s+.*--force.*\b(main|master)\b',
     "Force push to main/master is blocked"),
    (r'\bgit\s+push\s+.*-f\b.*\b(main|master)\b',
     "Force push to main/master is blocked"),
    # SQL destruction
    (r'\bDROP\s+(TABLE|DATABASE)\b',
     "DROP TABLE/DATABASE is blocked — use migrations instead"),
    # --- FALSO NEGATIVO hallado el 4-sep-2026 escribiendo el test del carril B ---
    # Los patrones de arriba anclan con `\\s*$` justo despues de la barra, asi que
    # una COMILLA DE CIERRE rompe el ancla y el borrado pasa:
    #
    #     rm -rf /      -> DENIEGA        rm -rf "/"   -> PERMITIA
    #     rm -rf /*     -> DENIEGA        rm -rf $HOME -> PERMITIA
    #
    # El caso de la variable es EXACTAMENTE el incidente del 9-ago que casi borra
    # /home/dadito y que origino la regla de oro de William ("ruta literal, nunca
    # variable"). O sea: el guardian no habria frenado el incidente que motivo la
    # regla que dice proteger.
    (r'\brm\s+(?:-\S+\s+)*-\S*[rf]\S*\s+["\']/["\']\s*$',
     "borrado recursivo de la raiz (entrecomillada) bloqueado"),
    (r'\brm\s+(?:-\S+\s+)*-\S*[rf]\S*\s+["\']?/\*',
     "borrado recursivo de la raiz bloqueado"),
    # Ruta por VARIABLE en un borrado recursivo: la regla de oro de William
    # (9-ago) lo prohibe SIEMPRE, se expanda a lo que se expanda. Un `$VAR` no se
    # puede auditar leyendo el comando, que es justo el punto de la regla.
    (r'\brm\s+(?:-\S+\s+)*-\S*[rf]\S*\s+["\']?\$',
     "borrado recursivo con ruta por VARIABLE: la regla de oro exige ruta literal"),
    # chmod 777
    (r'\bchmod\s+777\b',
     "chmod 777 is blocked — overly permissive permissions"),
    # mkfs on any device
    (r'\bmkfs\b',
     "mkfs is blocked — filesystem formatting requires manual confirmation"),
    # dd writing to block devices
    (r'\bdd\s+.*\bif=.*\bof=/dev/',
     "dd to /dev/ device is blocked — destructive disk write"),
    # fork bomb
    (r':\(\)\s*\{\s*:\|:\s*&\s*\}\s*;?\s*:',
     "Fork bomb detected and blocked"),
]

# Pre-compile for speed
_COMPILED_BLOCKLIST = [(re.compile(pat, re.IGNORECASE), msg) for pat, msg in BASH_BLOCKLIST]


# ---------------------------------------------------------------------------
# Posición EJECUTABLE vs texto que viaja como DATO
# ---------------------------------------------------------------------------
# POR QUÉ (carril B de Bob, 4-sep-2026): `pattern.search(command)` miraba TODO
# el string, así que un comando que sólo NOMBRA un patrón se veía igual que uno
# que lo EJECUTA. Medido ese día: 8 de 1.210 mensajes del canal contenían un
# patrón vigilado, y esos ocho eran los avisos de incidentes destructivos —la
# regla de oro de los destructivos, el incidente de /tmp, el informe sobre este
# mismo guardián—. Bloqueó a NEXUS 4 veces, a ADA 2 y a JARVIS 1, siempre
# escribiendo sobre el peligro. Es autosilenciador: bloquea el aviso de que
# bloquea.
#
# LA REGLA, y no es "si está entre comillas es dato" —eso abriría un agujero
# peor—: se exime SÓLO cuando la coincidencia cae ENTERA dentro de una región de
# datos. En `<borrado> "/"` el verbo queda AFUERA, la coincidencia cruza el
# borde y NO se exime. En `enviar 'ojo con <patrón>'` cae entera adentro y sí.
#
# Y NUNCA se exime nada si el comando puede EJECUTAR lo entrecomillado: `eval`,
# `bash -c`, `sh -c`, `python -c` reciben datos y los corren. Ahí lo citado es
# ejecutable y el guardián debe fallar CERRADO.

_EJECUTORES = re.compile(
    # `eval` y `source` como COMANDO. El `.` de POSIX (source) va anclado a
    # principio de comando: escrito como `\.` suelto dentro de la alternancia
    # matcheaba CUALQUIER punto --"seal_send.py", "aviso.md"-- y convertía en
    # ejecutor a todo comando que nombrara un archivo. Primer intento, medido y
    # corregido: eximía cero citas porque siempre creía estar ante un ejecutor.
    r'\beval\b|\bsource\b|(?:^|[;&|]|&&|\|\|)\s*\.\s+'
    # cualquier `-c <cadena>`: sh/bash/zsh, python, psql, y los que no conozco.
    # Es DELIBERADAMENTE ancho: un `-c` recibe una cadena y la ejecuta, y ante la
    # duda este guardián falla CERRADO. El costo es no eximir una cita que viaje
    # como argumento de un `-c` que no ejecute nada (`grep -c`), y ese costo se
    # paga: es un bloqueo de más, no un peligro de menos.
    r'|\s-c(?:\s|=)'
    r'|\bperl\s+-e\b|\bruby\s+-e\b|\bxargs\b|\bnohup\b'
    # EJECUTORES REMOTOS Y DIFERIDOS. Hallado por JARVIS revisando este carril
    # el 4-sep: `ssh <host> "<borrado>"` se eximia --el payload va entre comillas
    # y la region lo marcaba como dato-- y lo que viaja entre comillas de un ssh
    # SE EJECUTA EN OTRA MAQUINA. El criterio del carril es "solo donde es
    # ejecutable", y ahi es ejecutable: el que no lo veia era el guardian.
    # `tmux send-keys` escribe en una terminal viva; `at` y `watch` difieren la
    # ejecucion. Los cuatro se comprobaron PERMITIENDO antes de esta linea.
    r'|\bssh\b|\btmux\s+send-keys\b|\bat\s+now\b|\bwatch\b'
    r'|\bkubectl\s+exec\b|\bansible\b|\bsystemd-run\b',
    re.IGNORECASE,
)


def _regiones_de_datos(command: str) -> list[tuple[int, int]]:
    """Tramos [inicio, fin) que el shell trata como TEXTO, no como código.

    Cubre comillas simples, comillas dobles, cuerpos de heredoc y comentarios.
    Es un barrido de un solo paso: no pretende ser un parser de shell, y por eso
    ante la duda NO marca región (menos exenciones = fail-closed).
    """
    regiones: list[tuple[int, int]] = []
    i, n = 0, len(command)
    while i < n:
        c = command[i]
        if c in "'\"":
            j = command.find(c, i + 1)
            if j == -1:            # comilla sin cerrar: no es una region confiable
                break
            regiones.append((i + 1, j))
            i = j + 1
            continue
        if c == "#" and (i == 0 or command[i - 1] in " \t\n"):
            j = command.find("\n", i)
            j = n if j == -1 else j
            regiones.append((i, j))
            i = j
            continue
        if command.startswith("<<", i):
            m = re.match(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1", command[i:])
            if m:
                tag = m.group(2)
                nl = command.find("\n", i)
                if nl != -1:
                    cierre = re.search(r"^\s*" + re.escape(tag) + r"\s*$",
                                       command[nl + 1:], re.MULTILINE)
                    fin = nl + 1 + (cierre.start() if cierre else len(command) - nl - 1)
                    regiones.append((nl + 1, fin))
                    i = fin
                    continue
        i += 1
    return regiones


def _es_solo_una_cita(command: str, inicio: int, fin: int) -> bool:
    """True si la coincidencia [inicio, fin) cae ENTERA dentro de datos.

    SE RECORTAN LAS COMILLAS DEL BORDE antes de comparar. Los patrones terminan
    en `(?:["\']|$)` para ver el fin de un argumento entrecomillado --sin eso,
    `eval "<borrado> /"` no matcheaba y pasaba--, pero esa comilla que el patrón
    consume queda FUERA de la región de datos, que va entre las comillas. Sin
    este recorte, citar el patrón volvía a bloquearse: arreglar un lado rompía
    el otro. Medido las dos veces.
    """
    if _EJECUTORES.search(command):
        return False           # lo citado puede ejecutarse: no se exime nada
    while fin > inicio and command[fin - 1] in "\"' \t":
        fin -= 1
    return any(a <= inicio and fin <= b for a, b in _regiones_de_datos(command))


def check_bash_safety(command: str) -> dict | None:
    """Return a hook decision for dangerous/guarded commands, else None."""
    for pattern, message in _COMPILED_BLOCKLIST:
        for m in pattern.finditer(command):
            # `finditer` y no `search`: si la PRIMERA coincidencia es una cita
            # pero hay otra ejecutable más adelante, hay que denegar igual.
            if not _es_solo_una_cita(command, m.start(), m.end()):
                return deny(message)
    if os.environ.get("SEAL_COMMAND_INTENT_GUARD_ENFORCE", "0").strip().lower() not in {
        "1",
        "true",
        "yes",
    }:
        return None
    from command_intent_guard import audit_decision, evaluate_command

    decision = evaluate_command(command)
    audit_decision(command, decision)
    if decision.action == "deny":
        return deny(
            f"SEAL command intent guard [{decision.source}/{decision.category}]: "
            f"{decision.reason}"
        )
    if decision.action == "warn":
        return allow_with_updated_input(
            {"command": command},
            f"SEAL command intent guard warning: {decision.reason}",
        )
    return None


# Stable compatibility seam for the 1-Sep regression corpus.
def _var_destructive_kind(command: str) -> str:
    from command_intent_guard import _var_destructive_kind as classify

    return classify(command)


def main():
    raw = sys.stdin.read()
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        # Can't parse — passthrough
        print(json.dumps({}))
        return

    tool_name = event.get("tool_name", "")
    tool_input = event.get("tool_input", {})

    # -----------------------------------------------------------------------
    # Tier 1: MCP Identity Gate
    # -----------------------------------------------------------------------
    if tool_name.startswith("mcp__seal-memory__"):
        agent = os.environ.get("SEAL_AGENT", "")
        if agent not in ("ADA", "JARVIS", "ALICE", "DUM", "NEXUS"):
            print(json.dumps(deny(
                f"Unknown agent '{agent}' — SEAL_AGENT must be ADA, JARVIS, ALICE, DUM, or NEXUS"
            )))
            return

        # Auto-enrich memory_store
        if tool_name == "mcp__seal-memory__memory_store":
            updated = dict(tool_input)
            updated["agent"] = agent
            updated["timestamp"] = datetime.now(LIMA_TZ).isoformat()
            print(json.dumps(allow_with_updated_input(
                updated, f"Auto-enriched with agent={agent}"
            )))
            return

        # Auto-enrich memory_invalidate if missing agent
        if tool_name == "mcp__seal-memory__memory_invalidate":
            if "agent" not in tool_input or not tool_input["agent"]:
                updated = dict(tool_input)
                updated["agent"] = agent
                print(json.dumps(allow_with_updated_input(
                    updated, f"Injected agent={agent} into invalidate call"
                )))
                return

        # All other MCP calls — passthrough (agent is valid)
        print(json.dumps({}))
        return

    # -----------------------------------------------------------------------
    # Tier 2: Bash Safety
    # -----------------------------------------------------------------------
    if tool_name == "Bash":
        command = tool_input.get("command", "")
        result = check_bash_safety(command)
        if result:
            print(json.dumps(result))
            return
        print(json.dumps({}))
        return

    # -----------------------------------------------------------------------
    # Tier 3: Everything else — passthrough
    # -----------------------------------------------------------------------
    print(json.dumps({}))


if __name__ == "__main__":
    main()
