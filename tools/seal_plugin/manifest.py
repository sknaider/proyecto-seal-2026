#!/usr/bin/env python3
"""
SEAL Plugin — validador de MANIFIESTO ESTÁTICO (NEXUS 2026-07-09, adopción Claude #4).
=====================================================================================
Primer incremento del plugin SEAL (onboarding 1-comando que mata el config-drift).
Diseño clean-room (patrón de Claude Code, NUESTRO código — no corre bytes de terceros).

FRONTERA DE SEGURIDAD (R4 de FABLE): esto SOLO valida el manifiesto ESTÁTICO — parseo y
chequeo de estructura, CERO ejecución. El install/script.py (parte dinámica) NO se toca acá:
va en sandbox con aprobación única de FABLE por efecto. Este archivo es 100% revisable a ojo.

Un plugin SEAL empaqueta (declarativo): skills, hooks, mcpServers, migrations, install_script.
`seal plugin install X` → (1) valida manifest (acá), (2) FABLE vetea el install_script, (3) aplica.

Uso:
    from manifest import validate_manifest
    ok, errors, meta = validate_manifest(json_dict)     # NO ejecuta nada
    python3 manifest.py examples/plugin.json            # CLI: valida un manifest
"""
from __future__ import annotations
import json
import re
import sys

# Campos requeridos del manifest (pura metadata, sin ejecución).
_REQUIRED = ("name", "version")
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$")
_ALLOWED_KEYS = {
    "name", "version", "description", "author", "dependencies",
    "skills", "hooks", "mcpServers", "migrations", "install_script", "userConfig",
}


def validate_manifest(m: dict) -> tuple[bool, list[str], dict]:
    """Valida un manifest de plugin SEAL. NO ejecuta nada. Devuelve (ok, errores, meta_normalizada)."""
    errors: list[str] = []
    if not isinstance(m, dict):
        return False, ["manifest no es un objeto JSON"], {}

    # claves desconocidas (fail-closed: no aceptar campos no reconocidos)
    unknown = set(m) - _ALLOWED_KEYS
    if unknown:
        errors.append(f"claves desconocidas (no permitidas): {sorted(unknown)}")

    for req in _REQUIRED:
        if not m.get(req):
            errors.append(f"falta campo requerido: {req}")

    name = m.get("name", "")
    if name and not _NAME_RE.match(str(name)):
        errors.append(f"name inválido: {name!r} (usar [a-z0-9._-], 2-64 chars)")

    ver = m.get("version", "")
    if ver and not _SEMVER_RE.match(str(ver)):
        errors.append(f"version no es semver: {ver!r} (ej. 1.0.0)")

    # listas declarativas: deben ser listas de strings/objetos, sin código embebido
    for key in ("skills", "hooks", "mcpServers", "migrations"):
        val = m.get(key)
        if val is not None and not isinstance(val, list):
            errors.append(f"{key} debe ser una lista")

    # install_script: SOLO se declara la RUTA (string). Su contenido/ejecución NO se valida acá
    # (eso es la capa dinámica con sandbox + veto de FABLE, R4). Marcamos si está presente.
    has_install = bool(m.get("install_script"))
    if has_install and not isinstance(m["install_script"], str):
        errors.append("install_script debe ser una RUTA (string), no código inline")

    meta = {
        "name": name, "version": ver,
        "counts": {k: len(m.get(k) or []) for k in ("skills", "hooks", "mcpServers", "migrations")},
        "has_install_script": has_install,       # → requiere veto de FABLE antes de correr
        "static_only": not has_install,          # sin install_script = 100% estático/seguro
    }
    return (len(errors) == 0), errors, meta


def main() -> int:
    if len(sys.argv) < 2:
        print("uso: python3 manifest.py <plugin.json>", file=sys.stderr)
        return 2
    try:
        m = json.loads(open(sys.argv[1], encoding="utf-8").read())
    except Exception as e:
        print(f"[error] no se pudo leer/parsear: {e}", file=sys.stderr)
        return 2
    ok, errors, meta = validate_manifest(m)
    print(json.dumps({"valid": ok, "errors": errors, "meta": meta}, indent=2, ensure_ascii=False))
    if meta.get("has_install_script"):
        print("\n⚠️  install_script presente → requiere veto de provenance de FABLE (R4) + sandbox antes de correr.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
