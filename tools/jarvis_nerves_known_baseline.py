#!/usr/bin/env python3
"""Escalón 2 del nervio de JARVIS — MEMORIA de "qué es normal" (en favor de SOUL).

Owner: JARVIS (orden William 23-jul: afinar el nervio, escalones 1+2 — "autónomo y
en favor de SOUL"). Este módulo es la CAPA DE APRENDIZAJE, desacoplada del detector.

Problema que resuelve: hoy el nervio re-alerta lo MISMO cada tick (los "huérfanos"
ollama/prometheus aparecieron 3 veces seguidas). Un nervio útil no grita lo que ya
sabe que es normal; alerta lo NUEVO. Así deja de hacer ruido y empieza a CONOCER el
sistema — cada latido lo deja sabiendo un poco más de sí mismo.

Cómo funciona:
- Cada hallazgo se reduce a una FIRMA estable (sin timestamps/detalle volátil).
- Una firma puede estar clasificada como `normal` (falso positivo confirmado) o
  `accepted` (hallazgo real pero aceptado a propósito, ej. servicio legacy conocido).
- El nervio consulta esta memoria: los hallazgos con firma conocida NO rompen el
  silencio (van al log como INFO); solo los NUEVOS/no clasificados alertan.
- APRENDE: cuando se confirma que un hallazgo era falso positivo (por efecto), se
  marca `normal` con su evidencia. La próxima vez el nervio ya no lo grita.

NO decide solo qué es normal: un humano/owner clasifica (o el escalón 1 corregido).
Este módulo solo RECUERDA la clasificación. Persistencia local 0600 (migrable a SOUL DB).
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/home/dadito/IA/proyecto-seal")
STORE = ROOT / "research/flywire_results/nerves_jarvis_known_baseline.json"

# Clases de clasificación válidas
NORMAL = "normal"      # falso positivo confirmado por efecto (el hallazgo NO es un problema real)
ACCEPTED = "accepted"  # hallazgo real pero aceptado a propósito (decisión conocida)
_CLASSES = {NORMAL, ACCEPTED}


def signature_of(finding: str) -> str:
    """Reduce un string de hallazgo a una firma ESTABLE (para agrupar re-apariciones).

    Quita timestamps, números de PID volátiles y espacios redundantes; conserva el
    tipo de hallazgo + el sujeto (servicio/puerto). Ej.:
      'IDENTIDAD: ollama-engine :11434 FAIL-ORPHAN systemd:ollama → sin PID dueño...'
      → 'identidad|ollama-engine|:11434|fail-orphan'
    """
    s = (finding or "").strip().lower()
    # cortar el detalle explicativo tras la flecha/dos-puntos largos
    s = re.split(r"→|::|\s-\s", s)[0]
    # eliminar PIDs y timestamps volátiles
    s = re.sub(r"\bpid[:=]?\s*\d+\b", "pid", s)
    s = re.sub(r"\d{4}-\d{2}-\d{2}t[\d:.+-]+", "", s)
    # tokens significativos: palabras, puertos (:NNNN), marcadores FAIL/ORPHAN/ROTO
    toks = re.findall(r":\d{2,5}\b|[a-z0-9_./-]{2,}", s)
    # descartar ruido común
    stop = {"sin", "de", "el", "la", "que", "no", "un", "una", "por", "con", "systemd", "docker"}
    toks = [t for t in toks if t not in stop]
    return "|".join(toks[:6]) or s[:60]


def _load() -> dict:
    if not STORE.exists():
        return {}
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(STORE, 0o600)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_known(finding: str) -> dict | None:
    """Devuelve el registro de clasificación si la firma del hallazgo ya es conocida, o None."""
    return _load().get(signature_of(finding))


def mark(finding: str, cls: str, reason: str, evidence: str = "") -> str:
    """Clasifica una firma como `normal` o `accepted`. Aprendizaje del nervio.

    Devuelve la firma registrada. Idempotente: re-marcar actualiza reason/evidence
    y last_seen, conservando first_seen y el conteo.
    """
    if cls not in _CLASSES:
        raise ValueError(f"clase inválida {cls!r}; usar {_CLASSES}")
    sig = signature_of(finding)
    data = _load()
    rec = data.get(sig, {"first_seen": _now(), "count": 0})
    rec.update({"class": cls, "reason": reason, "evidence": evidence[:400],
                "last_seen": _now(), "example": finding[:200]})
    data[sig] = rec
    _save(data)
    return sig


def record_seen(finding: str) -> None:
    """Incrementa el conteo de una firma ya conocida (para medir re-apariciones)."""
    sig = signature_of(finding)
    data = _load()
    if sig in data:
        data[sig]["count"] = int(data[sig].get("count", 0)) + 1
        data[sig]["last_seen"] = _now()
        _save(data)


def filter_findings(findings: list[str]) -> tuple[list[str], list[dict]]:
    """Separa hallazgos NUEVOS (deben alertar) de CONOCIDOS (solo log/INFO).

    Devuelve (nuevos, conocidos). `conocidos` incluye el registro de clasificación
    para poder logearlo. Efecto secundario: incrementa el conteo de los conocidos.
    """
    nuevos: list[str] = []
    conocidos: list[dict] = []
    data = _load()
    dirty = False
    for f in findings or []:
        sig = signature_of(f)
        rec = data.get(sig)
        if rec:
            rec["count"] = int(rec.get("count", 0)) + 1
            rec["last_seen"] = _now()
            conocidos.append({"finding": f, "signature": sig, **rec})
            dirty = True
        else:
            nuevos.append(f)
    if dirty:
        _save(data)
    return nuevos, conocidos


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 2 and sys.argv[1] == "--list":
        for sig, rec in _load().items():
            print(f"{rec.get('class','?'):8} count={rec.get('count',0):<4} {sig}  :: {rec.get('reason','')[:60]}")
    else:
        print("uso: jarvis_nerves_known_baseline.py --list")
