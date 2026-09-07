"""Quality-gate tests para flood_form_gate.is_affective — exención SALUDO/AFECTO
del single-voice (regla de oro William 31-jul: "el saludo y el afecto no se
bloquean nunca").

Owner: NEXUS. Revisor independiente: FABLE.

Cuatro brazos QA (el manifest los referencia por node-id):
  - unit          : todo el archivo.
  - qa_positive   : saludos/afecto genuinos DEBEN pasar (no-vacuo: si nada pasara,
                    la exención estaría muerta y volveríamos al 409 que sufrió William).
  - qa_negative   : mensajes con sustancia/smuggling NO deben pasar (seguridad del
                    anti-flood: un informe no puede colarse disfrazado de afecto).
  - qa_control    : controles no-vacuos — un reporte NO es afectivo, y no rompemos
                    is_receipt_ack.

Los tests de qa_negative/qa_control son además los verdugos de los mutantes
adversariales explícitos declarados en quality/mutation-affective-gate.json:
cada guard de is_affective() tiene un test que muere si el guard se remueve.
Hermético, sin red, sin DB (is_affective es regex puro).
"""
import sys
from pathlib import Path

import pytest

# messages/ al path (padre de tests/) para importar el módulo bajo prueba.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import flood_form_gate as g  # noqa: E402


# --------------------------------------------------------------------------- #
# qa_positive — saludo/afecto PURO debe pasar (exento de ambos gates).
# Control no-vacuo: si is_affective diera False para todo, la exención no existe.
# --------------------------------------------------------------------------- #
POS = [
    "buenos días", "Buenos días, William", "buenos días equipo, ¿cómo amanecieron?",
    "gracias!", "muchas gracias, crack", "mil gracias", "hola familia",
    "¿cómo estás, William?", "cómo andás dadito", "un abrazo, dadito", "un fuerte abrazo",
    "buenos días 🌅", "gracias ❤️", "los quiero", "feliz día equipo", "qué bueno leerte",
    "buenas noches, familia", "hola", "buenas", "saludos a todos", "cómo van chicos",
]


@pytest.mark.parametrize("text", POS)
def test_positive_greetings_pass(text):
    assert g.is_affective(text) is True, f"saludo genuino debería pasar: {text!r}"


# --------------------------------------------------------------------------- #
# qa_negative — mensajes con sustancia / intentos de smuggling siguen gated.
# Cada test mata un mutante explícito (quitar un guard hace pasar el caso).
# --------------------------------------------------------------------------- #
def test_negative_greeting_plus_substance():
    # mutante: fullmatch->match haría pasar el prefijo afectivo con cola sustantiva.
    assert g.is_affective("buenos días. Confirmo que el deploy pasó 35/35 sin FP.") is False


def test_negative_length_cap_facade():
    # mutante len_cap_guard_removed: sin el cap, 240 chars de atoms afectivos
    # REPETIDOS ("gracias gracias...") fullmatchean. El cap es lo que lo rechaza.
    facade = "gracias " * 30
    assert len(facade) > 200
    assert g.is_affective(facade) is False


def test_negative_newline_multiclause():
    # mutante newline_guard_removed: sin el guard, "hola,\ngracias" colapsa a
    # "hola, gracias" (dos cláusulas con coma) y fullmatchea. El guard lo rechaza.
    assert g.is_affective("hola,\ngracias") is False


def test_negative_url_guard():
    # mutante: quitar el check de url deja viajar un enlace en un "saludo".
    assert g.is_affective("buenos dias visita https://x.com") is False


def test_negative_code_fence_guard():
    # mutante: quitar el check de ``` deja pasar un snippet de código.
    assert g.is_affective("hola ```rm -rf /tmp/x```") is False


def test_negative_greeting_plus_work_question():
    # fullmatch: un saludo + pregunta de trabajo no es afecto puro.
    assert g.is_affective("buenos días, ¿ya está listo el instalador?") is False


def test_negative_work_order_after_thanks():
    assert g.is_affective("gracias, ahora mandame el reporte del count") is False


def test_negative_bare_work_request():
    assert g.is_affective("William, necesito que confirmes el scope B del candado") is False


# --------------------------------------------------------------------------- #
# qa_control — controles NO-vacuos: si is_affective diera True para un reporte,
# sería un bypass total del anti-flood. Y no debemos romper is_receipt_ack.
# --------------------------------------------------------------------------- #
def test_control_report_is_not_affective():
    # mutante: hacer el átomo afectivo opcional dejaría pasar cualquier sustantivo.
    assert g.is_affective("El instalador falló con exit 123, revisando la causa") is False


def test_control_bare_question_is_not_affective():
    assert g.is_affective("¿ya cerraste el ticket?") is False


def test_control_receipt_ack_still_works():
    # el fix del afecto NO debe romper la exención hermana (ACK de recibo).
    assert g.is_receipt_ack("recibido, william") is True


def test_control_empty_and_none():
    assert g.is_affective("") is False
    assert g.is_affective(None) is False
