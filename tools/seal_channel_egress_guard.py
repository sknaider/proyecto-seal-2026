#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""seal_channel_egress_guard.py — Guardia de EGRESO por-canal a nivel AGENTE (cura #29, NEXUS).

Hace cumplir la regla de William/Henry: "cada canal es compartimento; responder en el canal de
origen; no llevar información de un canal a otro." #19 lo hace en la capa DB (RLS de lectura por
sesión); ESTE módulo lo hace en la capa-agente: filtra qué fragmentos de contexto (memoria, recall,
mensajes previos, citas) pueden incluirse al redactar hacia un canal destino, y verifica la salida
antes de postear.

Diseño: spec/SEAL_agent_channel_isolation_NEXUS.md. Verificación: tools/verify_agent_channel_isolation_29.py
(tabla de verdad adversarial; importa este módulo → fuente única de verdad).

Uso típico en un chat-agent:
    from seal_channel_egress_guard import filter_fragments, assert_safe_egress, EgressViolation
    usable = filter_fragments(candidate_fragments, target_channel)   # ingreso: poda por procedencia
    ...
    assert_safe_egress(fragments_referenced, target_channel)         # egreso: bloquea si hay fuga

Principios: fail-CLOSED (procedencia desconocida = cuarentena), prefijo case-insensitive (lección F2
de #19: 'User:'/'DM:' en mayúscula también es privado), determinístico (NO juicio del LLM).
"""
from __future__ import annotations
from typing import Iterable, List, Optional, Callable
import json
import os
import time

# ─────────────────────────── núcleo del predicado ───────────────────────────

_PRIVATE_PREFIXES = ("user:", "dm:")


def is_private(channel: str) -> bool:
    """¿El canal es un compartimento privado? Normaliza el prefijo a minúscula (F2 de #19)."""
    c = (channel or "").strip().lower()
    return c.startswith(_PRIVATE_PREFIXES)


def _norm(channel: str) -> str:
    """Normaliza para comparar compartimentos: prefijo case-insensitive, resto exacto (espejo RLS #19)."""
    c = (channel or "").strip()
    if ":" in c:
        pre, rest = c.split(":", 1)
        return pre.lower() + ":" + rest
    return c.lower()


def may_use(source_channel: Optional[str], target_channel: Optional[str]) -> bool:
    """¿Puede un fragmento de `source_channel` incluirse al redactar hacia `target_channel`?

    - Procedencia desconocida (vacía) → DENY (fail-closed: cuarentena hasta clasificar).
    - Fuente pública/compartida conocida (web_chat, topic:%) → ALLOW en cualquier destino.
    - Fuente privada (user:%, dm:%) → ALLOW solo si destino == MISMO compartimento; otro/vacío → DENY.
    """
    src = (source_channel or "").strip()
    if not src:
        return False
    if not is_private(src):
        return True
    if not (target_channel or "").strip():
        return False
    return _norm(src) == _norm(target_channel)


# ─────────────────────────── API de guardia ───────────────────────────

class EgressViolation(Exception):
    """Se lanza cuando un fragmento de canal privado intenta fugar a otro canal/destino."""


def _frag_channel(fragment) -> Optional[str]:
    """Extrae source_channel de un fragmento (dict con 'source_channel'/'channel', u objeto con attr)."""
    if isinstance(fragment, dict):
        return fragment.get("source_channel") or fragment.get("channel")
    return getattr(fragment, "source_channel", None) or getattr(fragment, "channel", None)


def filter_fragments(fragments: Iterable, target_channel: str) -> List:
    """INGRESO: devuelve solo los fragmentos que `may_use(...)` permite hacia `target_channel`.

    Fragmentos sin procedencia se descartan (fail-closed). Úsalo ANTES de armar el prompt."""
    out = []
    for f in fragments or []:
        if may_use(_frag_channel(f), target_channel):
            out.append(f)
    return out


def assert_safe_egress(fragments_referenced: Iterable, target_channel: str,
                       audit: Optional[Callable[[dict], None]] = None) -> None:
    """EGRESO: lanza EgressViolation si algún fragmento citado viola el aislamiento. Úsalo ANTES de postear.

    `audit` opcional: callback(dict) para registrar la violación (p.ej. a event_log)."""
    for f in fragments_referenced or []:
        src = _frag_channel(f)
        if not may_use(src, target_channel):
            evt = {"event": "egress_block", "rule": "#29", "ts": time.time(),
                   "source_channel": src, "target_channel": target_channel,
                   "reason": "unknown_provenance" if not (src or "").strip() else "cross_compartment_leak"}
            if audit:
                try:
                    audit(evt)
                except Exception:
                    pass
            raise EgressViolation(
                f"Bloqueado por aislamiento #29: fragmento de '{src or '∅'}' no puede ir a '{target_channel}'.")


def event_log_audit(evt: dict) -> None:
    """Auditor por defecto: append-only a un JSONL local (defensa en profundidad, no bloquea si falla)."""
    path = os.environ.get("SEAL_EGRESS_AUDIT_LOG",
                          os.path.expanduser("~/.private/seal_egress_audit.jsonl"))
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(evt, ensure_ascii=False) + "\n")
    except OSError:
        pass


if __name__ == "__main__":
    # smoke test mínimo (la verificación adversarial completa está en verify_agent_channel_isolation_29.py)
    assert may_use("web_chat", "user:3:tareas") is True
    assert may_use("user:3:tareas", "web_chat") is False
    assert may_use("user:3:tareas", "user:3:tareas") is True
    assert may_use("User:3:secreto", "web_chat") is False  # F2
    assert may_use("", "user:3:tareas") is False           # fail-closed
    print("seal_channel_egress_guard: smoke OK")
