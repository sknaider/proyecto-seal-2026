"""Contrato del WRITER de latido de ALICE: messages/alice_heartbeat_update.sh.

POR QUE EXISTE ESTA SUITE, y no es una formalidad: el 3-sep-2026 estuve 6,4 h
MUDA (06:01-12:36) con el proceso vivo, el latido fresco y el bridge activo,
mientras mi barra de tmux decia "100% context used". El latido publicaba
`alive:true` y era CIERTO. Nadie mintio: `alive` simplemente no distingue un
asiento sano de uno saturado, y por eso William tuvo que preguntar "Alice sigues
viva?" en vez de verlo en un panel.

QUE FIJA ESTA SUITE: que `context_percent` diga el numero REAL del medidor, y
-mas importante- que el medidor no pueda TUMBAR el latido. Le estoy agregando una
dependencia externa a un VIGILANTE: si el instrumento se cae y se lleva puesto al
que vigila, el remedio es peor que la enfermedad.

COSTURA DE SUJETO INTERCAMBIABLE (ALICE_WRITER_PATH). Sin esto la suite resuelve
su sujeto por SU PROPIA ubicacion: un revisor copia el sujeto a /tmp, apunta el
override, y la suite sigue midiendo PRODUCCION -- su mutante queda inerte y el
veredicto es falso. NO reasignar WRITER despues de leer el entorno: ese fue el
defecto PREEXISTENTE que JARVIS encontro en su propia suite al hacer este mismo
cambio (sus mutantes eran inertes y nadie lo sabia).

    ALICE_WRITER_PATH=/tmp/arbol/messages/alice_heartbeat_update.sh \
    python3 -m pytest agents/ALICE/tests/test_alice_heartbeat_writer_v1.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]  # tests -> ALICE -> agents -> raiz
WRITER = Path(os.environ.get("ALICE_WRITER_PATH", str(ROOT / "messages" / "alice_heartbeat_update.sh")))


def _home_sintetico(tmp_path: Path) -> Path:
    """HOME replicado. El writer resuelve MESSAGES_DIR y MEMORY_DIR por $HOME,
    asi que apuntarlo aca es lo que impide que un caso de prueba escriba sobre el
    latido REAL. La contencion es del arnes, no del sujeto."""
    msgs = tmp_path / "IA" / "proyecto-seal" / "messages"
    msgs.mkdir(parents=True, exist_ok=True)
    (tmp_path / "IA" / "proyecto-seal" / "memory").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _proc_vacio(tmp_path: Path) -> Path:
    """/proc sintetico SIN asientos: el latido no debe depender de que haya una
    ALICE viva en la maquina que corre los tests."""
    p = tmp_path / "proc"
    p.mkdir(exist_ok=True)
    return p


def _correr(tmp_path: Path, medidor: Path | None, timeout: int = 90):
    home = _home_sintetico(tmp_path)
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["PROC_ROOT"] = str(_proc_vacio(tmp_path))
    env["ALICE_CONTEXT_METER"] = str(medidor) if medidor else str(tmp_path / "no_existe.py")
    ini = time.monotonic()
    cp = subprocess.run(
        ["bash", str(WRITER)],
        check=False, capture_output=True, text=True, timeout=timeout, env=env,
    )
    dur = time.monotonic() - ini
    hb = home / "IA" / "proyecto-seal" / "messages" / "alice_claude_heartbeat.json"
    datos = json.loads(hb.read_text(encoding="utf-8")) if hb.exists() else None
    return cp, datos, dur


def _stub(tmp_path: Path, nombre: str, cuerpo: str) -> Path:
    p = tmp_path / nombre
    p.write_text(cuerpo, encoding="utf-8")
    return p


def test_bash_is_available():
    """Control: si esto falla, ningun otro resultado de esta suite significa nada."""
    assert shutil.which("bash"), "sin bash la suite no mide el sujeto, mide su ausencia"


def test_context_percent_es_el_numero_del_medidor(tmp_path):
    """Exige el numero EXACTO. Un test que solo pidiera 'es un entero' pasaria
    con un writer que publica cualquier cosa."""
    m = _stub(tmp_path, "ok.py", 'print("⚕ ALICE │ 400K/950K auto │ [####------] 42% │ 3s")\n')
    cp, d, _ = _correr(tmp_path, m)
    assert cp.returncode == 0
    assert d["context_percent"] == 42, d
    assert d["context_source"] == "seal_context_meter", d


def test_el_100_por_ciento_se_publica(tmp_path):
    """EL CASO QUE ME DEJO MUDA. Si el writer publicara null justo en el 100 %,
    todo lo demas seria decorativo: no cubriria el unico incidente que lo motivo."""
    m = _stub(tmp_path, "lleno.py", 'print("⚕ ALICE │ 950K/950K auto │ [##########] 100% │ 1s")\n')
    _, d, _ = _correr(tmp_path, m)
    assert d["context_percent"] == 100, d
    assert d["context_source"] == "seal_context_meter", d


@pytest.mark.parametrize(
    "nombre,cuerpo",
    [
        ("roto.py", "import sys; sys.exit(3)\n"),
        ("basura.py", 'print("sin porcentaje aqui")\n'),
        ("rango.py", 'print("ctx 999% listo")\n'),
        ("vacio.py", "pass\n"),
        (None, None),  # medidor AUSENTE
    ],
    ids=["rc_no_cero", "sin_porcentaje", "fuera_de_rango", "sin_salida", "ausente"],
)
def test_medidor_roto_da_null_y_no_tumba_el_latido(tmp_path, nombre, cuerpo):
    """LA RAZON DE SER DEL FAIL-SAFE. Le agrego una dependencia a un vigilante:
    cuando esa dependencia se cae, el vigilante tiene que SEGUIR VIGILANDO.
    `alive` y el rc son lo que no puede moverse."""
    m = _stub(tmp_path, nombre, cuerpo) if nombre else None
    cp, d, _ = _correr(tmp_path, m)
    assert cp.returncode == 0, cp.stderr
    assert d["context_percent"] is None, d
    assert d["context_source"] == "unavailable", d
    assert "alive" in d and isinstance(d["alive"], bool), d


def test_medidor_colgado_no_cuelga_el_latido(tmp_path):
    """Un medidor que NO responde es distinto de uno que falla: no da error, se
    queda callado para siempre. Sin `timeout` el latido dejaria de latir -- y un
    latido detenido se lee como agente MUERTO, que es peor que no tener la metrica."""
    m = _stub(tmp_path, "colgado.py", "import time; time.sleep(120)\n")
    cp, d, dur = _correr(tmp_path, m, timeout=90)
    assert cp.returncode == 0
    assert d["context_percent"] is None, d
    assert d["context_source"] == "unavailable", d
    assert dur < 60, f"el medidor colgado retuvo el latido {dur:.1f}s"


def test_el_campo_llega_a_los_tres_destinos(tmp_path):
    """Un fix que no llega al CONSUMIDOR no existe. El JSON no es el unico sink:
    el guard lee el event_log y el humano lee el mensaje del latido."""
    fuente = WRITER.read_text(encoding="utf-8")
    assert "CONTEXT_PCT" in fuente
    assert fuente.count("${CONTEXT_PCT}") >= 3, (
        "context_percent tiene que ir al JSON, al HB_MSG y al beat_sync del event_log"
    )


def test_apuntar_a_home_sintetico_no_puede_tocar_produccion(tmp_path):
    """CONTENCION. La suite corre el writer de verdad; si no estuviera contenida,
    cada caso de medidor roto escribiria `null` sobre MI latido real y el
    Stability Guard me veria degradada por culpa de un test.

    NO se compara byte a byte contra produccion. La primera version de este test
    hacia exactamente eso y dio un FALSO POSITIVO en su estreno: el
    `seal-alice-heartbeat.timer` corre cada 5 min POR DISENO y reescribio el
    archivo a mitad de la corrida (12:55:01). El test acusaba a la suite de pisar
    produccion cuando el que habia escrito era el timer, con un latido legitimo.

    La asercion correcta es por FIRMA, no por igualdad: la corrida sintetica es
    reconocible -- /proc vacio da alive=false y pid "0", y el stub da un
    porcentaje que yo elijo. Si esa terna aparece en produccion, la contencion
    fallo de verdad. Un latido real del timer nunca la produce."""
    real = ROOT / "messages" / "alice_claude_heartbeat.json"
    m = _stub(tmp_path, "ok2.py", 'print("[###-------] 33%")\n')
    _, d, _ = _correr(tmp_path, m)
    # la corrida sintetica produjo SU firma en el arbol temporal
    assert (d["context_percent"], d["process_pid"], d["alive"]) == (33, "0", False), d
    if real.exists():
        prod = json.loads(real.read_text(encoding="utf-8"))
        firma = (prod.get("context_percent"), prod.get("process_pid"), prod.get("alive"))
        assert firma != (33, "0", False), "la suite piso el latido de produccion"
