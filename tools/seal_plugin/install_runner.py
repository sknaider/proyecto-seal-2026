#!/usr/bin/env python3
"""
SEAL Plugin — install-runner: capa de VALIDACIÓN de sandbox (NEXUS 2026-07-09, adopción Claude #4).
====================================================================================================
Segundo incremento del plugin SEAL. Valida el PLAN de instalación de un plugin contra los límites
de sandbox de R4 (FABLE), FAIL-CLOSED — SIN EJECUTAR NADA en esta capa.

FRONTERA (R4 de FABLE):
  - install_script es DINÁMICO → corre en sandbox: fs read-only salvo destinos DECLARADOS,
    env en WHITELIST, sin red salvo hosts DECLARADOS, y APROBACIÓN ÚNICA de FABLE por efecto.
  - ESTA capa solo VALIDA que el plan esté dentro de esos límites (dry-run). La ejecución real
    (apply) exige un token de aprobación de FABLE y NO está implementada acá (gated).

builder≠verifier: NEXUS valida el plan; FABLE verifica adversarialmente (intenta escapar el sandbox).

Uso:
    from install_runner import validate_install_plan
    ok, violations, report = validate_install_plan(plan, plugin_dir)   # NO ejecuta
"""
from __future__ import annotations
import os

# Roots donde un install de plugin PUEDE escribir (least-privilege; ampliable por política explícita).
# NOTA (FABLE R4): /tmp NO se permite — es world-writable/readable (superficie de fuga). Un plugin que
# necesite temp usa su propio dir scopeado bajo ~/.seal/plugins/<name>/tmp, con perms restringidos.
_ALLOWED_WRITE_ROOTS = (
    os.path.expanduser("~/.seal/plugins"),
    os.path.expanduser("~/.claude/skills"),           # symlink al repo skills
    "/home/dadito/IA/proyecto-seal/skills",
)
# Env vars que un install puede leer (whitelist). Nada de secretos/tokens.
_ENV_WHITELIST = {"SEAL_AGENT", "SEAL_HOME", "HOME", "PATH", "PWD", "SEAL_PLUGIN_DIR"}


def _within(path: str, roots) -> bool:
    rp = os.path.realpath(path)
    return any(rp == os.path.realpath(r) or rp.startswith(os.path.realpath(r) + os.sep) for r in roots)


def validate_install_plan(plan: dict, plugin_dir: str) -> tuple[bool, list[str], dict]:
    """Valida un plan de instalación contra el sandbox R4. NO ejecuta. Devuelve (ok, violaciones, reporte)."""
    v: list[str] = []
    plan = plan or {}
    plugin_dir_real = os.path.realpath(plugin_dir)

    # 1) install_script: debe existir y estar DENTRO del dir del plugin (sin traversal)
    script = plan.get("install_script")
    if script:
        sp = os.path.realpath(os.path.join(plugin_dir, script)) if not os.path.isabs(script) else os.path.realpath(script)
        if not (sp == plugin_dir_real or sp.startswith(plugin_dir_real + os.sep)):
            v.append(f"install_script fuera del dir del plugin (traversal): {script}")

    # 2) destinos de escritura declarados: TODOS deben caer en roots permitidos (fail-closed)
    for dest in (plan.get("write_destinations") or []):
        if not _within(dest, _ALLOWED_WRITE_ROOTS):
            v.append(f"destino de escritura fuera de límites: {dest}")

    # 3) env solicitado: solo whitelist
    for e in (plan.get("env") or []):
        if e not in _ENV_WHITELIST:
            v.append(f"env no permitido (fuera de whitelist): {e}")

    # 4) red: solo hosts declarados; y si declara red, se marca para aprobación reforzada
    net_hosts = plan.get("network_hosts") or []
    needs_network = bool(net_hosts)

    report = {
        "static_ok": len(v) == 0,
        "install_script": script,
        "write_destinations": plan.get("write_destinations") or [],
        "env": plan.get("env") or [],
        "needs_network": needs_network,
        "network_hosts": net_hosts,
        # La ejecución real SIEMPRE requiere aprobación única de FABLE por efecto (no automatizable acá).
        "requires_fable_approval": True,
        "executed": False,   # esta capa NUNCA ejecuta
    }
    return (len(v) == 0), v, report


def apply_install(*_args, **_kwargs):
    """Ejecución real del install_script en sandbox. GATED: exige token de aprobación de FABLE por efecto.
    NO implementado en este incremento — es el paso security-critical que se buildea con pairing de FABLE."""
    raise NotImplementedError(
        "apply_install gated: requiere sandbox real (fs/env/red restringidos) + aprobación única de FABLE. "
        "Se implementa con pairing adversario de FABLE, no en un solo-edit."
    )


if __name__ == "__main__":
    import json, sys
    if len(sys.argv) < 3:
        print("uso: python3 install_runner.py <plan.json> <plugin_dir>", file=sys.stderr); raise SystemExit(2)
    plan = json.loads(open(sys.argv[1]).read())
    ok, viol, rep = validate_install_plan(plan, sys.argv[2])
    print(json.dumps({"valid": ok, "violations": viol, "report": rep}, indent=2, ensure_ascii=False))
    raise SystemExit(0 if ok else 1)
