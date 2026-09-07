#!/usr/bin/env python3
"""
test_skill_loader_L1.py — Test POR-EFECTO de progressive-disclosure L1 (adopción patrón Claude, #3).
Builder: NEXUS. Verificación de seguridad R3 (provenance/inyección): FABLE (independiente, DELEGATE-52).

Criterio de hecho (SPEC_SOUL_ADOPT_CLAUDE_PATTERNS_v1.md #3):
  "una skill L1 no carga su body hasta invocarse."

Prueba por efecto (sin tocar el loader): tras discover() el body (L2) NO está en memoria ni en el
catálogo; recién get()/build_prompt() lo materializan. Cero red, cero DB — temp dir + stdlib.

  python3 test_skill_loader_L1.py        # exit 0 = PASS, 1 = FAIL
"""
from __future__ import annotations
import sys, tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import skill_loader as sl  # noqa: E402

MARKER = "__L2_BODY_MARKER_no_debe_estar_en_L1__"
SKILL_MD = f"""---
name: l1_probe
description: Skill de prueba para progressive disclosure L1
when_to_use: solo en el test por-efecto
---
Este es el BODY (L2) de la skill. {MARKER}
Instrucciones extensas que NO deben entrar al contexto hasta invocar.
"""


def main() -> int:
    fails: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "skills").mkdir()
        (d / "skills" / "l1_probe.md").write_text(SKILL_MD, encoding="utf-8")
        empty_root = Path(td) / "empty_root"
        empty_root.mkdir()

        loader = sl.SkillLoader(extra_dirs=[d / "skills"], project_root=empty_root)
        loader.discover()

        s = loader._skills.get("l1_probe")
        if s is None:
            print("FAIL — la skill de prueba no fue descubierta")
            return 1

        # 1) Tras discover(): L1 puro — body diferido, content vacío, marker ausente.
        if s._body_loaded is not False:
            fails.append(f"L1: _body_loaded debería ser False tras discover, es {s._body_loaded!r}")
        if s.content != "":
            fails.append(f"L1: content debería estar vacío tras discover, tiene {len(s.content)} chars")
        if MARKER in (s.content or ""):
            fails.append("L1: el MARKER del body está presente tras discover (no debería)")

        # 2) El catálogo L1 (render_catalog) expone name+description pero NUNCA el body.
        cat = loader.render_catalog()
        if "l1_probe" not in cat:
            fails.append("catálogo L1 no incluye el name de la skill")
        if MARKER in cat:
            fails.append("catálogo L1 FILTRA el body (MARKER presente) — rompe progressive disclosure")

        # 3) get() = intención de usar → materializa L2 (body cargado, marker presente).
        s2 = loader.get("l1_probe")
        if s2 is None or s2._body_loaded is not True:
            fails.append("get(): no materializó L2 (_body_loaded True esperado)")
        if MARKER not in (s2.content if s2 else ""):
            fails.append("get(): el body (MARKER) NO se cargó al invocar")

        # 4) Un discover() fresco vuelve a L1 (body diferido de nuevo) — la carga no es global/persistente.
        loader.discover()
        s3 = loader._skills.get("l1_probe")
        if s3 is None or s3._body_loaded is not False or s3.content != "":
            got = None if s3 is None else (s3._body_loaded, len(s3.content))
            fails.append(f"re-discover: debería volver a L1 (False, content vacío), got {got}")

    if fails:
        print("FAIL — progressive-disclosure L1 no cumple el criterio de hecho:")
        for f in fails:
            print("  ✗", f)
        return 1
    print("PASS — L1: discover() difiere el body; catálogo sin body; get()/invocar materializa L2; "
          "re-discover vuelve a L1. Criterio de hecho #3 verificado por efecto.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
