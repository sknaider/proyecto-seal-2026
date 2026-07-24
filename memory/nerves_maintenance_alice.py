#!/usr/bin/env python3
"""Delivery pulse for ALICE: verify the artifact produced by ORION's nerve.

The product probe has one scheduler/owner: ``alice-orion-nerve.timer`` runs
the ORION health probe every 20 minutes. Shared NERVES consumes that artifact;
it must not invoke the expensive product probe a second time.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path


ROOT = Path("/home/dadito/IA/proyecto-seal")
STATUS_LOG = ROOT / "agents/ALICE/orion/orion_nerve_status.log"
MAX_AGE_S = int(os.environ.get("SEAL_ALICE_ORION_ARTIFACT_MAX_AGE_S", "2700"))


def _run() -> str | None:
    """Return ``None`` for a fresh OK artifact, otherwise a bounded finding."""
    try:
        if not STATUS_LOG.is_file():
            return "entrega ORION: artefacto ausente"
        if STATUS_LOG.stat().st_mode & 0o077:
            return "entrega ORION: artefacto con permisos inseguros"
        lines = [line for line in STATUS_LOG.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not lines:
            return "entrega ORION: artefacto vacío"
        payload = json.loads(lines[-1])
        ts = datetime.fromisoformat(str(payload["ts"]).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_s = (datetime.now(timezone.utc) - ts.astimezone(timezone.utc)).total_seconds()
        if age_s < -60:
            return "entrega ORION: timestamp futuro inválido"
        if age_s > MAX_AGE_S:
            return f"entrega ORION: artefacto stale ({int(age_s)}s > {MAX_AGE_S}s)"
        status = str(payload.get("status") or "")
        if status == "REMEDIATED":
            actions = payload.get("actions") or []
            detail = "; ".join(
                str(action.get("action") or "acción")
                for action in actions[:3]
                if isinstance(action, dict)
            )
            return (
                "entrega ORION: remediación verificada localmente"
                f" ({detail or 'sin detalle'}); pendiente siguiente OK completo"
            )
        if status != "OK":
            failures = (
                payload.get("fails")
                or payload.get("escalations")
                or payload.get("fails_original")
                or [f"estado {status or 'desconocido'} sin detalle"]
            )
            detail = "; ".join(str(item) for item in failures[:3])
            return f"entrega ORION: {detail[:500]}"
        if payload.get("db_user") != "svc_orion_exam":
            return f"entrega ORION: identidad DB inválida ({payload.get('db_user', 'unknown')})"
        if payload.get("login_http") != 200:
            return f"entrega ORION: /login={payload.get('login_http', 'unknown')}"
        return None
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        return f"entrega ORION: artefacto inválido ({type(exc).__name__})"


async def delivery_pulse() -> str | None:
    return await asyncio.to_thread(_run)


if __name__ == "__main__":
    print(asyncio.run(delivery_pulse()) or "clean")
