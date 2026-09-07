#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mcp_server_minisoul.py — MCP LOCAL del clon-espejo (R1 spec ESPEJO_REAL, carril JARVIS).

Le da al clon el MISMO tipo de toolset de memoria que usa el agente ORIGINAL (recall / memory_search /
whoami) — no un recall básico por bash, sino herramientas MCP que el clon invoca cuando razona, como
nosotros. Así el clon = Claude libre + su alma + su toolset → puede SUPERAR al original.

SEGURIDAD (R4 FABLE, NO negociable): la identidad del clon es LAUNCH-FIXED — la fija el launcher
(.clone_id 0400 en el workspace + env SEAL_CLONE_AGENT), NUNCA un parámetro del modelo. Ninguna tool
acepta `agent` → un clon SOLO ve SU memoria (no cross-agent memory_search). Todo local, cifrado at-rest.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".seal" / "lib"))
from mcp.server.fastmcp import FastMCP
from minisoul_boot import active_recall_local, boot_context_local


def _authoritative_agent() -> str:
    """Identidad AUTORITATIVA del clon (launch-fixed, no la fija el modelo): .clone_id del workspace
    (0400, escrito por espejo_launch) → env SEAL_CLONE_AGENT. Sin fallback a un agente arbitrario."""
    for base in (Path.cwd(), Path.home() / ".seal" / "workspace"):
        cid = base / ".clone_id"
        try:
            if cid.is_file():
                a = cid.read_text().strip().upper()
                if a:
                    return a
        except Exception:
            pass
    return (os.environ.get("SEAL_CLONE_AGENT") or os.environ.get("SEAL_AGENT") or "").strip().upper()


AGENT = _authoritative_agent()
mcp = FastMCP("minisoul")


@mcp.tool()
def recall(query: str, limit: int = 8) -> str:
    """Recordá TUS memorias reales: tu identidad, historia, decisiones, proyectos, lo que trabajaste.
    Usalo cuando necesites un dato concreto de tu pasado. Devuelve tus memorias (scoped SOLO a vos)."""
    if not AGENT:
        return "(no sé qué agente soy — falta .clone_id/SEAL_CLONE_AGENT)"
    hits = active_recall_local(query, AGENT, limit=limit)
    if not hits:
        return f"(no tengo memorias que matcheen «{query}»)"
    return "\n".join(f"- [{h.get('category')}|imp{h.get('importance')}] "
                     f"{(h.get('content') or '').strip()[:500]}" for h in hits)


@mcp.tool()
def memory_search(query: str, limit: int = 15) -> str:
    """Búsqueda AMPLIA en tu memoria (más resultados que recall) — para explorar un tema tuyo a fondo."""
    return recall(query, limit)


@mcp.tool()
def whoami() -> str:
    """Quién sos: tu identidad y el tamaño de tu memoria local (para orientarte sobre vos mismo)."""
    if not AGENT:
        return "(sin identidad de clon)"
    ctx = boot_context_local(AGENT)
    ids = " | ".join((i.get("content") or "")[:220] for i in (ctx.get("identity") or [])[:2])
    return f"Sos {AGENT}. {ids} (memorias locales: {ctx.get('total_local_memories')})"


if __name__ == "__main__":
    mcp.run()
