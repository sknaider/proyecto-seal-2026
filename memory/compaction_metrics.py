#!/usr/bin/env python3
"""Métricas de la compactación: qué entró, qué salió y si salió degradada.

POR QUÉ EXISTE (idea de IBM Bob reescrita desde cero, 4-sep-2026):
Bob **instrumenta** su compactación: publica cuántos mensajes había antes, cuántos
después, el largo del resumen, el umbral, el costo y la duración. Nosotros no
medíamos **nada**, y el precio se cobró el mismo día que lo descubrí:
`post_compact_session_start_hook.py` llevaba semanas devolviendo una sola línea
—«SOUL DB no disponible»— en cada compactación de cada agente, y **nadie lo supo**.
Ni correcciones de William, ni reglas, ni estado del equipo. La herramienta corría,
no fallaba, y no hacía su trabajo.

QUÉ SE ADOPTA Y QUÉ NO. Se adopta la IDEA de instrumentar, no su código: la
licencia IBM 5900-BVU prohíbe copiarlo y además contaminaría el producto de William.
Estos campos son NUESTROS y miden lo que a NOSOTROS nos falló.

EL CAMPO QUE IMPORTA ES `degraded`. Los conteos son contexto; el booleano es el que
habría gritado en julio. Una compactación que reinyecta el aviso de error en vez del
alma tiene que ser distinguible de una sana **en el dato**, no en la lectura de un log.

INVARIANTE DE SEGURIDAD: nada de esto puede tumbar una compactación. Toda función
falla en silencio y devuelve algo usable; medir no puede romper lo medido. Es la
lección del medidor de contexto de ALICE (3-sep): agregarle una dependencia a un
vigilante es peor que no tener el dato.

Owner: ADA — 4-sep-2026 — reescritura de la idea 1 del análisis de Bob.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

# Marca que el hook post-compactación emite cuando NO pudo traer el alma.
# Es la firma exacta del defecto que estuvo semanas invisible.
SENAL_DEGRADADA = "SOUL DB no disponible"

RUTA_POR_DEFECTO = Path.home() / "IA" / "proyecto-seal" / "memory" / "logs" / "compaction_metrics.jsonl"


def contar_transcript(transcript_path: str | Path | None) -> dict[str, int]:
    """Cuenta lo que había ANTES de compactar. Devuelve ceros si no puede leer.

    No distingue por tipo para no atarse a la forma del harness, que ya nos cambió
    una vez: cuenta entradas y bytes, que es lo que se pierde al compactar.
    """
    vacio = {"messages_before": 0, "bytes_before": 0}
    if not transcript_path:
        return vacio
    try:
        total = 0
        octetos = 0
        with Path(transcript_path).open("rb") as fh:
            for raw in fh:
                if raw.strip():
                    total += 1
                    octetos += len(raw)
        return {"messages_before": total, "bytes_before": octetos}
    except OSError:
        return vacio


def es_degradado(contexto: str | None) -> bool:
    """¿El bloque reinyectado es el aviso de error en vez del alma?"""
    return bool(contexto) and SENAL_DEGRADADA in contexto


def construir(fase: str, agente: str, **campos) -> dict:
    """Arma el registro. `fase` es 'pre' o 'post'."""
    registro = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "fase": fase,
        "agente": agente or "desconocido",
    }
    registro.update({k: v for k, v in campos.items() if v is not None})
    return registro


def registrar(registro: dict, ruta: str | Path | None = None) -> bool:
    """Escribe una línea JSONL. NUNCA levanta: medir no puede romper lo medido.

    Devuelve True si escribió, False si no pudo. El llamador ignora el resultado;
    está para que un test pueda distinguir las dos ramas.
    """
    destino = Path(ruta or os.environ.get("SEAL_COMPACTION_METRICS", RUTA_POR_DEFECTO))
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        with destino.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(registro, ensure_ascii=False, default=str) + "\n")
        return True
    except Exception:
        return False
