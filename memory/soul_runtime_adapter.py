#!/usr/bin/env python3
"""
soul_runtime_adapter.py — Capa 2 / F2 (groundwork): el ADAPTADOR de runtime.

Es la capa FINA y ÚNICA no-portable: traduce el evento NATIVO de un runtime a un
`SoulEventEnvelope` de la interfaz SOUL. Todo lo de abajo (dispatcher, scripts, DB) es
portable y no cambia al cambiar de runtime; sólo se escribe un adaptador nuevo por runtime.

Depende de `soul_event_interface` (ya construido y testeado), NO de la tabla `runtime_hooks`,
así que se puede construir en paralelo a la verificación RLS sin rework.

Autorizado: William 3-ago + 5-ago ("luz verde con el f1", "puedes hacerlo").
"""
from __future__ import annotations

import os

from soul_event_interface import (
    SoulEventEnvelope, to_soul_event, CLAUDE_CODE_EVENT_MAP,
)


class RuntimeAdapter:
    """Base: un runtime implementa `event_map` (evento nativo -> soul_event) y hereda
    `to_envelope`. La lógica de traducción es una sola; sólo cambia el mapa por runtime."""

    runtime_name: str = "base"
    event_map: dict[str, str] = {}

    def to_envelope(
        self,
        native_event: str,
        agent: str | None,
        session_id: str,
        ts: str,
        payload: dict | None = None,
    ) -> SoulEventEnvelope:
        """Traduce un evento nativo a un sobre SOUL. Falla RUIDOSO si el evento no mapea
        (un evento sin mapear es una capa que se perdería en silencio)."""
        soul_event = to_soul_event(native_event, self.event_map)
        return SoulEventEnvelope(
            soul_event=soul_event,
            agent=(agent or os.environ.get("SEAL_AGENT") or "UNKNOWN"),
            session_id=session_id,
            ts=ts,
            runtime=self.runtime_name,
            payload=dict(payload or {}),
        )

    def emits(self) -> set[str]:
        """Los soul_events que ESTE runtime puede emitir. La base del test de paridad:
        un runtime que no emite un soul_event crítico pierde esa capa de aprendizaje."""
        return set(self.event_map.values())


class ClaudeCodeAdapter(RuntimeAdapter):
    """Runtime actual: hooks de Claude Code. El mapa es el ground truth medido de F0."""

    runtime_name = "claude_code"
    event_map = dict(CLAUDE_CODE_EVENT_MAP)


class LocalRuntimeAdapter(RuntimeAdapter):
    """Runtime local (llama.cpp/servidor propio). NO tiene hooks nativos: el orquestador
    (F2) llama estos puntos explícitamente en su loop de inferencia. El mapa demuestra la
    portabilidad — los MISMOS soul_events, disparados por nombres nativos distintos.

    Los 4 críticos de aprendizaje DEBEN estar; los operativos son opcionales según lo que
    el runtime pueda observar. El test de paridad (F3) verifica esto por efecto."""

    runtime_name = "local_llama"
    event_map = {
        "session_start":   "on_boot",
        "user_message":    "on_prompt",
        "turn_complete":   "on_turn_end",
        "context_trim":    "on_compact",
        "tool_invoke":     "on_tool_call",
        "tool_return":     "on_tool_result",
    }


ADAPTERS: dict[str, RuntimeAdapter] = {
    "claude_code": ClaudeCodeAdapter(),
    "local_llama": LocalRuntimeAdapter(),
}


def get_adapter(runtime: str) -> RuntimeAdapter:
    try:
        return ADAPTERS[runtime]
    except KeyError:
        raise ValueError(f"runtime sin adaptador: {runtime!r} (hay: {sorted(ADAPTERS)})")


if __name__ == "__main__":
    from soul_event_interface import LEARNING_EVENTS
    for name, ad in ADAPTERS.items():
        emits = ad.emits()
        missing = [e for e in LEARNING_EVENTS if e not in emits]
        flag = "OK" if not missing else f"FALTAN CRÍTICOS: {missing}"
        print(f"{name:12s} emite {len(emits)} soul_events · aprendizaje {flag}")
