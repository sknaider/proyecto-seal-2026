#!/usr/bin/env python3
"""
test_gateway_guard_coverage.py — Prueba-de-NO-recurrencia (ALICE, 2026-06-09).

Garantía estructural de la cura C2 (Fase B): TODO gateway con `_dispatch = {...}` en
mcp_server_v4.py DEBE cruzar el guard de privacidad (_privacy_check) antes de despachar a
sus funciones internas. Si mañana alguien agrega un 5to gateway sin guard, este test FALLA.

Convierte el hallazgo del 9-jun (4 gateways, solo memory_gateway con check) en una guardia
permanente: no depende de 'acordarse de poner el check', lo verifica la máquina.

Uso:  python3 test_gateway_guard_coverage.py [/ruta/a/mcp_server_v4.py]
Exit 0 = todos los gateways cubiertos · Exit 1 = hay gateway(s) sin guard.
"""
import sys, re

MCP = sys.argv[1] if len(sys.argv) > 1 else "/home/dadito/IA/proyecto-seal/memory/mcp_server_v4.py"
GUARD = "_privacy_check"          # el chokepoint que todo gateway debe cruzar
WINDOW = 60                       # líneas tras el _dispatch donde debe aparecer el guard


def gateway_name_for(lines, dispatch_lineno):
    """Nombre de la función (async def / def) que contiene este _dispatch."""
    name = "?"
    for i in range(dispatch_lineno, -1, -1):
        m = re.match(r"\s*(?:async\s+)?def\s+([a-zA-Z_][a-zA-Z0-9_]*)", lines[i])
        if m:
            return m.group(1)
    return name


def main():
    src = open(MCP, encoding="utf-8").read()
    lines = src.splitlines()
    failures, checked = [], []
    for idx, line in enumerate(lines):
        if re.search(r"_dispatch\s*=\s*\{", line):
            gw = gateway_name_for(lines, idx)
            window = "\n".join(lines[idx: idx + WINDOW])
            has_guard = GUARD in window
            checked.append((gw, idx + 1, has_guard))
            if not has_guard:
                failures.append((gw, idx + 1))

    print(f"Gateways con _dispatch encontrados: {len(checked)}")
    for gw, ln, ok in checked:
        print(f"  {'PASS' if ok else 'FAIL'}  {gw} (L{ln}) — guard {GUARD} {'presente' if ok else 'AUSENTE'}")

    if failures:
        print(f"\n❌ {len(failures)} gateway(s) SIN guard de privacidad — C2 NO cubierto:")
        for gw, ln in failures:
            print(f"   - {gw} (L{ln})")
        print("Cura C2 incompleta: cada gateway debe cruzar _privacy_check antes de despachar.")
        sys.exit(1)
    print(f"\n✅ {len(checked)}/{len(checked)} gateways cruzan el guard. C2 cubierto a nivel estructural.")
    sys.exit(0)


if __name__ == "__main__":
    main()
