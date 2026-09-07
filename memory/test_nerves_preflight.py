#!/usr/bin/env python3
"""Tests del preflight de NERVES.

El test que importa es `test_claimed_sin_audit_manda_a_spawn_y_prohibe_bind`: es
exactamente el caso donde yo me equivoqué el 2-ago y perdí la misión 7ad08eb0. Si algún
día alguien "simplifica" el preflight y ese caso deja de distinguirse, este test cae.

Los estados se construyen en un directorio temporal a propósito. Un test que dependa de
las misiones reales envejece: hoy `7ad08eb0` está en `running`, mañana puede tener un
HOLD, y entonces el test pasaría a medir otra cosa sin avisar.
"""
from __future__ import annotations

import json
from pathlib import Path

from memory.nerves_preflight import paso_siguiente

MID = "7ad08eb0-f16a-5bd7-965f-917fbcde2d61"


def _escenario(tmp_path: Path, status: str, *, con_audit: bool) -> tuple[Path, Path]:
    # `parents/exist_ok`: varios tests reusan el mismo tmp_path o piden subcarpetas.
    # Sin esto el escenario explota por el ANDAMIO y parece un fallo del preflight.
    tmp_path.mkdir(parents=True, exist_ok=True)
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"deliveries": {MID: {"status": status}}}))
    inbox = tmp_path / "inbox"
    inbox.mkdir(exist_ok=True)
    if con_audit:
        (inbox / f"{MID}.prompt-render.json").write_text("{}")
    return state, inbox


def _correr(tmp_path: Path, status: str, *, con_audit: bool) -> tuple[int, str]:
    state, inbox = _escenario(tmp_path, status, con_audit=con_audit)
    return paso_siguiente(MID, state_path=state, inbox=inbox)


def test_live_notified_manda_a_claim(tmp_path):
    codigo, texto = _correr(tmp_path, "live_notified", con_audit=False)
    assert codigo == 0
    assert "CLAIM" in texto


def test_claimed_sin_audit_manda_a_spawn_y_prohibe_bind(tmp_path):
    """EL test. Es el paso donde perdi 7ad08eb0.

    No alcanza con que diga "spawn": tiene que decir explicitamente que NO se haga
    bind-platform todavia. Un preflight que sugiere lo correcto sin nombrar lo que
    clausura deja intacta la trampa -- yo tambien "sabia" que habia que lanzar el
    razonador, y aun asi bindee primero.
    """
    codigo, texto = _correr(tmp_path, "claimed", con_audit=False)
    assert codigo == 0
    assert "RAZONADOR" in texto
    assert "NO HAGAS bind-platform" in texto
    assert "MUERTA" in texto


def test_claimed_con_audit_manda_a_bind(tmp_path):
    codigo, texto = _correr(tmp_path, "claimed", con_audit=True)
    assert codigo == 0
    assert "BIND-PLATFORM" in texto
    # El perfil cambia entre misiones: copiar el hash viejo hace fallar el bind.
    assert "RECALCULA" in texto


def test_running_con_audit_manda_al_recibo(tmp_path):
    codigo, texto = _correr(tmp_path, "running", con_audit=True)
    assert codigo == 0
    assert "RECIBO" in texto


def test_running_sin_audit_es_callejon_sin_salida(tmp_path):
    """El estado en que quedo 7ad08eb0: hay que reconocerlo como muerto, no proponer pasos."""
    codigo, texto = _correr(tmp_path, "running", con_audit=False)
    assert codigo == 1
    assert "CALLEJON SIN SALIDA" in texto
    assert "HOLD" in texto


def test_terminal_no_propone_nada(tmp_path):
    for status in ("completed", "failed", "abstained"):
        codigo, texto = _correr(tmp_path, status, con_audit=True)
        assert codigo == 1
        assert "TERMINAL" in texto


def test_mision_inexistente_no_se_confunde_con_sin_pasos(tmp_path):
    """exit 2 (no pude medir) es DISTINTO de exit 1 (no hay paso).

    Devolver "no hay nada que hacer" para una mision que ni existe es el vacio silencioso
    de siempre: se lee como "todo en orden".
    """
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"deliveries": {}}))
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    codigo, texto = paso_siguiente(MID, state_path=state, inbox=inbox)
    assert codigo == 2
    assert "no figura" in texto


def test_state_ausente_no_pude_medir(tmp_path):
    codigo, texto = paso_siguiente(
        MID, state_path=tmp_path / "no_existe.json", inbox=tmp_path)
    assert codigo == 2
    assert "NO PUDE MEDIR" in texto


def test_el_audit_manda_sobre_el_status(tmp_path):
    """CONTROL DIFERENCIAL: el mismo `claimed` da pasos OPUESTOS segun el audit.

    Si esto fallara, el preflight estaria decidiendo por el status solo -- que es
    precisamente la lectura incompleta que me costo la mision. `running` tambien lo
    escribe `bind-platform`, asi que el status NO distingue si el spawn ocurrio.
    """
    _, sin = _correr(tmp_path / "a", "claimed", con_audit=False)
    _, con = _correr(tmp_path / "b", "claimed", con_audit=True)
    assert "RAZONADOR" in sin and "BIND-PLATFORM" not in sin
    assert "BIND-PLATFORM" in con and "NO HAGAS bind-platform" not in con
