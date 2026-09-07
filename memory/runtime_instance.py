"""Etiqueta del CUERPO que escribe una memoria (runtime_instance) cuando el escritor no la declara.

H8 del caso «los dos cuerpos son uno» (William 3-sep-2026, luz verde 20:22): las memorias que
entran por el MCP (Codex TUI, Claude Code, otros clientes) no decían qué cuerpo las vivió.
El servidor sí sabe quién le habla (clientInfo.name del handshake MCP y User-Agent), así que
infiere la etiqueta por identidad del cliente. Regla: lo declarado gana; si no se puede inferir,
NO se inventa (se deja sin etiqueta y se guarda el nombre crudo del cliente para auditar).
"""
from __future__ import annotations

from typing import Any

CLIENT_HINTS: tuple[tuple[str, str], ...] = (
    ("codex", "CODEX_TUI"),
    ("claude", "CLAUDE"),
    ("cursor", "CURSOR"),
    ("openclaw", "OPENCLAW"),
)


def infer_runtime_instance(agent: str, client_name: str | None, user_agent: str | None = None,
                           declared: str | None = None) -> str | None:
    """Devuelve la etiqueta de cuerpo para una memoria de `agent`.

    - `declared` (metadata.runtime_instance del escritor) gana siempre.
    - Si no, se infiere de clientInfo.name / User-Agent: codex -> <AGENTE>_CODEX_TUI,
      claude -> <AGENTE>_CLAUDE, cursor -> <AGENTE>_CURSOR, openclaw -> <AGENTE>_OPENCLAW.
    - Si nada matchea: None (no se inventa un cuerpo).
    """
    if declared and str(declared).strip():
        return str(declared).strip()
    a = (agent or "").strip().upper()
    if not a:
        return None
    hay = f"{client_name or ''} {user_agent or ''}".lower()
    for needle, suffix in CLIENT_HINTS:
        if needle in hay:
            return f"{a}_{suffix}"
    return None


def tag_runtime_instance(agent: str, meta: dict[str, Any], transport_meta: dict[str, Any] | None) -> dict[str, Any]:
    """Completa `meta` con runtime_instance / shared_canonical_identity / runtime_instance_by / mcp_client.
    No pisa lo declarado. La PROCEDENCIA se decide ANTES de mutar meta (hallazgo FABLE #147409:
    evaluarla después hacía que 'declared' dependiera de que mcp_client ya estuviera en meta)."""
    meta = meta if isinstance(meta, dict) else {}
    tm = transport_meta or {}
    client_name = tm.get("client_info_name")
    user_agent = tm.get("user_agent")
    declared = str(meta.get("runtime_instance") or "").strip() or None
    inst = infer_runtime_instance(agent, client_name, user_agent, declared)
    if inst:
        meta["runtime_instance"] = inst
        meta.setdefault("shared_canonical_identity", (agent or "").strip().upper())
        meta["runtime_instance_by"] = "declared" if declared else "mcp_client_identity"
    if client_name and "mcp_client" not in meta:
        meta["mcp_client"] = str(client_name)[:120]
    return meta
