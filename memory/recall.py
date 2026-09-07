#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""recall.py — RECALL del clon-espejo sobre SU memory store LOCAL (device).

El clon-espejo lo ejecuta (bash, human-gated) para RECUPERAR su historia/decisiones/proyectos reales
desde su memory store replicado (cifrado at-rest) en vez de inventar o decir «no sé». Pedido de William
(5-jul): «replicar el memory store completo + active_recall sobre SUS memorias». El agente = $SEAL_AGENT
(lo setea espejo_launch) o argv[2].

Uso:  python3 ~/.seal/lib/recall.py "<consulta>"
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from minisoul_boot import active_recall_local


def _authoritative_agent() -> str:
    """La identidad del clon la fija el LAUNCHER, NO el modelo (FABLE: un clon NO puede leer memorias de
    otro). Orden: archivo protegido .clone_id del workspace (0400, escrito por espejo_launch) → env
    SEAL_CLONE_AGENT. NO se acepta un agente por argumento (evita «recall <otro> <q>» = cross-read)."""
    try:
        cid = os.path.join(os.getcwd(), ".clone_id")
        if os.path.isfile(cid):
            a = open(cid).read().strip().upper()
            if a:
                return a
    except Exception:
        pass
    return (os.environ.get("SEAL_CLONE_AGENT") or os.environ.get("SEAL_AGENT") or "").strip().upper()


def main():
    query = sys.argv[1] if len(sys.argv) > 1 else ""
    agent = _authoritative_agent()
    if not agent:
        print("(recall: no sé qué agente soy — falta .clone_id / SEAL_CLONE_AGENT)")
        return
    if not query.strip():
        print("(recall: pasá una consulta, ej: recall.py 'decisiones sobre el proyecto X')")
        return
    try:
        hits = active_recall_local(query, agent, limit=12)
    except Exception as e:
        print(f"(recall: error leyendo tu memoria: {e})")
        return
    if not hits:
        print(f"(recall: sin memorias que matcheen «{query}». Probá otras palabras clave.)")
        return
    print(f"### Tus memorias reales sobre «{query}» ({len(hits)} recuperadas):")
    for h in hits:
        c = (h.get("content") or "").strip().replace("\n", " ")
        print(f"- [{h.get('category')}|imp{h.get('importance')}] {c[:500]}")


if __name__ == "__main__":
    main()
