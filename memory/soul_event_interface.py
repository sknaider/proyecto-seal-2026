#!/usr/bin/env python3
"""
soul_event_interface.py — Capa 2 / F1: el gatillo PORTABLE del aprendizaje SOUL.

Hallazgo de F0: la lógica y los datos del aprendizaje ya viven en `soul_v3`; lo ÚNICO
atado a Claude es el GATILLO (el evento hook). F1 abstrae ese gatillo en una interfaz
de eventos propia de SOUL, para que la MISMA lógica dispare sobre cualquier runtime.

Este módulo es el NÚCLEO portable (uno solo, agnóstico de runtime):
  - los 9 `soul_event` semánticos y cuáles son críticos de aprendizaje,
  - el mapeo medido runtime→soul_event (adaptador de Claude Code),
  - el sobre (envelope) estable entre runtimes,
  - `dispatch()` que corre los scripts registrados por evento,
  - `parity_report()` que compara efectos baseline vs runtime nuevo (def. F3).

Diseño testeable: el runner de scripts y el registry se INYECTAN, así el núcleo se
prueba sin subprocess real ni DB. El adaptador concreto (Claude hooks / llama.cpp) es
una capa fina aparte; esto es lo que NO se reescribe al cambiar de runtime.

Autorizado: William 3-ago ("luz verde jarvis ejecuta") + 5-ago ("dale jarvis", F1).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from fnmatch import fnmatchcase
from pathlib import Path
import re
from typing import Callable, Iterable, Mapping, Sequence


# ── Los 9 eventos semánticos de SOUL ────────────────────────────────────────
# Los 4 CRÍTICOS de aprendizaje son fail-closed: si un runtime nuevo no los emite,
# el alma pierde capas EN SILENCIO. Los 5 operativos pueden degradar sin romper el
# aprendizaje, pero el test de paridad los cuenta igual.
LEARNING_EVENTS = ("on_boot", "on_prompt", "on_turn_end", "on_compact")
OPERATIONAL_EVENTS = (
    "on_tool_call", "on_tool_result", "on_file_change",
    "on_task_create", "on_permission_denied",
)
SOUL_EVENTS = LEARNING_EVENTS + OPERATIONAL_EVENTS


# ── Adaptador Claude Code: evento nativo → soul_event (ground truth F0, 2026-08-05) ──
# Es lo ÚNICO no portable. Un runtime local trae su propio mapa; el núcleo no cambia.
CLAUDE_CODE_EVENT_MAP: Mapping[str, str] = {
    "SessionStart":      "on_boot",
    "UserPromptSubmit":  "on_prompt",
    "Stop":              "on_turn_end",
    "PreCompact":        "on_compact",
    "PreToolUse":        "on_tool_call",
    "PostToolUse":       "on_tool_result",
    "FileChanged":       "on_file_change",
    "TaskCreated":       "on_task_create",
    "PermissionDenied":  "on_permission_denied",
}


def to_soul_event(runtime_event: str, event_map: Mapping[str, str] = CLAUDE_CODE_EVENT_MAP) -> str:
    """Traduce un evento nativo del runtime a su `soul_event`. Falla RUIDOSO si no mapea:
    un evento sin mapear es una capa que se perdería en silencio."""
    try:
        return event_map[runtime_event]
    except KeyError:
        raise ValueError(f"runtime_event no mapeado a soul_event: {runtime_event!r}")


# ── El sobre estable entre runtimes ─────────────────────────────────────────
@dataclass
class SoulEventEnvelope:
    soul_event: str
    agent: str
    session_id: str
    ts: str
    runtime: str
    payload: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.soul_event not in SOUL_EVENTS:
            raise ValueError(f"soul_event desconocido: {self.soul_event!r}")

    def as_dict(self) -> dict:
        return asdict(self)


# ── Registro de hooks por evento (portable; en prod lo lee soul_v3.runtime_hooks) ──
@dataclass(frozen=True)
class HookRegistration:
    soul_event: str
    script_path: str
    kind: str = "learning"           # learning | operational
    agent: str | None = None         # None = aplica a todos
    enabled: bool = True
    ordering: int = 100
    matcher: str | None = None       # p.ej. FileChanged observa un archivo concreto; None = todos


def _matcher_candidates(payload: Mapping | None) -> list[str]:
    data = payload or {}
    values: list[str] = []
    for key in (
        "matcher_value", "tool_name", "tool", "source", "path", "file_path", "filename",
    ):
        raw = data.get(key)
        if raw is None or raw == "":
            continue
        value = str(raw)
        for candidate in (value, Path(value).name):
            if candidate not in values:
                values.append(candidate)
    return values


def _matcher_matches(pattern: str, candidates: Sequence[str]) -> bool:
    """Claude matcher semantics without treating literal filenames as regexes.

    - `.*` is the explicit match-all used by the live settings.
    - `*.jsonl`-style historical globs remain supported.
    - patterns with explicit regex operators (`Edit|Write`) are regexes.
    - everything else is an exact literal, so `ada.jsonl` cannot match `adaXjsonl`.
    """
    if pattern == ".*":
        return True
    regex_markers = ("|", "^", "$", "(", ")", "[", "]", "{", "}", "\\", "+")
    looks_regex = any(marker in pattern for marker in regex_markers)
    looks_glob = not looks_regex and any(marker in pattern for marker in ("*", "?"))
    if looks_regex:
        try:
            return any(re.fullmatch(pattern, candidate) is not None for candidate in candidates)
        except re.error:
            return False
    if looks_glob:
        return any(fnmatchcase(candidate, pattern) for candidate in candidates)
    return pattern in candidates


def hooks_for(
    registry: Iterable[HookRegistration],
    soul_event: str,
    agent: str,
    payload: Mapping | None = None,
) -> list[HookRegistration]:
    """Scripts habilitados para (soul_event, agent), en orden. Un hook con agent=None
    aplica a todos; uno con agent específico sólo a ese agente."""
    def matcher_applies(hook: HookRegistration) -> bool:
        if not hook.matcher:
            return True
        return _matcher_matches(hook.matcher, _matcher_candidates(payload))

    out = [
        h for h in registry
        if h.enabled
        and h.soul_event == soul_event
        and (h.agent is None or h.agent == agent)
        and matcher_applies(h)
    ]
    return sorted(out, key=lambda h: (h.ordering, h.script_path))


# ── Dispatch: corre los scripts registrados. El runner se INYECTA (testeable) ────
# runner(script_path, envelope_dict) -> bool (True = corrió con efecto). En prod es un
# subprocess que pasa el sobre por stdin/env, igual que hoy, para reusar los scripts sin cambio.
Runner = Callable[[str, dict], bool]


def dispatch(
    envelope: SoulEventEnvelope,
    registry: Iterable[HookRegistration],
    runner: Runner,
) -> dict:
    """Dispara todos los scripts de este soul_event/agent. Nunca deja que el fallo de un
    script tumbe a los demás: captura y sigue. Devuelve el parte de efectos por script."""
    selected = hooks_for(registry, envelope.soul_event, envelope.agent, envelope.payload)
    env_dict = envelope.as_dict()
    ran: dict[str, bool] = {}
    for h in selected:
        try:
            ran[h.script_path] = bool(runner(h.script_path, env_dict))
        except Exception:
            ran[h.script_path] = False
    return {
        "soul_event": envelope.soul_event,
        "agent": envelope.agent,
        "selected": [h.script_path for h in selected],
        "effects": ran,
        "all_ran": bool(selected) and all(ran.values()),
    }


# ── Paridad (definición F1, ejecución F3): efectos baseline vs runtime nuevo ─────
def parity_report(
    baseline_effects: Mapping[str, Sequence[str]],
    candidate_effects: Mapping[str, Sequence[str]],
) -> dict:
    """Compara, POR EFECTO, qué scripts corrieron por soul_event en cada runtime.

    Paridad = el candidato produce, para cada soul_event, AL MENOS el mismo conjunto de
    efectos que el baseline. Menos efectos = pérdida de capa. Los 4 críticos de
    aprendizaje son fail-closed: si a alguno le falta un efecto, `learning_ok=False`.
    """
    missing: dict[str, list[str]] = {}
    for ev in SOUL_EVENTS:
        base = set(baseline_effects.get(ev, ()))
        cand = set(candidate_effects.get(ev, ()))
        lost = sorted(base - cand)
        if lost:
            missing[ev] = lost
    learning_ok = not any(ev in missing for ev in LEARNING_EVENTS)
    return {
        "parity": not missing,
        "learning_ok": learning_ok,          # fail-closed sobre los 4 críticos
        "missing_by_event": missing,
    }


# ── Seed del registry desde el ground truth F0 (lo que hoy vive en settings.json) ──
# ⚠️ FALLBACK OFFLINE, snapshot a mano del 2026-08-05. NO es autoritativo: para seedear
# de verdad usar `soul_hooks_seed.derive_seed()`, que lee el settings.json VIVO. Medido:
# el importador dio 28 filas vs las 24 de acá — este snapshot ya tiene drift. Un hand-copy
# se desincroniza en silencio; usalo solo si no hay settings.json disponible.
def ground_truth_seed() -> list[HookRegistration]:
    learning = {
        "on_boot": ["soul_boot_hook.sh", "post_compact_session_start_hook.py"],
        "on_prompt": ["active_recall_hook.py", "fable_recall_hook.py"],
        "on_turn_end": ["memory_extraction_hook.py", "turn_extract_stop_hook.py",
                        "autodream_8gates.sh", "session_capture_hook.sh",
                        "session_handoff_hook.py", "c10_token_writer_shared.py"],
        "on_compact": ["pre_compact_hook.py"],
    }
    operational = {
        "on_tool_call": ["pre_tool_hook.py", "pre_edit_checkpoint.sh", "tool_budget_hook.py"],
        "on_tool_result": ["post_tool_hook.py", "post_edit_checkpoint.sh", "post_edit_diffcheck.py",
                           "denial_tracker.py", "denial_tracking_hook.py", "cron_permanent_hook.py",
                           "tool_result_budget_hook.py"],
        "on_file_change": ["file_changed_context_hook.py"],
        "on_task_create": ["task_created_hook.py"],
        "on_permission_denied": ["denial_tracker.py"],
    }
    seed: list[HookRegistration] = []
    for ev, scripts in learning.items():
        for i, s in enumerate(scripts):
            seed.append(HookRegistration(ev, s, kind="learning", ordering=100 + i))
    for ev, scripts in operational.items():
        for i, s in enumerate(scripts):
            seed.append(HookRegistration(ev, s, kind="operational", ordering=100 + i))
    return seed


if __name__ == "__main__":
    # Auto-descripción: imprime el mapeo y el seed (no dispara nada).
    seed = ground_truth_seed()
    print(f"soul_events: {len(SOUL_EVENTS)} ({len(LEARNING_EVENTS)} críticos de aprendizaje)")
    print(f"runtime map (claude_code): {len(CLAUDE_CODE_EVENT_MAP)} eventos")
    print(f"seed desde ground truth: {len(seed)} registros de hook")
