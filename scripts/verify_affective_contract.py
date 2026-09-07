#!/usr/bin/env python3
"""Delivery-effect verifier del clasificador is_affective (manifest affective-gate).

Prueba por-efecto que el DELIVERABLE de este subject —clasificar saludo/afecto PURO
vs mensaje sustantivo— se cumple sobre casos canónicos, con control NO-vacuo (si
clasificara todo igual, no entregaría nada). NO prueba el efecto vivo del coordinador
(eso lo verifica coordinator-cutover tras desplegar el wiring de chat_server.py); prueba
el contrato de la función load-bearing que ese wiring consume.

Fail-closed: cualquier caso que no cumpla -> exit!=0 y ok:false. Sin red, sin DB.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "messages"))
import flood_form_gate as g  # noqa: E402

# (texto, esperado) — afecto PURO pasa; sustancia/smuggling NO pasa; control no-vacuo.
CASES = [
    ("buenos días, William", True),
    ("gracias equipo", True),
    ("un abrazo, dadito", True),
    ("hola", True),
    ("buenos días. Confirmo que el deploy pasó 35/35.", False),   # fachada + sustancia
    ("gracias, ahora mandame el reporte", False),                 # gracias + orden
    ("El instalador falló con exit 123, revisando", False),       # reporte (control no-vacuo)
    ("gracias " * 30, False),                                      # fachada larga (>200)
    ("hola,\ngracias", False),                                     # smuggling multilínea
    # Acks mínimos y saludos-pregunta (agregados 2-sep para el coordinador, que
    # consume este mismo átomo). Sin ellos, 11 de 46 saludos de William caían
    # fuera y `classify_mode` los mandaba al canal de TRABAJO.
    ("ok", True),
    ("listo", True),
    ("dale", True),
    ("descansen", True),
    ("buen descanso, chicos", True),
    ("como van", True),
    ("como durmieron", True),
    ("que onda", True),
    # Controles NEGATIVOS de los mismos átomos: el ack sólo vale si el mensaje es
    # ENTERAMENTE el ack. Un átomo corto es más fácil de usar como fachada que un
    # "buenos días", así que estos son los que hay que mirar.
    ("ok, pero el deploy falló con exit 123", False),
    ("listo el fix, reinicié el daemon", False),
    ("dale, mandame el reporte de la métrica", False),
    ("como van los tests del coordinador?", False),
]


def main() -> int:
    fails = [(t, exp, g.is_affective(t)) for t, exp in CASES if g.is_affective(t) is not exp]
    passed_pos = sum(1 for t, exp in CASES if exp and g.is_affective(t) is True)
    passed_neg = sum(1 for t, exp in CASES if not exp and g.is_affective(t) is False)
    # control no-vacuo: deben existir AMBOS lados (si todo cayera de un lado, es vacuo).
    non_vacuous = passed_pos > 0 and passed_neg > 0
    ok = not fails and non_vacuous
    result = {
        "ok": ok,
        "cases": len(CASES),
        "positives_pass": passed_pos,
        "negatives_pass": passed_neg,
        "non_vacuous": non_vacuous,
        "failures": [{"text": t[:50], "expected": e, "got": got} for t, e, got in fails],
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
