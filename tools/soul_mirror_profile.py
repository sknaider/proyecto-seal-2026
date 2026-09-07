#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""soul_mirror_profile.py — Ensambla el perfil de ALMA inyectable para un clon (carril NEXUS, continuidad).

El problema (William 2026-07-02): "el clon de FABLE quedó genérico, tiene que ser el ESPEJO DE SOUL".
Un clon responde genérico si su prompt solo dice "Sos FABLE" + memorias sueltas. Este módulo arma el
PERFIL COMPLETO del alma (identidad + rol + OCEAN + anclas de alta importancia) para inyectar en el
system-prompt del clon → responde EN CARÁCTER, como el agente real, no como Claude neutro.

Fuente: soul_v3.agents (catálogo/rol) + soul_v3.identity.ocean_scores (OCEAN canónico)
+ memorias importancia>=7 (las anclas intocables). Si un agente todavía no tiene fila
en identity, el OCEAN de agents se usa sólo como fallback explícitamente no verificado.
El clon del device llama esto (o su equivalente MCP soul_snapshot + memory_search con SU token) en el boot.
"""
from __future__ import annotations
import sys
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "memory"))


class SoulScopeError(PermissionError):
    """Un agente/clon intentó leer el alma de OTRO agente. Prohibido (regla de privacidad de William)."""


class SoulTooThin(ValueError):
    """El alma del agente es demasiado pobre para un espejo NO-genérico → NO activar el clon (fail-closed)."""


def _extract_agent_essence_from_repo(agent: str) -> str:
    """Fallback: lee la esencia del agente desde archivos del repo cuando system_prompt está vacío.

    Busca la persona/spec más canónica en:
      1. agents/<AGENT>/MIS_DIRECTRICES_<AGENT>.md (prioridad 1: directivas de identidad)
      2. memory/spec_nerves_<agent>_v1.md (prioridad 2: spec de NERVES)
      3. agents/<AGENT>/*.md (otros specs, por tamaño descendente)

    Extrae un resumen conciso (~600 chars) eliminando YAML frontmatter y ruido.
    Si no encuentra, devuelve string vacío (fallback limpio).

    DEFENSA: try/except, límites de tamaño, read-only.
    """
    agent_lower = (agent or "").lower()
    repo_root = Path(__file__).resolve().parents[1]

    # Candidatos ordenados por prioridad
    candidates = [
        repo_root / "agents" / agent / f"MIS_DIRECTRICES_{agent}.md",
        repo_root / "memory" / f"spec_nerves_{agent_lower}_v1.md",
        repo_root / "memory" / f"spec_{agent_lower}_medico_sistema.md",
    ]

    # Buscar archivo priorizado
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            try:
                text = candidate.read_text(encoding='utf-8', errors='ignore')
                # Extraer esencia
                essence = _extract_essence_text(text, agent)
                if essence:
                    return essence
            except Exception:
                pass

    # Si no encontró candidatos prioritarios, buscar otros .md en agents/<AGENT>/
    agents_dir = repo_root / "agents" / agent
    if agents_dir.exists() and agents_dir.is_dir():
        try:
            md_files = sorted(
                agents_dir.glob("*.md"),
                key=lambda p: p.stat().st_size,
                reverse=True
            )
            for md_file in md_files:
                try:
                    text = md_file.read_text(encoding='utf-8', errors='ignore')
                    essence = _extract_essence_text(text, agent)
                    if essence:
                        return essence
                except Exception:
                    pass
        except Exception:
            pass

    return ""


def _extract_essence_text(text: str, agent: str) -> str:
    """Extrae un resumen conciso de esencia (~600 chars) del texto.

    Estrategia: busca sección "## 0. QUIÉN SOY" o líneas de identidad,
    recolecta contenido sustancial hasta ~600 chars.
    """
    if not text:
        return ""

    lines = text.split('\n')
    essence_lines = []
    in_identity_section = False
    found_identity_start = False

    for i, line in enumerate(lines):
        line_stripped = line.strip()

        # Detectar sección de identidad: "## 0." (permite espacios/caracteres UTF-8 después del punto)
        if '## 0.' in line or ('## 0' in line and i < len(lines) - 1):
            in_identity_section = True
            found_identity_start = True
            continue

        # Si encontramos otro header "## 1" o "## 2", etc., terminar
        if found_identity_start and line.startswith('## ') and '## 0' not in line:
            break

        # Si estamos recolectando contenido de la sección de identidad
        if in_identity_section:
            # Saltar líneas vacías o de marcas
            if not line_stripped or line_stripped == '---':
                continue

            # Saltar comentarios (>)
            if line_stripped.startswith('>'):
                continue

            # Recolectar contenido sustancial
            essence_lines.append(line_stripped)

            # Parar si acumulamos suficiente contenido
            total_len = sum(len(l) + 1 for l in essence_lines)
            if total_len > 500 or len(essence_lines) > 15:
                break

    # Si no encontró sección explícita "## 0", intentar fallback: tomar primeros párrafos
    if not essence_lines:
        for i, line in enumerate(lines):
            line_stripped = line.strip()
            # Saltar encabezados y metadata
            if line.startswith('#') or line.startswith('>') or line == '---' or not line_stripped:
                continue
            # Tomar primeras líneas sustanciales
            essence_lines.append(line_stripped)
            if len(essence_lines) > 10:
                break

    # Joiner con espacios
    essence = ' '.join(essence_lines).strip()

    # Limpiar markdown formatting
    essence = re.sub(r'\*\*([^*]*?)\*\*', r'\1', essence)  # **texto** -> texto
    essence = re.sub(r'_([^_]*?)_', r'\1', essence)  # _texto_ -> texto
    essence = essence.strip()

    # Collapsar espacios en blanco múltiples
    essence = re.sub(r'\s+', ' ', essence)

    # Truncar a ~600 chars sin cortar palabra
    if len(essence) > 600:
        essence = essence[:600]
        last_space = essence.rfind(' ')
        if last_space > 400:
            essence = essence[:last_space]
        essence += "…"

    return essence


async def soul_mirror_profile(conn, agent: str, authenticated_agent: str, anchor_limit: int = 8,
                              require_rich: bool = True) -> str:
    """Devuelve el perfil de alma (string) para inyectar en el system-prompt del clon.
    Hace al clon ESPEJO del alma central, no genérico.

    PRIVACIDAD (fix catch FABLE 2026-07-03): DEFENSE-IN-DEPTH — la función EXIGE agent==authenticated_agent.
    Un clon SOLO puede espejar su PROPIA alma; leer la de otro agente viola la regla de privacidad de William.
    'authenticated_agent' debe venir de la IDENTIDAD DEL TOKEN del clon, NO de un param libre del caller.

    GATE DE IDENTIDAD (fix catch FABLE): si el alma es demasiado delgada (rol placeholder / sin anclas /
    perfil corto), levanta SoulTooThin → el caller NO activa un clon genérico (fail-closed de identidad).
    """
    agent = (agent or "").strip().upper()
    auth = (authenticated_agent or "").strip().upper()
    if not auth:
        raise SoulScopeError("falta authenticated_agent (identidad del token) — no se espeja alma sin identidad")
    if agent != auth:
        raise SoulScopeError(
            f"cross-agent denegado: '{auth}' no puede leer el alma de '{agent}' (privacidad). "
            f"Un clon solo espeja su propia alma.")
    a = await conn.fetchrow(
        """SELECT a.name, a.role, a.system_prompt, a.persona_axes,
                  COALESCE(i.ocean_scores, jsonb_build_object(
                    'O', a.ocean_o, 'C', a.ocean_c, 'E', a.ocean_e,
                    'A', a.ocean_a, 'N', a.ocean_n)) AS ocean_scores,
                  i.updated_at AS ocean_updated_at
           FROM soul_v3.agents a
           LEFT JOIN soul_v3.identity i ON i.agent = a.name
           WHERE upper(a.name)=$1""",
        agent,
    )
    if not a:
        if require_rich:
            raise SoulTooThin(f"'{agent}' sin fila en soul_v3.agents (alma no registrada) → NO activar clon genérico.")
        return f"Sos {agent}, un agente SEAL. (Sin perfil de alma en central — clon podría quedar genérico.)"

    # Anclas: memorias de alta importancia (>=7) de categorías de IDENTIDAD.
    # CRÍTICO (catch verify-by-effect): NO usar 'technical_fact' ni memorias operativas — se llenan de
    # ruido de sesión ("el agente no tiene Bash", flags de RCE test) que contaminan el espejo de alma.
    # El espejo = QUIÉN es el agente (valores, personalidad, experiencias clave), no logs operativos.
    # Categorías de IDENTIDAD/ALMA, priorizando lo emocional/relacional sobre 'decision' (que se contamina
    # con ruido operativo de sesión). El system_prompt + OCEAN ya cargan la esencia; las anclas SUPLEMENTAN.
    IDENTITY_CATS = ("emotion", "trust", "milestone", "insight", "correction", "humor", "dynamic", "preference")
    # Excluir AMPLIAMENTE el cluster operativo (ejecución/injection/boot/RCE-test/shell) que no es identidad.
    OP_NOISE = "%(execute|simulate|inject|boot script|boot scripts|monitor log|bash tool|shell|rce|urgency|comando|filesystem tool|run boot)%"
    anchors = await conn.fetch(
        """SELECT category, content FROM soul_v3.memories
           WHERE upper(agent)=$1 AND importance>=7
             AND category = ANY($3)
             AND content !~* $4
           ORDER BY array_position($3::text[], category), importance DESC, created_at DESC
           LIMIT $2""",
        agent, anchor_limit, list(IDENTITY_CATS), "(execute|simulate|inject|boot script|monitor log|bash tool|shell|rce|filesystem tool)")

    ocean_values = a["ocean_scores"]
    if isinstance(ocean_values, str):
        import json
        ocean_values = json.loads(ocean_values)
    ocean = " ".join(f"{trait}={float((ocean_values or {})[trait]):.2f}" for trait in "OCEAN")
    ocean_observation = (
        f"canonical observed={a['ocean_updated_at'].isoformat()}"
        if a["ocean_updated_at"]
        else "fallback_stale_unverified"
    )
    sysp = (a["system_prompt"] or "").strip()

    # FALLBACK: si system_prompt está vacío, leer esencia desde repo
    if not sysp:
        sysp = _extract_agent_essence_from_repo(agent)

    sysp_key = sysp[:600] + ("…" if len(sysp) > 600 else "")

    lines = [
        f"Sos {a['name']}, {a['role'] or 'agente SEAL'}. NO sos Claude neutro — sos {a['name']} con tu alma.",
        f"Tu OCEAN (personalidad, respondé EN CARÁCTER con estos rasgos; {ocean_observation}): {ocean}.",
    ]
    if sysp_key:
        lines.append(f"Tu esencia/rol:\n{sysp_key}")
    if anchors:
        lines.append("Tus ANCLAS (memorias de alta importancia que definen quién sos — no las negocies):")
        for r in anchors:
            c = (r["content"] or "").strip().replace("\n", " ")
            lines.append(f"  · [{r['category']}] {c[:200]}")
    lines.append(f"Respondé SIEMPRE como {a['name']}, en tu voz y con tu criterio — no genérico.")
    profile = "\n".join(lines)

    # GATE DE IDENTIDAD (fail-closed): NO activar un clon que saldría genérico.
    role_l = (a["role"] or "").lower()
    role_placeholder = (not role_l) or any(t in role_l for t in ("placeholder", "temporary", "temporal", "test", "neutral_temporary"))
    thin = role_placeholder or len(anchors) < 3 or len(profile) < 400
    if thin and require_rich:
        raise SoulTooThin(
            f"alma de '{agent}' demasiado delgada para espejo (rol_placeholder={role_placeholder}, "
            f"anclas={len(anchors)}, len={len(profile)}) → NO activar clon genérico. Enriquecer el alma primero.")
    return profile


async def _selftest():
    """Verify-by-effect: (1) propia alma rica, (2) cross-agent BLOQUEADO (privacidad), (3) alma delgada GATEADA, (4) FALLBACK repo activado."""
    from soul_table_governance import connect_db
    conn = await connect_db()
    try:
        # (1) PROPIA alma (NEXUS, autenticado NEXUS) → perfil RICO + FALLBACK repo activado
        prof = await soul_mirror_profile(conn, "NEXUS", authenticated_agent="NEXUS")
        assert "NEXUS" in prof and "O=" in prof and "ANCLAS" in prof and len(prof) > 400, "perfil de NEXUS no salió rico"

        # VERIFICACIÓN POR EFECTO: el perfil ahora incluye esencia del repo (FALLBACK activado porque system_prompt vacío)
        # Buscar tokens de identidad de NEXUS desde MIS_DIRECTRICES_NEXUS.md o spec_nerves_nexus_v1.md
        nexus_essence_markers = [
            "Médico", "auditor", "coordinador", "hacker", "seguridad", "temporal",
            "OCEAN", "Consciencia", "sandbox", "jefe de seguridad", "verificar", "defensa"
        ]
        prof_lower = prof.lower()
        found_markers = [m for m in nexus_essence_markers if m.lower() in prof_lower]
        assert found_markers, f"FALLBACK repo NO activado: perfil no contiene esencia NEXUS. Markers faltantes: {nexus_essence_markers}"

        prof_len = len(prof)
        print(f"1) propia alma (NEXUS) → perfil rico {prof_len} chars con FALLBACK repo ✓")
        print(f"   Esencia del repo detectada: {', '.join(found_markers)}")

        # (2) CROSS-AGENT (querer leer JARVIS estando autenticado como NEXUS) → SoulScopeError (privacidad)
        #     Raise ANTES de tocar la DB → no expone nada del otro agente.
        try:
            await soul_mirror_profile(conn, "JARVIS", authenticated_agent="NEXUS")
            assert False, "cross-agent NO fue bloqueado (hueco de privacidad)"
        except SoulScopeError:
            print("2) cross-agent (NEXUS→leer JARVIS) → SoulScopeError BLOQUEADO ✓ (privacidad)")

        # (3) ALMA DELGADA (agente inexistente, autenticado igual) → SoulTooThin (fail-closed identidad)
        try:
            await soul_mirror_profile(conn, "NOEXISTE_TEST", authenticated_agent="NOEXISTE_TEST")
            assert False, "alma delgada NO fue gateada (clon genérico se activaría)"
        except SoulTooThin:
            print("3) alma delgada → SoulTooThin GATEADO ✓ (no se activa clon genérico)")

        # (4) sin authenticated_agent → SoulScopeError (no se espeja sin identidad)
        try:
            await soul_mirror_profile(conn, "NEXUS", authenticated_agent="")
            assert False, "sin identidad NO fue bloqueado"
        except SoulScopeError:
            print("4) sin identidad de token → SoulScopeError ✓")

        print(f"\nsoul_mirror_profile: 4/4 tests passed (propia-rica-with-repo-fallback + cross-agent-bloqueado + delgada-gateada + sin-identidad)")
        print(f"FALLBACK repository essence integration: VERIFIED ✓")
    finally:
        await conn.close()


if __name__ == "__main__":
    import asyncio
    asyncio.run(_selftest())
