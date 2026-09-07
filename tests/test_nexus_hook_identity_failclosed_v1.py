"""Una identidad declarada y desconocida NO escribe el journal de nadie.

POR QUE (NEXUS 3-sep-2026, decision de JARVIS como operador)

El asiento sombra exporta `SEAL_AGENT=ALICE-V2`. No esta en el roster de cinco,
asi que `detect_agent()` caia a un substring del cmdline del padre y despues a
adivinar por cwd. Medido antes del fix:

    SEAL_AGENT=ALICE-V2  -> JARVIS
    SEAL_AGENT=SOMBRA-X  -> JARVIS      <- ni siquiera era "se parece a ALICE"

**Una identidad nueva no quedaba sin registrar: quedaba registrada como OTRO.**
Eso no es fail-closed, es fail-OPEN con nombre ajeno. Que un asiento en
evaluacion pierda su journal es recuperable; que ensucie el de ALICE, no.

EL BRAZO QUE PROTEGE A LOS CINCO es el de `SEAL_AGENT` vacio: esa rama historica
NO se toca, y si alguien la rompe, los cinco asientos dejan de escribir. Vale
mas que el brazo del defecto.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import subprocess
import sys

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[1]
MEMORIA = RAIZ / "memory"


@pytest.fixture(scope="module")
def hook_utils():
    spec = importlib.util.spec_from_file_location("hook_utils_bajo_prueba", MEMORIA / "hook_utils.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# --- unit ----------------------------------------------------------------

def test_unit_el_roster_es_el_de_los_SEIS_con_asiento(hook_utils):
    """FABLE entro el 3-sep: mi fail-closed lo dejaba sin journal porque el
    roster tenia cinco. **Un fail-closed sobre una lista desactualizada apaga a
    quien falte en ella**, y el que falta no se entera: sale rc 0, en silencio."""
    assert hook_utils.KNOWN_AGENTS == {"ADA", "JARVIS", "ALICE", "DUM", "NEXUS", "FABLE"}


# --- qa_positive: los cinco NO cambian -----------------------------------

@pytest.mark.parametrize("agente", ["ADA", "JARVIS", "ALICE", "DUM", "NEXUS", "FABLE"])
def test_qa_positive_una_identidad_del_roster_se_respeta(hook_utils, monkeypatch, agente):
    monkeypatch.setenv("SEAL_AGENT", agente)
    assert hook_utils.detect_agent() == agente


def test_qa_positive_sin_SEAL_AGENT_la_rama_historica_sigue_viva(hook_utils, monkeypatch):
    """Si esto devolviera None, los CINCO dejarian de escribir su journal."""
    monkeypatch.delenv("SEAL_AGENT", raising=False)
    resultado = hook_utils.detect_agent()
    assert resultado in hook_utils.KNOWN_AGENTS


def test_qa_positive_mayusculas_y_espacios_no_expulsan_del_roster(hook_utils, monkeypatch):
    monkeypatch.setenv("SEAL_AGENT", "  nexus  ")
    assert hook_utils.detect_agent() == "NEXUS"


# --- qa_negative: la identidad declarada y desconocida ---------------------

@pytest.mark.parametrize("declarada", ["ALICE-V2", "alice-v2", "SOMBRA-X", "ALICE_V2", "ALICEV2"])
def test_qa_negative_una_identidad_desconocida_devuelve_None(hook_utils, monkeypatch, declarada):
    """`ALICEV2` y `ALICE_V2` importan: la rama vieja los habria mandado a ALICE
    por substring. Ninguna variante puede heredar el nombre de otro."""
    monkeypatch.setenv("SEAL_AGENT", declarada)
    assert hook_utils.detect_agent() is None


def test_qa_negative_el_hook_de_working_state_no_escribe_y_lo_dice(tmp_path):
    """Por subproceso, que es como corre de verdad: el contrato es stdout con
    hook_output VACIO y el motivo en stderr."""
    # HEREDA el entorno real y solo pisa lo necesario: con un `env` minimo el
    # hook moria antes de llegar al guard --stdout vacio-- y el test medía esa
    # muerte, no el fail-closed. Un arnes que mata al sujeto antes del punto de
    # medicion da un rojo que se lee como hallazgo.
    env = {**os.environ, "SEAL_WORKING_STATE_HOOK": "1", "SEAL_AGENT": "ALICE-V2"}
    p = subprocess.run([sys.executable, "working_state_hook.py"], input="{}",
                       capture_output=True, text=True, cwd=MEMORIA, env=env, timeout=60)
    assert p.stdout.strip() == "{}"
    assert "fuera del roster" in p.stderr
    assert p.returncode == 0


# --- qa_control ----------------------------------------------------------

def test_control_no_vacuo_el_test_puede_fallar(hook_utils, monkeypatch):
    monkeypatch.setenv("SEAL_AGENT", "NEXUS")
    with pytest.raises(AssertionError):
        assert hook_utils.detect_agent() is None
