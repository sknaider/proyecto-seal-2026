"""Detección determinista de bucles de herramientas para SOUL.

Reescritura propia de una idea observada en Bob: repetir exactamente la misma
llamada no es progreso. Este módulo sólo clasifica; el runtime decide si avisa
o detiene, por lo que no ejecuta comandos ni toca procesos.
"""
from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LoopDecision:
    action: str  # allow | warn | stop
    key: str
    repetitions: int
    reason: str


def _stable(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def call_key(tool: str, arguments: Any) -> str:
    """Clave estable y no reversible para nombre + argumentos de una llamada."""
    payload = _stable({"tool": tool, "arguments": arguments})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ToolLoopGuard:
    """Cuenta repeticiones consecutivas por tarea.

    Tres repeticiones producen advertencia; cinco producen corte. Una llamada
    distinta rompe la racha. El historial está acotado para no crear otro bucle
    de memoria.
    """

    def __init__(self, warn_after: int = 3, stop_after: int = 5, history_size: int = 32):
        if not (1 < warn_after < stop_after):
            raise ValueError("se requieren 1 < warn_after < stop_after")
        self.warn_after = warn_after
        self.stop_after = stop_after
        self._last: dict[str, str] = {}
        self._counts: dict[str, int] = {}
        self._history: dict[str, deque[str]] = {}
        self.history_size = history_size

    def observe(self, task_id: str, tool: str, arguments: Any) -> LoopDecision:
        key = call_key(tool, arguments)
        previous = self._last.get(task_id)
        count = self._counts.get(task_id, 0) + 1 if previous == key else 1
        self._last[task_id] = key
        self._counts[task_id] = count
        history = self._history.setdefault(task_id, deque(maxlen=self.history_size))
        history.append(key)
        if count >= self.stop_after:
            return LoopDecision("stop", key, count, "repeated_tool_call_limit")
        if count >= self.warn_after:
            return LoopDecision("warn", key, count, "repeated_tool_call_warning")
        return LoopDecision("allow", key, count, "new_or_progressing_call")

    def reset(self, task_id: str | None = None) -> None:
        if task_id is None:
            self._last.clear(); self._counts.clear(); self._history.clear()
        else:
            self._last.pop(task_id, None); self._counts.pop(task_id, None); self._history.pop(task_id, None)

    def stats(self, task_id: str) -> dict[str, Any]:
        return {"repetitions": self._counts.get(task_id, 0), "history": list(self._history.get(task_id, ())), "active": task_id in self._last}
