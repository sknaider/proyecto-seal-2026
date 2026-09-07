"""Observabilidad de compactación para SOUL, sin dependencias de red ni LLM."""
from __future__ import annotations

import time
from dataclasses import dataclass, asdict
from typing import Any, Callable


@dataclass(frozen=True)
class CompactionObservation:
    agent: str
    messages_before: int
    messages_after: int
    chars_before: int
    chars_after: int
    threshold: int
    summary_chars: int
    duration_ms: float
    degraded: bool
    exit_reason: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def observe_compaction(agent: str, messages: list[str], compact: Callable[[list[str]], list[str]], *, threshold: int = 0, sink: Callable[[dict[str, Any]], None] | None = None) -> tuple[list[str], CompactionObservation]:
    """Ejecuta compactación y mide efecto; el sink nunca puede romperla."""
    before_chars = sum(len(x) for x in messages)
    started = time.perf_counter()
    reason = "completed"
    degraded = False
    try:
        result = compact(messages)
        if not isinstance(result, list):
            raise TypeError("compact debe devolver list[str]")
    except Exception:
        result = list(messages)
        reason = "compaction_failed_passthrough"
        degraded = True
    elapsed = (time.perf_counter() - started) * 1000
    observation = CompactionObservation(agent, len(messages), len(result), before_chars, sum(len(x) for x in result), threshold, sum(len(x) for x in result), elapsed, degraded, reason)
    if sink is not None:
        try:
            sink(observation.as_dict())
        except Exception:
            pass
    return result, observation
