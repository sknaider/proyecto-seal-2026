#!/usr/bin/env python3
"""Decide si una alerta periódica debe MANDARSE o CALLARSE.

POR QUÉ EXISTE (NEXUS, 8-sep-2026, a pedido de ALICE, dueña del detector):
`seal_chequeo_integridad_recuperacion.sh` avisa «sólo cuando hay hallazgo», que
es la regla correcta de William (brief por hallazgo, no por reloj). Pero
«hay hallazgo» y «hay algo NUEVO» no son lo mismo, y medido ese día:

    05:52  sha da899b6f1294
    06:53  sha da899b6f1294   <- identica
    07:53  sha 74c0513a6bf5
    08:56  sha 55c9f290e7cd
    09:56  sha 55c9f290e7cd   <- identica BYTE A BYTE

Cinco avisos, tres contenidos. Y el día que uno traiga algo nuevo, la línea que
cambió viaja escondida entre 2.500 caracteres iguales a los de la hora anterior:
a las 08:56 el único cambio real fue `SIN FUENTE: 2 -> 1`, o sea la confirmación
de que orion había quedado arreglado, y pasó desapercibida.

EL RIESGO DE LA CURA, Y POR ESO EL RECORDATORIO: callar para siempre un hallazgo
que persiste es peor que repetirlo. Un problema con dueño y sin arreglar tiene
que volver a asomar. Por eso hay `max_horas`: silencio mientras no cambie, pero
nunca más de esa ventana sin recordar que sigue ahí.

Sobre el HASH: se calcula sobre el contenido COMPLETO de hallazgos, no sobre el
texto recortado que se publica. Si se hasheara lo recortado, un cambio más allá
del corte no dispararía aviso y el silencio sería un error, no una decisión.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sys
import time

ESTADO_POR_DEFECTO = pathlib.Path.home() / ".local/state/seal/alerta_dedup.json"
MAX_HORAS_POR_DEFECTO = 24.0


def huella(contenido: str) -> str:
    """sha256 del contenido completo de hallazgos."""
    return hashlib.sha256(contenido.encode("utf-8")).hexdigest()


def leer_estado(ruta: pathlib.Path) -> dict:
    """Estado previo, o vacío. Un estado ilegible NO silencia: se manda."""
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return datos if isinstance(datos, dict) else {}


def guardar_estado(ruta: pathlib.Path, clave: str, sha: str, ahora: float) -> None:
    estado = leer_estado(ruta)
    estado[clave] = {"sha": sha, "enviada_en": ahora}
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_suffix(".tmp")
    tmp.write_text(json.dumps(estado, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, ruta)  # atómico: dos corridas solapadas no dejan basura a medias


def decidir(contenido: str, clave: str, ruta_estado: pathlib.Path,
            ahora: float, max_horas: float = MAX_HORAS_POR_DEFECTO) -> dict:
    """MANDAR o CALLAR, con el motivo explícito.

    - contenido nuevo o cambiado  -> MANDAR (motivo: cambio / primera_vez)
    - igual pero vencido el plazo -> MANDAR (motivo: recordatorio)
    - igual y dentro del plazo    -> CALLAR
    """
    sha = huella(contenido)
    previo = leer_estado(ruta_estado).get(clave)

    if not isinstance(previo, dict) or "sha" not in previo:
        return {"mandar": True, "motivo": "primera_vez", "sha": sha, "sha_previo": None}

    if previo.get("sha") != sha:
        return {"mandar": True, "motivo": "cambio", "sha": sha,
                "sha_previo": previo.get("sha")}

    try:
        edad_h = (ahora - float(previo.get("enviada_en", 0))) / 3600.0
    except (TypeError, ValueError):
        # marca de tiempo corrupta: se manda. El silencio nunca es el default.
        return {"mandar": True, "motivo": "estado_corrupto", "sha": sha,
                "sha_previo": previo.get("sha")}

    if edad_h >= max_horas:
        return {"mandar": True, "motivo": "recordatorio", "sha": sha,
                "sha_previo": previo.get("sha"), "horas": round(edad_h, 2)}

    return {"mandar": False, "motivo": "sin_cambios", "sha": sha,
            "sha_previo": previo.get("sha"), "horas": round(edad_h, 2)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--clave", required=True, help="identifica la alerta (una por detector)")
    ap.add_argument("--estado", type=pathlib.Path, default=ESTADO_POR_DEFECTO)
    ap.add_argument("--max-horas", type=float, default=MAX_HORAS_POR_DEFECTO)
    ap.add_argument("--archivo", type=pathlib.Path,
                    help="contenido de hallazgos; por defecto stdin")
    ap.add_argument("--marcar-enviada", action="store_true",
                    help="registra el envío (usar sólo si el envío salió bien)")
    args = ap.parse_args(argv)

    contenido = (args.archivo.read_text(encoding="utf-8") if args.archivo
                 else sys.stdin.read())
    d = decidir(contenido, args.clave, args.estado, time.time(), args.max_horas)

    if args.marcar_enviada and d["mandar"]:
        guardar_estado(args.estado, args.clave, d["sha"], time.time())

    print(json.dumps(d, ensure_ascii=False))
    return 0 if d["mandar"] else 10  # 10 = callar; 0 = mandar


if __name__ == "__main__":
    raise SystemExit(main())
