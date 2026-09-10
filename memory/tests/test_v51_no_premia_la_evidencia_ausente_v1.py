#!/usr/bin/env python3
"""v5.1 no puede premiar la ausencia de evidencia — brazos QA.

**El defecto (ADA, 9-sep-2026).** `v5.1 External Evidence Gate` pregunta si una
afirmación de sprint se puede replicar desde ids de evidencia persistidos. Dos de
sus cuatro casillas eran `report_ids.issubset(...)`. Cuando el reporte desapareció
con el borrado del 7-sep, `report_ids` quedó VACÍO — y **el conjunto vacío es
subconjunto de todo**, así que las dos daban True *porque la evidencia faltaba*.
El test publicaba 50 % sin haber mirado una sola fila de la base.

Medido antes del arreglo: `report_run_ids: []`, `found_ids: []`, y aun así
`db_has_report_ids: True`, `all_report_ids_passed: True`, score 50.0.
"""
from __future__ import annotations

import os
import sys

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import seal_bench_v5 as v5  # noqa: E402

REQUERIDOS = set(v5.REQUIRED_RUN_IDS)


def _puntaje(checks: dict[str, bool]) -> float:
    return sum(checks.values()) / len(checks) * 100.0


# ── qa_negative — la ausencia nunca puntúa ────────────────────────────────────

def test_qa_negative_sin_ids_ninguna_casilla_da_verde():
    """EL DEFECTO ORIGINAL. Con el reporte perdido daba 50.0."""
    checks = v5.construir_checks_de_evidencia(
        report_exists=False, report_ids=set(), found_ids=set(), passed_ids=set(),
    )
    assert _puntaje(checks) == 0.0
    assert not checks["db_has_report_ids"]
    assert not checks["all_report_ids_passed"]


def test_qa_negative_sin_ids_NO_se_salva_con_la_base_llena():
    """La base puede tener miles de corridas: sin ids que replicar, no prueban nada.

    Es el caso exacto del 7-sep: la base estaba intacta y el reporte no existía.
    """
    checks = v5.construir_checks_de_evidencia(
        report_exists=False, report_ids=set(),
        found_ids=set(REQUERIDOS) | {1, 2, 3}, passed_ids=set(REQUERIDOS) | {1, 2, 3},
    )
    assert _puntaje(checks) == 0.0


def test_qa_negative_un_id_inventado_en_el_reporte_hace_caer_el_test():
    """El reporte no puede aprobarse a sí mismo agregando ids convenientes."""
    checks = v5.construir_checks_de_evidencia(
        report_exists=True, report_ids=REQUERIDOS | {999999},
        found_ids=set(REQUERIDOS), passed_ids=set(REQUERIDOS),
    )
    assert _puntaje(checks) < 100.0
    assert not checks["db_has_report_ids"]


def test_qa_negative_un_id_que_existe_pero_no_paso_no_alcanza():
    checks = v5.construir_checks_de_evidencia(
        report_exists=True, report_ids=REQUERIDOS,
        found_ids=set(REQUERIDOS), passed_ids=set(REQUERIDOS) - {min(REQUERIDOS)},
    )
    assert checks["db_has_report_ids"]
    assert not checks["all_report_ids_passed"]


def test_qa_negative_un_reporte_incompleto_no_pasa():
    """Faltando un id obligatorio, la casilla de completitud tiene que caer."""
    parcial = set(REQUERIDOS) - {max(REQUERIDOS)}
    checks = v5.construir_checks_de_evidencia(
        report_exists=True, report_ids=parcial, found_ids=parcial, passed_ids=parcial,
    )
    assert not checks["report_has_required_ids"]
    assert _puntaje(checks) < 100.0


# ── qa_positive — la evidencia completa sí puntúa ─────────────────────────────

def test_qa_positive_evidencia_completa_y_replicada_da_cien():
    checks = v5.construir_checks_de_evidencia(
        report_exists=True, report_ids=set(REQUERIDOS),
        found_ids=set(REQUERIDOS), passed_ids=set(REQUERIDOS),
    )
    assert _puntaje(checks) == 100.0


def test_qa_positive_ids_de_mas_en_la_base_no_molestan():
    """La base tiene miles de corridas; sólo importan las que el reporte cita."""
    checks = v5.construir_checks_de_evidencia(
        report_exists=True, report_ids=set(REQUERIDOS),
        found_ids=set(REQUERIDOS) | {1, 2, 3}, passed_ids=set(REQUERIDOS) | {1, 2, 3},
    )
    assert _puntaje(checks) == 100.0


# ── qa_control — que el test siga pudiendo distinguir casos ───────────────────

def test_qa_control_el_arreglo_no_rompio_la_casilla_de_existencia():
    """Control: `report_exists` sigue reflejando su argumento, no quedó atada al resto."""
    con = v5.construir_checks_de_evidencia(
        report_exists=True, report_ids=set(REQUERIDOS),
        found_ids=set(REQUERIDOS), passed_ids=set(REQUERIDOS))
    sin = v5.construir_checks_de_evidencia(
        report_exists=False, report_ids=set(REQUERIDOS),
        found_ids=set(REQUERIDOS), passed_ids=set(REQUERIDOS))
    assert con["report_exists"] and not sin["report_exists"]
    assert _puntaje(con) > _puntaje(sin)


def test_qa_control_las_cuatro_casillas_siguen_existiendo():
    """Si alguien 'arregla' borrando una casilla, el puntaje sube sin medir más."""
    checks = v5.construir_checks_de_evidencia(
        report_exists=True, report_ids=set(REQUERIDOS),
        found_ids=set(REQUERIDOS), passed_ids=set(REQUERIDOS))
    assert set(checks) == {
        "report_exists", "report_has_required_ids",
        "db_has_report_ids", "all_report_ids_passed",
    }


# ── unit — la lista de ids obligatorios vive en el TEST, no en el reporte ─────

def test_unit_los_ids_obligatorios_los_declara_el_benchmark():
    """Si la lista saliera del reporte, el reporte se aprobaría a sí mismo."""
    assert len(REQUERIDOS) >= 10
    assert all(isinstance(i, int) for i in REQUERIDOS)


@pytest.mark.parametrize("vacio", [set(), frozenset()])
def test_unit_cualquier_forma_de_vacio_se_trata_igual(vacio):
    checks = v5.construir_checks_de_evidencia(
        report_exists=True, report_ids=set(vacio),
        found_ids=set(REQUERIDOS), passed_ids=set(REQUERIDOS))
    assert not checks["db_has_report_ids"]
    assert not checks["all_report_ids_passed"]
