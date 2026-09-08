"""Brazos del dedup de alertas periódicas.

El caso que da sentido a todo esto está medido: cinco avisos del chequeo de
integridad en cinco horas, tres contenidos distintos, dos pares idénticos byte a
byte. Y el riesgo de la cura es callar un problema que sigue vivo: por eso hay
tantos brazos sobre el RECORDATORIO como sobre el silencio.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import seal_alerta_dedup as dd  # noqa: E402

HORA = 3600.0
T0 = 1_757_000_000.0
CONTENIDO = "SIN FUENTE DE CREDENCIAL: 1\n  seal-soul-metrics-exporter.service\n"
OTRO = "SIN FUENTE DE CREDENCIAL: 2\n  seal-soul-metrics-exporter.service\n  orion-backup.service\n"


@pytest.fixture()
def estado(tmp_path):
    return tmp_path / "estado.json"


def _enviar(estado, contenido, t, clave="integridad"):
    d = dd.decidir(contenido, clave, estado, t)
    if d["mandar"]:
        dd.guardar_estado(estado, clave, d["sha"], t)
    return d


# ─────────────────────────── qa_positive: se manda ───────────────────────────

def test_qa_positive_la_primera_vez_se_manda(estado):
    d = dd.decidir(CONTENIDO, "integridad", estado, T0)
    assert d["mandar"] is True
    assert d["motivo"] == "primera_vez"


def test_qa_positive_si_el_contenido_CAMBIA_se_manda(estado):
    _enviar(estado, CONTENIDO, T0)
    d = dd.decidir(OTRO, "integridad", estado, T0 + HORA)
    assert d["mandar"] is True
    assert d["motivo"] == "cambio"
    assert d["sha_previo"] == dd.huella(CONTENIDO)


def test_qa_positive_una_sola_linea_distinta_ya_es_cambio(estado):
    """El caso real: de 'SIN FUENTE: 2' a 'SIN FUENTE: 1' — una línea entre
    2.500 caracteres iguales. Si esto no dispara, el dedup tapa la señal."""
    _enviar(estado, "a\n" * 400 + "SIN FUENTE DE CREDENCIAL: 2\n", T0)
    d = dd.decidir("a\n" * 400 + "SIN FUENTE DE CREDENCIAL: 1\n", "integridad", estado, T0 + HORA)
    assert d["mandar"] is True and d["motivo"] == "cambio"


# ─────────────────────────── qa_negative: se calla ───────────────────────────

def test_qa_negative_contenido_IDENTICO_dentro_del_plazo_se_calla(estado):
    _enviar(estado, CONTENIDO, T0)
    d = dd.decidir(CONTENIDO, "integridad", estado, T0 + HORA)
    assert d["mandar"] is False
    assert d["motivo"] == "sin_cambios"


def test_qa_negative_cinco_corridas_iguales_mandan_UNA(estado):
    """Reproduce la mañana del 8-sep: 5 corridas, mismo contenido."""
    enviados = [_enviar(estado, CONTENIDO, T0 + i * HORA)["mandar"] for i in range(5)]
    assert enviados == [True, False, False, False, False]


# ──────────── el riesgo de la cura: que un problema vivo se olvide ────────────

def test_el_recordatorio_vuelve_a_sonar_al_vencer_el_plazo(estado):
    _enviar(estado, CONTENIDO, T0)
    d = dd.decidir(CONTENIDO, "integridad", estado, T0 + 24 * HORA)
    assert d["mandar"] is True
    assert d["motivo"] == "recordatorio"


def test_el_recordatorio_NO_suena_un_minuto_antes(estado):
    """Control del borde: 23 h 59 min sigue callado. Sin este brazo, un mutante
    que cambie >= por > pasaría desapercibido."""
    _enviar(estado, CONTENIDO, T0)
    d = dd.decidir(CONTENIDO, "integridad", estado, T0 + 24 * HORA - 60)
    assert d["mandar"] is False


def test_tras_el_recordatorio_el_reloj_se_reinicia(estado):
    _enviar(estado, CONTENIDO, T0)
    _enviar(estado, CONTENIDO, T0 + 24 * HORA)          # recordatorio, re-guarda
    d = dd.decidir(CONTENIDO, "integridad", estado, T0 + 25 * HORA)
    assert d["mandar"] is False, "el recordatorio no debe repetirse cada hora"


# ─────────── el silencio nunca es el default ante un estado dudoso ───────────

def test_qa_control_un_estado_ILEGIBLE_manda_igual(estado):
    estado.write_text("{ esto no es json", encoding="utf-8")
    d = dd.decidir(CONTENIDO, "integridad", estado, T0)
    assert d["mandar"] is True


def test_qa_control_una_marca_de_tiempo_CORRUPTA_manda_igual(estado):
    estado.write_text(json.dumps(
        {"integridad": {"sha": dd.huella(CONTENIDO), "enviada_en": "ayer"}}), encoding="utf-8")
    d = dd.decidir(CONTENIDO, "integridad", estado, T0)
    assert d["mandar"] is True
    assert d["motivo"] == "estado_corrupto"


def test_qa_control_estado_inexistente_manda(estado):
    assert not estado.exists()
    assert dd.decidir(CONTENIDO, "integridad", estado, T0)["mandar"] is True


# ───────────────────────────── aislamiento por clave ─────────────────────────

def test_dos_alertas_distintas_no_se_silencian_entre_si(estado):
    _enviar(estado, CONTENIDO, T0, clave="integridad")
    d = dd.decidir(CONTENIDO, "otro-detector", estado, T0)
    assert d["mandar"] is True, "una clave no puede callar a otra"


# ───────────────── el hash se toma del contenido COMPLETO ─────────────────

def test_un_cambio_mas_alla_del_recorte_publicado_dispara_igual(estado):
    """El .sh publica sólo los primeros 2.500 caracteres. Si el hash se tomara
    del texto recortado, un cambio después del corte no se avisaría nunca."""
    largo_a = "x" * 3000 + "\nSIN FUENTE: 2\n"
    largo_b = "x" * 3000 + "\nSIN FUENTE: 1\n"
    _enviar(estado, largo_a, T0)
    assert largo_a[:2500] == largo_b[:2500], "el prefijo publicado es el mismo"
    d = dd.decidir(largo_b, "integridad", estado, T0 + HORA)
    assert d["mandar"] is True and d["motivo"] == "cambio"


# ───────────────────────────── la interfaz de línea ──────────────────────────

def _cli(*args, entrada=""):
    return subprocess.run(
        [sys.executable, str(pathlib.Path(dd.__file__)), *args],
        input=entrada, capture_output=True, text=True, timeout=60,
    )

def test_cli_devuelve_0_para_mandar_y_10_para_callar(estado):
    p1 = _cli("--clave", "k", "--estado", str(estado), "--marcar-enviada", entrada=CONTENIDO)
    assert p1.returncode == 0, p1.stderr
    assert json.loads(p1.stdout)["motivo"] == "primera_vez"
    p2 = _cli("--clave", "k", "--estado", str(estado), "--marcar-enviada", entrada=CONTENIDO)
    assert p2.returncode == 10, p2.stdout
    assert json.loads(p2.stdout)["mandar"] is False


def test_cli_sin_marcar_enviada_NO_toca_el_estado(estado):
    """Si el envío falla, el estado no debe quedar como si hubiera salido:
    la próxima corrida tiene que reintentar."""
    _cli("--clave", "k", "--estado", str(estado), entrada=CONTENIDO)
    assert not estado.exists()
    p = _cli("--clave", "k", "--estado", str(estado), entrada=CONTENIDO)
    assert p.returncode == 0, "sin marcar, la siguiente corrida vuelve a mandar"
