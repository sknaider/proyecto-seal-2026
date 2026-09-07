#!/usr/bin/env python3
"""Sujetos declarados por DOS O MAS manifiestos: cerrar un carril puede abrir el otro.

Medido el 7-sep-2026: `messages/session_checkpoint.py` es sujeto de `checkpoint-g2` y de
`nexus-credential-paths`. NEXUS lo reescribio para cerrar el primero y con eso invalido la
evidencia de mutacion del segundo -correctamente, y sin que nada lo avisara-. El gate detecta
la caducidad UNO POR UNO; nadie mira el acoplamiento ENTRE manifiestos.

Grave (exit 1) cuando un archivo COMPARTIDO tiene una firma que ya no cubre los bytes del arbol.

LO QUE ESTE DETECTOR NO PRUEBA: que el cambio lo haya causado el OTRO carril. El gate ya marca
esa caducidad manifiesto por manifiesto (independent_review_bytes_stale); lo unico que agrega
aca es DECIR CON QUIEN se comparte el archivo, para que el duenzo sepa a quien avisar. La causa
es una hipotesis a verificar mirando los commits, no un hecho que salga de esta salida.

Salidas: 0 sin desfase · 1 hay firma desfasada por un carril ajeno · 2 no se pudo mirar (fallo silencioso).
"""
from __future__ import annotations
import hashlib, json, pathlib, sys

RAIZ = pathlib.Path(__file__).resolve().parents[1]


def _sha(ruta: pathlib.Path) -> str | None:
    try:
        return hashlib.sha256(ruta.read_bytes()).hexdigest()
    except OSError:
        return None


def revisar(carpeta: pathlib.Path) -> tuple[dict[str, list[str]], list[str]]:
    por_sujeto: dict[str, list[str]] = {}
    firmado: dict[tuple[str, str], str] = {}      # (manifiesto, sujeto) -> sha firmado
    for ruta in sorted(carpeta.glob("*.json")):
        try:
            m = json.loads(ruta.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            continue
        if not isinstance(m, dict):
            continue
        recibo = ((m.get("review") or {}).get("receipt") or {}) if isinstance(m.get("review"), dict) else {}
        firmados = recibo.get("reviewed_sha256") or {}
        for sujeto in m.get("subjects") or []:
            por_sujeto.setdefault(sujeto, []).append(ruta.stem)
            if isinstance(firmados, dict) and sujeto in firmados:
                firmado[(ruta.stem, sujeto)] = firmados[sujeto]

    graves: list[str] = []
    for sujeto, manifiestos in sorted(por_sujeto.items()):
        if len(manifiestos) < 2:
            continue
        real = _sha(RAIZ / sujeto)
        for manifiesto in manifiestos:
            f = firmado.get((manifiesto, sujeto))
            if f and real and f != real:
                otros = [x for x in manifiestos if x != manifiesto]
                graves.append(
                    f"{sujeto}\n    la firma de {manifiesto} NO cubre los bytes del arbol"
                    f"\n    ademas lo declaran: {', '.join(otros)}  <- a quien avisar, no la causa probada")
    return por_sujeto, graves


def main(argv: list[str]) -> int:
    carpeta = pathlib.Path(argv[1]) if len(argv) > 1 else RAIZ / "quality" / "manifests"
    if not carpeta.is_dir() or not any(carpeta.glob("*.json")):
        print(f"SIN MIRAR: no hay manifiestos en {carpeta}. Un detector sin sujetos no dice 'limpio'.")
        return 2
    por_sujeto, graves = revisar(carpeta)
    compartidos = {s: m for s, m in por_sujeto.items() if len(m) > 1}
    for s, m in sorted(compartidos.items()):
        print(f"COMPARTIDO  {s}\n    {', '.join(sorted(m))}")
    for g in graves:
        print(f"FIRMA VENCIDA EN ARCHIVO COMPARTIDO  {g}")
    print(f"\n{len(por_sujeto)} sujetos · {len(compartidos)} compartidos · {len(graves)} con firma vencida")
    return 1 if graves else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
