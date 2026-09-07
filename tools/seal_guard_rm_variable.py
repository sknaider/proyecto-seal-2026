#!/usr/bin/env python3
"""Hook PreToolUse (Bash) — REGLA DE ORO de William (9-ago-2026, reafirmada 7-sep-2026 18:24):
borrados SOLO con ruta LITERAL escrita a mano; NUNCA una variable ni un glob.

Por qué existe como MECANISMO y no como texto: la regla estaba escrita en CLAUDE.md y aun así
ALICE (1-sep 13:35), JARVIS (1-sep 15:34) y NEXUS (7-sep 16:19→18:24, dos horas) quedaron congelados
en el prompt «Dangerous rm operation on possibly-empty variable path» de Claude Code. Ese candado
PREGUNTA y deja al agente mudo hasta que un humano contesta. Este hook NIEGA en el acto, sin prompt,
y devuelve la forma que sí se escribe. Sólo stdlib; corre en < 20 ms.

Qué niega (cuando la palabra de comando es rm/rmdir/unlink, con o sin sudo/env delante):
  - un argumento de ruta con variable:   rm -rf "$T"   rm "${DIR}/x"   rm -r ~/algo   rm $(cmd)
  - un argumento de ruta con glob:        rm "$M"/*.json   rm -f /tmp/x/*.log   rm foo?
Qué permite:
  - rm con rutas literales:               rm -rf /tmp/seal-arena-abc123   rm -f /tmp/x/a.log
  - la forma documentada para temporales: find "$DIR" -mindepth 1 -delete
Salida: JSON permissionDecision=deny con el motivo y el reemplazo; o nada (exit 0) si no aplica.
"""
import json
import re
import sys

_SEP = re.compile(r"(?:\|\||&&|;|\||\n)")
_PREFIJO = re.compile(r"^(?:\s*(?:sudo(?:\s+-\S+)*|env|command|nice|nohup|timeout\s+\S+|[A-Za-z_][A-Za-z0-9_]*=\S*)\s+)*")
_CMD = re.compile(r"^\s*(rm|rmdir|unlink)\b(.*)$", re.S)
_VARIABLE = re.compile(r"\$|~(?:/|$)|`")
_GLOB = re.compile(r"[*?\[]")

MENSAJE = (
    "BLOQUEADO por la regla de oro de William (9-ago/7-sep-2026): un borrado sólo acepta RUTAS LITERALES "
    "escritas a mano; nunca una variable ni un glob. Encontré: {malos}. "
    "Escribí en su lugar: (a) la ruta literal completa, p. ej. rm -rf /tmp/seal-arena-abc123; "
    "(b) para vaciar un temporal: find \"$DIR\" -mindepth 1 -delete; "
    "(c) en /tmp no limpies: el sistema lo vacía solo. Si esto es DOCUMENTACIÓN, escribila con Write/Edit, no por bash."
)


def _args_de_ruta(resto: str) -> list[str]:
    out = []
    for tok in resto.strip().split():
        if tok == "--":
            continue
        if tok.startswith("-") and not tok.startswith(("-$", "-~")):
            continue
        out.append(tok)
    return out


def segmentos(command: str) -> list[str]:
    return [s for s in _SEP.split(command) if s and s.strip()]


def evaluar(command: str) -> list[str]:
    """Devuelve la lista de argumentos ofensivos (vacía = permitido)."""
    malos: list[str] = []
    for seg in segmentos(command):
        seg = _PREFIJO.sub("", seg, count=1)
        m = _CMD.match(seg)
        if not m:
            continue
        for tok in _args_de_ruta(m.group(2)):
            if _VARIABLE.search(tok) or _GLOB.search(tok):
                malos.append(tok)
    return malos


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    if data.get("tool_name") != "Bash":
        return 0
    command = str((data.get("tool_input") or {}).get("command") or "")
    malos = evaluar(command)
    if not malos:
        return 0
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                      "permissionDecisionReason": MENSAJE.format(malos=", ".join(malos[:4]))}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
