"""La guarda de concurrencia y cadencia del hook de captura de sesion.

POR QUE EXISTE: tres emergencias termicas el 8-sep-2026 (SoC 90-93 C) por el
mismo ciclo. Cada turno de cada agente dispara `session_capture_hook.sh`, que
lee el transcript ENTERO, llama al modelo para clasificar emocion y ademas corre
`consolidate.py --quick` (su propio comentario dice "~2-3 min"). Se midieron
TRES capturas simultaneas; nada impedia diez. Cuanto mas hablaba el equipo, mas
calor hacia.

COMO SE OBSERVA SIN CORRER EL TRABAJO PESADO: la guarda deja una marca
(`session_capture.last.<AGENTE>`) SOLO cuando decide seguir. Con un HOME
temporal y sin transcripts, el hook sale enseguida despues de la guarda, asi
que la marca es un observador limpio de la DECISION. No se simula el hook: se
ejecuta el real.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess

import pytest

HOOK = pathlib.Path(__file__).resolve().parents[1] / "session_capture_hook.sh"
EVENTO = json.dumps({"cwd": "/home/dadito/IA/proyecto-seal"})   # -> AGENT=ADA


def corre(home: pathlib.Path, intervalo: str = "180") -> bool:
    """True si la guarda dejo seguir (escribio su marca)."""
    marca = home / ".local/state/seal/session_capture.last.ADA"
    antes = marca.stat().st_mtime_ns if marca.exists() else None
    env = dict(os.environ, HOME=str(home), SEAL_CAPTURE_MIN_INTERVAL=intervalo)
    subprocess.run(["bash", str(HOOK)], input=EVENTO, text=True,
                   capture_output=True, timeout=60, env=env)
    if not marca.exists():
        return False
    return antes is None or marca.stat().st_mtime_ns != antes


@pytest.fixture()
def home(tmp_path):
    (tmp_path / ".local/state/seal").mkdir(parents=True)
    return tmp_path


# ───────────────────────── qa_positive: deja pasar ─────────────────────────

def test_qa_positive_la_primera_corrida_pasa(home):
    assert corre(home) is True


def test_qa_positive_pasada_la_cadencia_vuelve_a_pasar(home):
    assert corre(home) is True
    assert corre(home, intervalo="0") is True


# ───────────────────────── qa_negative: frena ─────────────────────────

def test_qa_negative_una_segunda_corrida_INMEDIATA_se_saltea(home):
    """El caso que genera el calor: un turno detras de otro."""
    assert corre(home) is True
    assert corre(home) is False


def test_qa_negative_con_el_candado_TOMADO_por_otro_se_saltea(home):
    """Concurrencia real: se sostiene el candado desde fuera. Determinista, no
    depende de que dos procesos se pisen por tiempo.

    (Mi primera version de esta prueba lanzaba dos hooks a la vez con un
    trabajo instantaneo y daba «2 de 2 pasaron»: el primero soltaba el candado
    antes de que el segundo lo pidiera. Verde por la razon equivocada.)
    """
    import fcntl
    lock = home / ".local/state/seal/session_capture.lock"
    with open(lock, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert corre(home, intervalo="0") is False
    assert corre(home, intervalo="0") is True, "al soltarlo debe volver a pasar"


# ───────── qa_control: ante la duda, CAPTURAR (no perder memoria) ─────────

def test_qa_control_una_marca_CORRUPTA_no_bloquea(home):
    """Si el estado es ilegible, la guarda debe dejar pasar: perder una captura
    por un archivo roto seria perder memoria por un detalle de plomeria."""
    marca = home / ".local/state/seal/session_capture.last.ADA"
    marca.write_text("no es un numero")
    assert corre(home) is True


def test_qa_control_la_cadencia_es_POR_AGENTE_no_global(home):
    """Dos agentes distintos no deben bloquearse entre si por cadencia: el
    candado limita la concurrencia, la cadencia limita la frecuencia de cada uno.
    """
    assert corre(home) is True                      # ADA marca
    otro = home / ".local/state/seal/session_capture.last.JARVIS"
    assert not otro.exists(), "la marca de ADA no debe contar como la de JARVIS"


def test_qa_control_el_candado_y_la_marca_viven_fuera_del_repo(home):
    """El estado va a ~/.local/state/seal: si viviera en el repo, cada corrida
    ensuciaria el arbol de trabajo y el indice compartido."""
    fuente = HOOK.read_text(encoding="utf-8")
    assert 'LOCK="$HOME/.local/state/seal' in fuente
    assert "proyecto-seal" not in fuente.split("LOCK=")[1].split("\n")[0]
