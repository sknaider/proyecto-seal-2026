#!/usr/bin/env python3
"""
test_boot_skill_core_within_window.py — guard del hallazgo de FABLE (6-ago, medido por efecto).

El loader de boot (`mcp_server_v4.py`, boot_context) inyecta por cada skill `boot_load=true` solo
los **primeros 800 caracteres** del SKILL.md (`content[:800]`), FRONTMATTER incluido. Si el núcleo
ENFORCEABLE de una skill (los pasos/estados que hay que cumplir) cae DESPUÉS de esa ventana, el boot
carga el preámbulo y NO el procedimiento — el boot-load queda hueco.

Discoverable por pytest (test_* + parametrize) Y ejecutable como script. Fail-closed: una
boot-skill que no esté ni en CORE_MARKERS ni en NO_ENFORCEABLE_CORE **falla**.

IDENTIDAD (limitación honesta, hallazgo #3 de ADA/FABLE): el loader REAL enumera las boot-skills con
el rol per-agente restringido (`mcp_runtime_<agente>` vía `resolve_mcp_agent_db_url`), no con `seal`.
Este test enumera con el DSN de `SEAL_MCP_AGENT_DSN` si está seteado (identidad correcta); si no, cae
a `pg_dsn` y AVISA RUIDOSO que corrió elevado — la paridad de visibilidad-RLS queda por verificar con
el rol per-agente (frontera de ADA). La verificación de TRUNCACIÓN (el valor central) lee ARCHIVOS y
es independiente de la identidad, así que sigue siendo válida en ambos casos.
"""
import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "memory"))
from seal_secrets import pg_dsn  # noqa: E402
import asyncpg  # noqa: E402

BOOT_WINDOW = 800  # DEBE coincidir con content[:800] del loader en mcp_server_v4.py

CORE_MARKERS = {
    "verification-before-completion": [
        "TERMINAL_OWNER", "PARTIAL", "COMPLETED", "ONLY THEN"
    ],  # contrato de ownership + estado + último paso del gate
    "verify-true-green-tests": ["TRUE_GREEN", "INDETERMINATE"],  # INDETERMINATE = último de los 5 estados
}
NO_ENFORCEABLE_CORE = {
    "seal-agent-lifecycle", "seal-agent-debug", "seal-memory-ops",
    "seal-mcp-tool-authoring", "seal-schema-guard",
}


def _enum_dsn() -> str:
    """DSN para enumerar boot-skills. Prefiere la identidad per-agente real; si no, avisa RUIDOSO."""
    dsn = os.environ.get("SEAL_MCP_AGENT_DSN")
    if dsn:
        return dsn
    print("⚠️  [identidad] enumerando con pg_dsn (posible superuser), NO el rol per-agente del "
          "loader. Paridad de visibilidad-RLS SIN verificar (setear SEAL_MCP_AGENT_DSN con un "
          "mcp_runtime_<agente> para cerrar #3).", file=sys.stderr)
    return pg_dsn(required=True)


def _fetch_boot_skills() -> list[tuple[str, str]]:
    """(name, skill_path) — el MISMO WHERE EXACTO del loader (mcp_server_v4:3828): sin invalid_at."""
    async def _q():
        c = await asyncpg.connect(_enum_dsn())
        try:
            rows = await c.fetch(
                "SELECT name, skill_path FROM soul_v3.skills "
                "WHERE boot_load = true AND pending_review = false ORDER BY name")
            return [(r["name"], r["skill_path"]) for r in rows]
        finally:
            await c.close()
    return asyncio.run(_q())


BOOT_SKILLS = _fetch_boot_skills()  # colectado en import-time para parametrizar (fail-loud sin DB)


def _core_in_window(content: str, marker: str) -> bool:
    """La string COMPLETA del marcador debe estar dentro de content[:BOOT_WINDOW] (no cortada a mitad)."""
    return marker in content[:BOOT_WINDOW]


def test_hay_boot_skills():
    assert BOOT_SKILLS, "no hay boot-skills elegibles (boot_load=true, pending_review=false)"


def test_cebo_detecta_truncacion():
    """CONTROL NEGATIVO (cebo): un núcleo DESPUÉS de la ventana DEBE detectarse como fuera.
    Si esto pasara, el test sería vacuo — nunca podría fallar por truncación."""
    synthetic = ("x" * (BOOT_WINDOW + 50)) + "\nENFORCEABLE_STEP"   # marcador más allá de 800
    assert not _core_in_window(synthetic, "ENFORCEABLE_STEP"), "el cebo debería estar fuera de la ventana"
    inside = "ENFORCEABLE_STEP en el arranque"                       # marcador dentro
    assert _core_in_window(inside, "ENFORCEABLE_STEP"), "un marcador temprano debería estar dentro"


@pytest.mark.parametrize("name,path", BOOT_SKILLS, ids=[n for n, _ in BOOT_SKILLS])
def test_boot_skill_registered_and_core_in_window(name, path):
    assert name in CORE_MARKERS or name in NO_ENFORCEABLE_CORE, (
        f"boot-skill '{name}' sin clasificar: agregala a CORE_MARKERS (si tiene núcleo "
        f"obligatorio) o a NO_ENFORCEABLE_CORE (si es de referencia). No se silencia.")
    markers = CORE_MARKERS.get(name)
    if not markers:
        return
    assert path, f"{name}: skill_path vacío"
    content = Path(path).read_text(encoding="utf-8")
    for m in markers:
        assert _core_in_window(content, m), (
            f"{name}: el núcleo '{m}' cae fuera de los {BOOT_WINDOW} chars del boot "
            f"(offset {content.find(m)}) → boot-load hueco, reordená/trimeá el SKILL.md")


def main() -> int:
    fails = 0
    try:
        test_cebo_detecta_truncacion()
        print("[PASS] cebo (control negativo): el test PUEDE detectar truncación")
    except AssertionError as e:
        print(f"[FAIL] cebo: {e}"); fails += 1
    for name, path in BOOT_SKILLS:
        try:
            test_boot_skill_registered_and_core_in_window(name, path)
            print(f"[PASS] {name}")
        except AssertionError as e:
            print(f"[FAIL] {name}: {e}"); fails += 1
    print("\n" + ("RESULTADO: TODO PASS ✅" if not fails else f"RESULTADO: {fails} fallo(s) ❌"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
