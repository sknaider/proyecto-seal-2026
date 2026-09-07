#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""clone_exec_gate.py — Gate de ejecución del CLON (carril FABLE). POLÍTICA: ALLOWLIST / DEFAULT-DENY.

CONTEXTO (William 5-jul): clones «mejores que el original» → necesitan autonomía para investigar,
PERO no pueden exfiltrar. Se wirea como PreToolUse[Bash] hook (JARVIS): auto-decide sin prompt
interactivo (evita el cuelgue headless) devolviendo allow/deny.

POR QUÉ ALLOWLIST y no denylist (lección por efecto 5-jul): un DENYLIST bajo default-allow se
bypassea con ofuscación infinita (python3 -c os.system(base64...), eval, hex, $IFS...). Un filtro
de comandos NUNCA atrapa todo el exfil. Por eso: DEFAULT-DENY — solo corre lo explícitamente SEGURO;
todo lo desconocido/ofuscado → deny. (El egress de red es el backstop complementario, capa aparte.)

Diseño: parte el comando por operadores de shell (; && || | & \n) y exige que CADA sub-comando sea
seguro. Un solo sub-comando no-safe → deny todo. Rutas sensibles (.atrest.key, tokens, .env,
/etc/shadow) → deny aunque el comando base sea safe.

FABLE define la política; JARVIS wirea el hook; FABLE la red-teamea por efecto.
"""
from __future__ import annotations
import json
import re
import shlex
import sys

# ── Comandos base SEGUROS (read-only / investigación / trabajo local, sin red ni exec arbitrario) ──
SAFE_CMDS = {
    "ls", "pwd", "cd", "find", "tree", "stat", "file", "wc", "du", "df",
    "cat", "head", "tail", "less", "more", "nl",              # lectura (gated por ruta sensible abajo)
    "grep", "egrep", "fgrep", "rg", "ag", "ack",              # búsqueda
    "echo", "printf", "date", "whoami", "hostname", "uname", "id",
    "git",                                                     # gated a subcomandos read-only abajo
    "jq", "column", "sort", "uniq", "cut", "sed",             # texto (sed sin -i/destructivo abajo)
    "diff", "comm", "md5sum", "sha256sum",
}
# git: solo subcomandos de LECTURA
GIT_READONLY = {"status", "diff", "log", "show", "branch", "remote", "config", "blame", "ls-files", "rev-parse"}
# python/node: SOLO scripts allowlisted (NO -c, NO módulos arbitrarios) → mata el bypass base64/os.system
PY_ALLOWED_SCRIPTS = re.compile(r"^(python3?|node)\s+([^\s;|&]*/)?(recall|minisoul_boot|whoami)\.py(\s|$)", re.I)

# ── Rutas SENSIBLES: ni un comando safe puede tocarlas (credenciales/llaves/secretos) ──
SENSITIVE = re.compile(
    r"\.atrest\.key|\.seal/(?:[^\s]*\.(key|enc)|tokens?/)|token\.enc|atrest_secret|"
    r"\.db_cred|\.pgpass|\.env\b|id_rsa|id_ed25519|/etc/(passwd|shadow|sudoers)|\.ssh/|\.cred\b|secret|"
    r"\$[{(]?(?:[A-Z0-9_]*_)?(?:TOKEN|SECRET|KEY|PASSWORD|PASS|CRED|API_KEY|AUTH)[A-Z0-9_]*[)}]?",
    re.I)
# ── Señales de exec arbitrario / ofuscación (deny aunque empiece con algo safe) ──
OBFUSC = re.compile(
    r"-c\s|eval\b|base64\s+-d|b64decode|os\.system|subprocess|__import__|exec\(|"
    r"`|\$\(|\bsource\b|/dev/tcp|\bsystem\s*\(",
    re.I)
# Split por operadores shell. El `&` suelto (background/chain) SÍ separa; pero NO el `&` de un
# redirect (2>&1, &>, >&, 1>&2) — ese es benigno. Lookaround: `&` no precedido/seguido de `>`/`&`.
# (catch de 3 clones 5-jul: «2>&1» se fragmentaba → falso-positivo que bloqueaba comandos legítimos.)
SHELL_SPLIT = re.compile(r"\s*(?:;|&&|\|\||\||(?<![>&\d])&(?![>&])|\n)\s*")


def _sub_ok(sub: str) -> tuple[bool, str]:
    sub = sub.strip()
    if not sub:
        return True, "vacio"
    if SENSITIVE.search(sub):
        return False, "ruta_sensible"
    if OBFUSC.search(sub):
        return False, "exec_arbitrario/ofuscacion"
    # python/node: solo scripts allowlisted
    try:
        toks = shlex.split(sub)
    except Exception:
        return False, "parse_fail"          # no parseable → deny (default-deny)
    if not toks:
        return False, "vacio"
    base = toks[0].split("/")[-1]
    if base in ("python", "python3", "node"):
        return (True, "script_allowlisted") if PY_ALLOWED_SCRIPTS.search(sub) else (False, "interprete_arbitrario")
    if base == "git":
        return (True, "git_readonly") if (len(toks) > 1 and toks[1] in GIT_READONLY) else (False, "git_no_readonly")
    if base == "sed" and "-i" in toks:
        return False, "sed_in_place_destructivo"
    if base == "sed" and any("e" in t and re.search(r"s.*/.*e$", t) for t in toks[1:]):
        return False, "sed_exec_destructivo"
    if base in SAFE_CMDS:
        return True, "safe"
    return False, "no_en_allowlist"          # DEFAULT-DENY


def evaluate(command: str) -> dict:
    cmd = command or ""
    if not cmd.strip():
        return {"decision": "deny", "reason": "comando_vacio"}
    for sub in SHELL_SPLIT.split(cmd):
        ok, why = _sub_ok(sub)
        if not ok:
            return {"decision": "deny",
                    "reason": f"gate_clon(allowlist): «{sub.strip()[:60]}» → {why}. Solo corre lo "
                              f"explícitamente seguro (investigación read-only + recall). Exec/red/credenciales requieren gate humano."}
    return {"decision": "allow", "reason": "todos_los_subcomandos_safe"}


# ── Tools de RUTA (Read/Edit/Write/Glob/Grep): auto-decide por path para evitar el cuelgue del
#    prompt interno headless, y denegar lectura/escritura de rutas sensibles (llaves de hermanos,
#    credenciales). allow del proyecto, deny de ~/.seal/*.key + credenciales. Complementa el egress
#    inexistente: aunque no puede exfiltrar, evita exponer la llave de otro agente en el contexto. ──
def evaluate_path_tool(tool: str, tinput: dict) -> dict:
    # rutas que puede traer el evento según la tool
    paths = []
    for k in ("file_path", "path", "notebook_path"):
        v = tinput.get(k)
        if isinstance(v, str):
            paths.append(v)
    # Grep/Glob traen 'pattern'+'path'; si el pattern apunta a algo sensible, también deny
    blob = " ".join(str(v) for v in tinput.values() if isinstance(v, str))
    if SENSITIVE.search(blob) or any(SENSITIVE.search(p) for p in paths):
        return {"decision": "deny",
                "reason": f"gate_clon(path): {tool} sobre ruta sensible (llave/credencial de otro agente). "
                          f"Denegado — el clon no lee/escribe secretos ajenos."}
    return {"decision": "allow", "reason": f"{tool}_ruta_ok"}


def main():
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    ev = {}
    if raw.strip():
        try:
            ev = json.loads(raw)
        except Exception:
            ev = {"tool_input": {"command": raw}}
    tool = ev.get("tool_name") or ""
    tinput = ev.get("tool_input", {}) or {}
    if tool in ("Read", "Edit", "Write", "Glob", "Grep", "NotebookEdit"):
        res = evaluate_path_tool(tool, tinput)
    else:
        cmd = tinput.get("command", "") or ev.get("command", "")
        if not cmd and len(sys.argv) > 1:
            cmd = " ".join(sys.argv[1:])
        res = evaluate(cmd)
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                              "permissionDecision": "allow" if res["decision"] == "allow" else "deny",
                                              "permissionDecisionReason": res["reason"]}}, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
