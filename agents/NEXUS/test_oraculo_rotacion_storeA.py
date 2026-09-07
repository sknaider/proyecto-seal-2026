"""Contrato del oráculo de rotación de Store-A.

Por qué existe: el oráculo trae un `--self-test`, y un self-test NO es un test
que otro pueda correr — es el sujeto evaluándose a sí mismo. Este archivo lo
ejecuta desde afuera y además construye sus propias fixtures, de modo que el
oráculo se mide contra un criterio que no vive dentro de él.

Los cuatro estados son NOMBRADOS y cada uno tiene su rc, para poder decir
«roto y lo vi» en vez de «no falló»:

    rc=0  ABSTENIDO_ninguno_en_ventana
    rc=1  FALLA_vencido_sin_rotar
    rc=2  EN_VENTANA_debe_rotar
    rc=3  ILEGIBLE_no_puedo_medir
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ORACULO = Path(__file__).resolve().parent / "oraculo_rotacion_storeA.sh"

# La ventana del rotador es de 72 h; el vencimiento, 30 días.
HORA = 3600
DIA = 24 * HORA


def _correr(store: Path, agentes: str = "NEXUS") -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["SEAL_STOREA_DIR"] = str(store)
    # Sin esto el oráculo busca los CUATRO agentes reales dentro de la fixture
    # y los tres ausentes lo mandan a ILEGIBLE — el mismo defecto que su propio
    # self-test documenta haber tenido.
    env["SEAL_STOREA_AGENTS"] = agentes
    return subprocess.run(
        ["bash", str(ORACULO)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _sembrar(store: Path, agente: str, *, vence_en_segundos: float) -> None:
    """Siembra el par que el oráculo lee: `<AG>.token` y `<AG>.token.meta.json`.

    El contrato NO lo inventé: `expires_at` es un ISO-8601 que el oráculo pasa
    por `datetime.fromisoformat`. Un epoch numérico lo manda a ILEGIBLE.
    """
    store.mkdir(parents=True, exist_ok=True)
    (store / f"{agente}.token").write_text("x" * 64, encoding="utf-8")
    vence = datetime.now(timezone.utc) + timedelta(seconds=vence_en_segundos)
    (store / f"{agente}.token.meta.json").write_text(
        json.dumps({"expires_at": vence.isoformat()}),
        encoding="utf-8",
    )


def test_el_oraculo_existe_y_es_ejecutable():
    assert ORACULO.is_file(), f"falta el sujeto: {ORACULO}"


def test_self_test_del_oraculo_pasa_sus_cuatro_casos():
    """El self-test propio, corrido desde afuera. No reemplaza a los de abajo."""
    r = subprocess.run(
        ["bash", str(ORACULO), "--self-test"],
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "fallos=0" in r.stdout, r.stdout


def test_verde_ninguno_en_ventana_se_abstiene(tmp_path):
    """CONTROL VERDE: credenciales nuevas -> el oráculo NO debe pedir rotación."""
    store = tmp_path / "seal"
    _sembrar(store, "NEXUS", vence_en_segundos=20 * DIA)
    r = _correr(store)
    assert r.returncode == 0, f"esperaba abstención, dio rc={r.returncode}: {r.stdout}"


def test_rojo_uno_en_ventana_pide_rotar(tmp_path):
    """CONTROL ROJO: dentro de la ventana de 72 h -> debe pedir rotación."""
    store = tmp_path / "seal"
    _sembrar(store, "NEXUS", vence_en_segundos=12 * HORA)
    r = _correr(store)
    assert r.returncode == 2, f"esperaba EN_VENTANA (2), dio rc={r.returncode}: {r.stdout}"


def test_rojo_vencido_sin_rotar_es_falla(tmp_path):
    """CONTROL ROJO: vencido y nadie rotó -> falla, no abstención."""
    store = tmp_path / "seal"
    _sembrar(store, "NEXUS", vence_en_segundos=-1 * DIA)
    r = _correr(store)
    assert r.returncode == 1, f"esperaba FALLA (1), dio rc={r.returncode}: {r.stdout}"


def test_meta_ilegible_no_se_confunde_con_sano(tmp_path):
    """Una meta corrupta debe dar ILEGIBLE, nunca el verde de la abstención.

    Es la distinción que el oráculo existe para hacer: «no le tocaba» y «no
    pude medir» dan el mismo silencio si se funden en un solo código.
    """
    store = tmp_path / "seal"
    store.mkdir(parents=True, exist_ok=True)
    (store / "NEXUS.token").write_text("x" * 64, encoding="utf-8")
    (store / "NEXUS.token.meta.json").write_text("{ esto no es json", encoding="utf-8")
    r = _correr(store)
    assert r.returncode == 3, f"esperaba ILEGIBLE (3), dio rc={r.returncode}: {r.stdout}"


def test_los_cuatro_estados_son_distinguibles(tmp_path):
    """El control que hace útil a los anteriores: los 4 rc son DISTINTOS.

    Sin esto, cuatro asserts podrían pasar contra un oráculo que devuelve
    siempre el mismo valor por casualidad de las fixtures.
    """
    vistos = []

    verde = tmp_path / "verde"
    _sembrar(verde, "NEXUS", vence_en_segundos=20 * DIA)
    vistos.append(_correr(verde).returncode)

    ventana = tmp_path / "ventana"
    _sembrar(ventana, "NEXUS", vence_en_segundos=12 * HORA)
    vistos.append(_correr(ventana).returncode)

    vencido = tmp_path / "vencido"
    _sembrar(vencido, "NEXUS", vence_en_segundos=-1 * DIA)
    vistos.append(_correr(vencido).returncode)

    ilegible = tmp_path / "ilegible"
    ilegible.mkdir(parents=True, exist_ok=True)
    (ilegible / "NEXUS.token").write_text("x" * 64, encoding="utf-8")
    (ilegible / "NEXUS.token.meta.json").write_text("{ roto", encoding="utf-8")
    vistos.append(_correr(ilegible).returncode)

    assert len(set(vistos)) == 4, f"el oráculo no discrimina: rc observados {vistos}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
