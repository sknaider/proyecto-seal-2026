#!/usr/bin/env python3
"""Manifiestos cuyo `independent_reviewer` NO puede firmar hoy: nadie con ese nombre existe.

Medido el 7-sep-2026: 18 manifiestos nombran revisores que no son agentes vivos (DARWIN 6,
WEGENER 3, PLATFORM_SECURITY_AUDIT 2, y ocho nombres mas). JARVIS ya habia reasignado UNO de
esos casos a mano ese mismo dia -F3_HARNESS_AUDIT, "ya no es un agente"-, y el mismo nombre
seguia en OTRO manifiesto que nadie miro. El arreglo de a uno no encuentra a los demas.

LO QUE ESTO NO AFIRMA (ADA, 7-sep): que esas identidades nunca fueran legitimas. Un arnes o una
auditoria externa pudo ser un revisor real en su momento. Lo que si queda medido es que HOY nadie
puede asumir esa revision, y por lo tanto el carril no puede cerrarse como esta.

La lista de agentes vivos se DERIVA, no se cablea: tokens de sesion en messages/ (quien puede
autenticarse) unidos a soul_v3.agents si la base responde. Si no se puede derivar NINGUNA lista,
sale 2: un detector que no pudo mirar no dice "limpio".

Salidas: 0 todos los revisores existen · 1 hay revisores que no pueden firmar · 2 no se pudo mirar.
"""
from __future__ import annotations
import json, pathlib, subprocess, sys

RAIZ = pathlib.Path(__file__).resolve().parents[1]


def agentes_vivos() -> set[str]:
    vivos = {p.name.replace(".agent_session_token_", "")
             for p in (RAIZ / "messages").glob(".agent_session_token_*")}
    try:
        r = subprocess.run(
            ["docker", "exec", "seal-memory-db", "psql", "-U", "seal", "-d", "seal_memory", "-At",
             "-c", "SELECT name FROM soul_v3.agents;"],
            capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            vivos |= {n.strip() for n in r.stdout.splitlines() if n.strip()}
    except (OSError, subprocess.SubprocessError):
        pass          # la base puede no estar; los tokens alcanzan para decidir
    return vivos


def revisar(carpeta: pathlib.Path, vivos: set[str]) -> dict[str, list[str]]:
    huerfanos: dict[str, list[str]] = {}
    for ruta in sorted(carpeta.glob("*.json")):
        try:
            m = json.loads(ruta.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            continue
        if not isinstance(m, dict):
            continue
        revisor = m.get("independent_reviewer")
        if isinstance(revisor, str) and revisor and revisor not in vivos:
            huerfanos.setdefault(revisor, []).append(ruta.stem)
    return huerfanos


def main(argv: list[str]) -> int:
    carpeta = pathlib.Path(argv[1]) if len(argv) > 1 else RAIZ / "quality" / "manifests"
    # Una lista inyectada VACIA no debe degradar a "derivala sola" ni convertirse en {""}:
    # con argv[2]="" el split da [""], un conjunto NO vacio con un nombre falso, y el detector
    # marcaba a TODOS como huerfanos creyendo que tenia lista. Lo encontro mi propio brazo de control.
    if len(argv) > 2:
        vivos = {x.strip() for x in argv[2].split(",") if x.strip()}
    else:
        vivos = agentes_vivos()
    if not carpeta.is_dir() or not any(carpeta.glob("*.json")):
        print(f"SIN MIRAR: no hay manifiestos en {carpeta}.")
        return 2
    if not vivos:
        print("SIN MIRAR: no se pudo derivar la lista de agentes vivos (ni tokens ni base).")
        return 2
    huerfanos = revisar(carpeta, vivos)
    total = 0
    for revisor, manifiestos in sorted(huerfanos.items(), key=lambda kv: -len(kv[1])):
        print(f"NADIE PUEDE FIRMAR COMO  {revisor}  ({len(manifiestos)} manifiestos)")
        for m in manifiestos:
            print(f"    {m}")
        total += len(manifiestos)
    print(f"\nagentes vivos: {', '.join(sorted(vivos)) if len(vivos) < 12 else str(len(vivos)) + ' nombres'}"
          f"\n{total} manifiestos con un revisor que no puede firmar hoy")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
