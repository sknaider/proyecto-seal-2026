#!/usr/bin/env python3
"""
soul_hooks_seed.py — Capa 2 / F1: deriva el seed de `runtime_hooks` desde el settings.json VIVO.

Por qué: `ground_truth_seed()` en soul_event_interface.py es una copia a mano medida el
2026-08-05. Una copia a mano DERIVA de la realidad y luego DERIVA — cuando alguien agregue
un hook a settings.json, el seed queda desincronizado en silencio. Este importador lee el
settings.json REAL y produce las filas, así la fuente de verdad sale de lo que de verdad corre.

No silencia lo que no entiende: un evento de runtime que no mapea a un soul_event se REPORTA
(no se descarta). Un evento sin mapear es una capa que se perdería sin aviso — la lección de
siempre. No toca la DB: devuelve las filas para que el paso de persistencia (con ADA) las inserte.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from soul_event_interface import (
    CLAUDE_CODE_EVENT_MAP, LEARNING_EVENTS, HookRegistration,
)

DEFAULT_SETTINGS = Path.home() / ".claude" / "settings.json"
_SCRIPT_RE = re.compile(r"[\w./\-]+\.(?:py|sh)")


def _extract_scripts(command: str) -> list[str]:
    """Los script(s) referenciados por un comando de hook, como rutas ejecutables reales
    (no basenames): el seed tiene que poder CORRER lo que registra."""
    return _SCRIPT_RE.findall(command or "")


def load_runtime_hooks(settings_path: Path = DEFAULT_SETTINGS) -> dict[str, list[tuple[str | None, str]]]:
    """{evento_runtime: [(matcher, comando), ...]} tal como está hoy en settings.json.

    Preserva el `matcher` por entrada (p.ej. FileChanged observa un archivo concreto). Sin él,
    5 hooks de FileChanged con matchers distintos colapsan en 5 "duplicados" del mismo script y
    se pierde QUÉ archivo observa cada uno — el script correría sobre el archivo equivocado.
    """
    data = json.loads(Path(settings_path).read_text())
    out: dict[str, list[tuple[str | None, str]]] = {}
    for ev, arr in data.get("hooks", {}).items():
        pairs: list[tuple[str | None, str]] = []
        for entry in arr:
            matcher = entry.get("matcher") or None
            for h in entry.get("hooks", []):
                cmd = h.get("command", "")
                if cmd:
                    pairs.append((matcher, cmd))
        out[ev] = pairs
    return out


def derive_seed(
    settings_path: Path = DEFAULT_SETTINGS,
    event_map: dict[str, str] = dict(CLAUDE_CODE_EVENT_MAP),
) -> tuple[list[HookRegistration], list[str]]:
    """Devuelve (filas, eventos_sin_mapear). Cada comando de hook se vuelve una fila
    registrada bajo su soul_event, clasificada learning/operational según el destino.
    Los eventos de runtime que no mapean a un soul_event se DEVUELVEN aparte, nunca se tragan."""
    runtime_hooks = load_runtime_hooks(settings_path)
    rows: list[HookRegistration] = []
    unmapped: list[str] = []
    for runtime_ev, pairs in runtime_hooks.items():
        soul_ev = event_map.get(runtime_ev)
        if soul_ev is None:
            unmapped.append(runtime_ev)
            continue
        kind = "learning" if soul_ev in LEARNING_EVENTS else "operational"
        for i, (matcher, cmd) in enumerate(pairs):
            # una fila por script del comando (un comando puede encadenar varios);
            # el matcher se conserva para no perder QUÉ observa cada hook.
            for script in _extract_scripts(cmd) or [cmd]:
                rows.append(HookRegistration(
                    soul_event=soul_ev, script_path=script, kind=kind,
                    ordering=100 + i, matcher=matcher,
                ))
    return rows, unmapped


def coverage_report(rows: list[HookRegistration]) -> dict:
    """Qué soul_events de aprendizaje quedaron cubiertos por el seed derivado. Fail-closed:
    si falta un evento crítico, el seed derivado NO sirve (el runtime no aprendería esa capa)."""
    covered = {r.soul_event for r in rows}
    missing_learning = [e for e in LEARNING_EVENTS if e not in covered]
    return {
        "total_rows": len(rows),
        "events_covered": sorted(covered),
        "missing_learning_events": missing_learning,
        "learning_complete": not missing_learning,
    }


if __name__ == "__main__":
    rows, unmapped = derive_seed()
    rep = coverage_report(rows)
    print(f"seed derivado del settings.json VIVO: {rep['total_rows']} filas")
    print(f"eventos cubiertos: {rep['events_covered']}")
    print(f"aprendizaje completo (4 críticos): {rep['learning_complete']}")
    if unmapped:
        print(f"⚠️  eventos de runtime SIN mapear a soul_event (revisar, NO ignorar): {unmapped}")
    else:
        print("todos los eventos de runtime mapean a un soul_event")
