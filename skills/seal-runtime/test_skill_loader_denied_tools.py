#!/usr/bin/env python3
"""
test_skill_loader_denied_tools.py — Test POR-EFECTO de capability-gating de tools (adopción #3).
Builder: NEXUS. Verificación de seguridad R3 (skills): FABLE (independiente, DELEGATE-52).

Criterio de hecho (SPEC_SOUL_ADOPT_CLAUDE_PATTERNS_v1.md #3):
  "una skill con denied-tools intenta usar una tool prohibida y es bloqueada."

Prueba SkillDef.is_tool_allowed() por efecto:
  • deny-wins (denied gana sobre allowed), allowlist restringe, sin-restricción permite;
  • DECIDIBLE EN L1: la decisión se toma con la frontmatter, sin cargar el body (content sigue vacío).
Cero red, cero DB — construcción directa + temp dir + stdlib.

  python3 test_skill_loader_denied_tools.py    # exit 0 = PASS, 1 = FAIL
"""
from __future__ import annotations
import sys, tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import skill_loader as sl  # noqa: E402


def _mk(**kw) -> "sl.SkillDef":
    return sl.SkillDef(name="t", description="d", content="", **kw)


def main() -> int:
    fails: list[str] = []

    # 1) denylist puro: Bash prohibido, el resto permitido.
    s = _mk(denied_tools=["Bash"])
    if s.is_tool_allowed("Bash"):
        fails.append("denylist: 'Bash' debería estar PROHIBIDO")
    if not s.is_tool_allowed("Read"):
        fails.append("denylist: 'Read' debería estar permitido")

    # 2) allowlist restringe: solo Read/Grep permitidos.
    s = _mk(allowed_tools=["Read", "Grep"])
    if not s.is_tool_allowed("Read"):
        fails.append("allowlist: 'Read' debería estar permitido")
    if s.is_tool_allowed("Bash"):
        fails.append("allowlist: 'Bash' (no listado) debería estar PROHIBIDO")

    # 3) DENY-WINS: si está en ambos, gana denied (fail-safe).
    s = _mk(allowed_tools=["Bash"], denied_tools=["Bash"])
    if s.is_tool_allowed("Bash"):
        fails.append("deny-wins: 'Bash' en allowed Y denied debería estar PROHIBIDO")

    # 4) sin restricciones → todo permitido.
    s = _mk()
    if not s.is_tool_allowed("CualquierTool"):
        fails.append("sin restricciones: debería permitir cualquier tool")

    # 3b) HARDENING FABLE #1 — CASE-INSENSITIVE: denied=['Bash'] también bloquea 'bash'/'BASH'.
    s = _mk(denied_tools=["Bash"])
    for variant in ("bash", "BASH", "BaSh"):
        if s.is_tool_allowed(variant):
            fails.append(f"case-insensitive: variante '{variant}' debería estar PROHIBIDA por denied ['Bash']")
    # y el storage preserva el case original (solo se normaliza al comparar)
    if s.denied_tools != ["Bash"]:
        fails.append("case-insensitive: el storage NO debe mutar el case original de denied_tools")

    # 3c) HARDENING FABLE #2 — GLOB/PREFIJO: denied=['mcp__seal-memory__*'] bloquea toda la familia.
    s = _mk(denied_tools=["mcp__seal-memory__*"])
    for fam in ("mcp__seal-memory__memory_store", "mcp__seal-memory__active_recall"):
        if s.is_tool_allowed(fam):
            fails.append(f"glob: '{fam}' debería estar PROHIBIDO por denied ['mcp__seal-memory__*']")
    if not s.is_tool_allowed("Read"):
        fails.append("glob: 'Read' (fuera de la familia) debería estar permitido")
    # allowlist con glob restringe correctamente
    s = _mk(allowed_tools=["mcp__seal-memory__*", "Read"])
    if not s.is_tool_allowed("mcp__seal-memory__memory_store"):
        fails.append("glob-allow: familia mcp debería estar permitida por allowed glob")
    if s.is_tool_allowed("Bash"):
        fails.append("glob-allow: 'Bash' (no listado) debería estar PROHIBIDO")

    # 5) tool vacío/None → PROHIBIDO (fail-safe).
    s = _mk(denied_tools=["Bash"])
    if s.is_tool_allowed("") or s.is_tool_allowed(None):  # type: ignore[arg-type]
        fails.append("tool vacío/None debería ser PROHIBIDO (fail-safe)")

    # 6) DECIDIBLE EN L1: cargar solo metadata (body diferido) y gatear sin materializar el body.
    with tempfile.TemporaryDirectory() as td:
        d = Path(td); (d / "skills").mkdir()
        (d / "skills" / "gated.md").write_text(
            "---\nname: gated\ndescription: skill con denied-tools\ndenied-tools: Bash, Write\n---\n"
            "BODY que NO debe cargarse solo para gatear tools.\n", encoding="utf-8")
        empty = Path(td) / "empty"; empty.mkdir()
        loader = sl.SkillLoader(extra_dirs=[d / "skills"], project_root=empty)
        loader.discover()
        g = loader._skills.get("gated")
        if g is None:
            fails.append("L1-gating: la skill 'gated' no fue descubierta")
        else:
            if g.denied_tools != ["Bash", "Write"]:
                fails.append(f"L1-gating: denied_tools mal parseado: {g.denied_tools}")
            allowed_bash = g.is_tool_allowed("Bash")
            # la decisión NO debe haber forzado la carga del body (sigue L1: content vacío / _body_loaded False)
            if allowed_bash:
                fails.append("L1-gating: 'Bash' debería estar PROHIBIDO por la frontmatter")
            if g.content != "" or g._body_loaded is not False:
                fails.append("L1-gating: gatear tools NO debe cargar el body (rompe progressive disclosure)")
            if not g.is_tool_allowed("Read"):
                fails.append("L1-gating: 'Read' (no denegado) debería estar permitido")

    if fails:
        print("FAIL — capability-gating de tools no cumple el criterio de hecho:")
        for f in fails:
            print("  ✗", f)
        return 1
    print("PASS — is_tool_allowed: deny-wins, allowlist restringe, sin-restricción permite, fail-safe en "
          "vacío, y DECIDIBLE EN L1 sin cargar el body. Criterio de hecho #3 (denied-tools) verificado por efecto.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
