#!/usr/bin/env python3
"""Corte de bucles de publicación en dos escalones. Avisa a las 3, corta a la 6.

POR QUÉ EXISTE (idea de IBM Bob reescrita desde cero, 4-sep-2026):
Bob corta sus bucles en DOS escalones: a las 3 llamadas idénticas le avisa al
modelo que cambie de enfoque, a las 5 declara «doom loop» y corta. Nosotros no
teníamos nada. El 2-may-2026 el bucle de reflejos de NEXUS publicó **199 mensajes
en 60 segundos** y lo cortó JARVIS a mano matando el proceso. Con esto se habría
cortado solo.

Se adopta la IDEA, no su código: licencia IBM 5900-BVU.

EL INVARIANTE QUE NO SE PISA, y está escrito en `seal_send.py`:
    «este script es la ÚNICA vía de escritura del equipo […] nunca se deja mudo
     a nadie»

**Suprimir la sexta copia idéntica NO deja mudo a nadie: el contenido ya llegó
cinco veces.** Mudez es que el contenido no llegue, no que no lleguen copias. Esa
distinción es la única razón por la que este guard puede existir, y si alguien la
discute, tiene razón en discutirla — no la escondí.

TRES CANDADOS PARA NO REPETIR EL ERROR DEL CANDADO DESTRUCTIVO, que el 1-sep dejó
mudos a ALICE, a JARVIS y dos veces a NEXUS:
  1. FALLA ABIERTO. Cualquier excepción del guard => se manda igual. Sin excepción.
  2. SÓLO cuenta lo IDÉNTICO: mismo destinatario, mismo canal, mismo cuerpo, dentro
     de una ventana corta. Un mensaje distinto nunca cuenta, por parecido que sea.
  3. SALIDA DE EMERGENCIA documentada: `SEAL_SEND_NO_LOOP_GUARD=1` lo desactiva.
     Si me equivoqué en un caso, nadie queda encerrado esperando que yo lo arregle.

Owner: ADA — 4-sep-2026.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

VENTANA_SEG = 120        # un bucle es rápido; un trabajo legítimo repetido, no
AVISO_EN = 3             # avisa y sigue
AVISO_FUERTE_EN = 5      # avisa fuerte y sigue
CORTA_DESDE = 6          # a partir de acá suprime la copia

RUTA_POR_DEFECTO = Path.home() / "IA" / "proyecto-seal" / "memory" / "logs" / "send_loop_guard.json"


def huella(destinatario: str, canal: str, cuerpo: str) -> str:
    """Identidad de un envío. Cambia cualquiera de los tres y ya no es el mismo."""
    h = hashlib.sha256()
    for parte in (destinatario or "", canal or "", cuerpo or ""):
        h.update(parte.encode("utf-8", "replace")); h.update(b"\x00")
    return h.hexdigest()[:32]


def _ruta() -> Path:
    return Path(os.environ.get("SEAL_LOOP_GUARD_STATE", RUTA_POR_DEFECTO))


def _leer(ruta: Path) -> dict:
    try:
        d = json.loads(ruta.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def evaluar(destinatario: str, canal: str, cuerpo: str, agente: str = "",
            ahora: float | None = None) -> tuple[str, int, str]:
    """Decide qué hacer con este envío.

    Devuelve (accion, repeticiones, aviso) donde accion es:
      "enviar"   — normal, o el guard no pudo trabajar (falla ABIERTO)
      "avisar"   — se manda igual, con aviso al remitente
      "suprimir" — copia idéntica de más; el contenido ya llegó varias veces

    NUNCA levanta. Si algo sale mal, devuelve "enviar".
    """
    try:
        if os.environ.get("SEAL_SEND_NO_LOOP_GUARD") == "1":
            return "enviar", 0, ""
        t = time.time() if ahora is None else ahora
        clave = f"{agente}:{huella(destinatario, canal, cuerpo)}"
        ruta = _ruta()
        estado = _leer(ruta)
        entrada = estado.get(clave) or {}
        visto = float(entrada.get("ultimo", 0) or 0)
        n = int(entrada.get("n", 0) or 0) + 1 if (t - visto) <= VENTANA_SEG else 1

        estado = {k: v for k, v in estado.items()
                  if (t - float((v or {}).get("ultimo", 0) or 0)) <= VENTANA_SEG * 5}
        estado[clave] = {"n": n, "ultimo": t}
        try:
            ruta.parent.mkdir(parents=True, exist_ok=True)
            tmp = ruta.with_suffix(".tmp")
            tmp.write_text(json.dumps(estado, ensure_ascii=False), encoding="utf-8")
            tmp.replace(ruta)
        except Exception:
            pass                      # no poder anotar NO puede impedir el envío

        if n >= CORTA_DESDE:
            return ("suprimir", n,
                    f"[seal_send][BUCLE] copia IDENTICA n.{n} en {VENTANA_SEG}s hacia "
                    f"'{destinatario}'. El contenido ya llego {CORTA_DESDE - 1} veces; "
                    f"esta no se manda. Cambia el mensaje o el destinatario. "
                    f"Si esto es un falso positivo: SEAL_SEND_NO_LOOP_GUARD=1")
        if n >= AVISO_FUERTE_EN:
            return ("avisar", n,
                    f"[seal_send][BUCLE] va la {n}a copia IDENTICA en {VENTANA_SEG}s. "
                    f"A partir de la {CORTA_DESDE}a dejo de mandarlas.")
        if n >= AVISO_EN:
            return ("avisar", n,
                    f"[seal_send][BUCLE] {n} copias IDENTICAS en {VENTANA_SEG}s hacia "
                    f"'{destinatario}'. Si estas en un bucle, cambia de enfoque.")
        return "enviar", n, ""
    except Exception:
        return "enviar", 0, ""        # candado 1: falla ABIERTO, siempre
