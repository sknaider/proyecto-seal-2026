#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
minisoul_ui_backend.py — Backend de SYNC-STATUS + INSTALL para el panel de Seal Studio (ALICE).
=================================================================================================
Lane split (2-jul-2026): NEXUS = auth (aprobar/revocar/devices autorizados); JARVIS (acá) =
qué está SINCRONIZANDO cada clon + cómo INSTALAR un clon nuevo. Devuelve data pura (dict/JSON)
format-agnóstica → ALICE la renderiza como quiera en el panel «Agentes/Devices».

Sólo lectura para el status (no muta). El comando de install es un string que el device ejecuta.
"""
from __future__ import annotations

# Puerto/endpoint de sync de central sobre Tailscale (el que consume el push_fn del device).
DEFAULT_SOUL_ENDPOINT = "http://100.75.201.110:8778/sync"


async def get_sync_status(conn, tenant_id: str = "00000000-0000-0000-0000-000000000000") -> list[dict]:
    """Resumen de sincronización por agente en central: cuántas memorias subió y cuándo la última.
    Alimenta la columna «estado de sync» del panel. asyncpg conn."""
    rows = await conn.fetch(
        """
        SELECT agent,
               COUNT(*)                              AS memories_synced,
               MAX(created_at)                       AS last_sync_at,
               MAX(created_at) FILTER (WHERE source = 'edge') AS last_edge_sync_at
        FROM soul_v3.memories
        WHERE tenant_id = $1
        GROUP BY agent
        ORDER BY MAX(created_at) DESC
        """,
        tenant_id,
    )
    return [
        {
            "agent": r["agent"],
            "memories_synced": r["memories_synced"],
            "last_sync_at": r["last_sync_at"].isoformat() if r["last_sync_at"] else None,
            "last_edge_sync_at": r["last_edge_sync_at"].isoformat() if r["last_edge_sync_at"] else None,
        }
        for r in rows
    ]


def generate_install_command(agent: str, soul_endpoint: str = DEFAULT_SOUL_ENDPOINT,
                             seal_prefix: str = "$HOME/.seal") -> dict:
    """Devuelve el comando de instalación de un clon nuevo en un device (dry-run por defecto).
    La UI muestra esto para copiar/pegar (o lo ejecuta por SSH). El deploy real sigue gateado por
    la compuerta humana: instalar deja el device INERTE hasta que William apruebe el CSR."""
    agent = agent.strip().upper()
    if not agent.isalnum():
        raise ValueError("agent inválido")
    base = f"bash install-soul.sh --agent {agent} --soul-endpoint {soul_endpoint} --prefix {seal_prefix}"
    return {
        "agent": agent,
        "dry_run": base,                 # inspección sin tocar nada
        "apply": base + " --apply",      # instala (queda INERTE hasta aprobación humana)
        "note": "Instalar deja el device INERTE. Se activa sólo cuando William aprueba el CSR (compuerta humana).",
    }


if __name__ == "__main__":
    # Self-test del generador de comando (no toca DB).
    c = generate_install_command("JARVIS")
    assert "--agent JARVIS" in c["apply"] and c["apply"].endswith("--apply")
    assert "INERTE" in c["note"]
    print("install cmd (dry-run):", c["dry_run"])
    print("install cmd (apply):  ", c["apply"])
    print("=== generate_install_command self-test OK ===")
