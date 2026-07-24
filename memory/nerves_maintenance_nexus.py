#!/usr/bin/env python3
"""nerves_maintenance_nexus.py — acción de mantenimiento de SEGURIDAD para el nervio de NEXUS.

William 14-jun: "quiero utilidad al nervio, a favor de SOUL, apliquen cada uno a su rol".
Reparto (JARVIS arquitecto): el dispatch del nervio invoca una acción de mantenimiento por
agente. La de NEXUS = SEGURIDAD: el nervio, en vez de saludar, hace un PULSO DE VIGILANCIA
ligero y deja un ARTEFACTO (hallazgo o 'limpio'). DRIVE→PROTEGE, no DRIVE→ANUNCIA.

Principio: postear a William SOLO si el pulso CAZA algo (artefacto con valor). Si todo está
limpio, registra en log y NO interrumpe (cero ruido — la lección del día).

Es read-only/diagnóstico (no mata ni cambia nada — eso requiere autorización por la doctrina).
Lo pesado se evita: chequeos baratos, rápidos, sin recursividad.
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path

ROOT = Path("/home/dadito/IA/proyecto-seal")
ARTIFACT = ROOT / "research/flywire_results/nerves_nexus_maintenance.jsonl"
WATCH = ROOT / "tools/nexus_nerves_watch.py"


def _watch_check() -> tuple[str, list[str], list[str], str]:
    """Load NEXUS's bounded read-only action without starting another daemon."""
    spec = importlib.util.spec_from_file_location("seal_nexus_nerves_watch", WATCH)
    if spec is None or spec.loader is None:
        raise RuntimeError("nexus_nerves_watch loader unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.check()


def _write_artifact(state: str, findings: list[str], broken: list[str], detail: str) -> None:
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "agent": "NEXUS",
        "action": "security_pulse",
        "action_source": str(WATCH.relative_to(ROOT)),
        "state": state,
        "status": "clean" if state == "GREEN" else "issue",
        "findings": findings[:10],
        "broken": broken[:10],
        "detail": detail[:800],
    }
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(ARTIFACT, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(ARTIFACT, 0o600)


async def security_pulse() -> str | None:
    """Run the allowlisted read-only security action and persist its verdict."""
    state, findings, broken, detail = await asyncio.to_thread(_watch_check)
    _write_artifact(state, findings, broken, detail)
    if state == "GREEN":
        return None
    summary = "; ".join((broken if state == "BROKEN" else findings)[:3])
    return f"🛡️ [NERVES/NEXUS seguridad] {state}: {summary[:500]}"


# registro en el dispatch compartido (lo importa seal_nerves si SEAL_NERVES_USEFUL)
MAINTENANCE_ACTION = security_pulse


if __name__ == "__main__":
    # dry-run: probar el pulso por efecto
    r = asyncio.run(security_pulse())
    print("ARTEFACTO:" , r if r else "(limpio — sin hallazgos, no postearía nada)")
