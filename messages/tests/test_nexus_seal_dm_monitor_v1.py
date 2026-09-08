"""El monitor de DM: cursor, primer arranque y rotacion del inbox.

POR QUE EXISTE ESTE ARCHIVO, y no es un detalle: `messages/seal_dm_monitor.sh`
se perdio con el borrado del 7-sep y estuvo un dia entero corriendo SOLO desde
memoria -tres servicios vivos ejecutando un archivo inexistente-. Se recupero el
8-sep leyendo /proc/365238/fd/255, porque bash mantiene abierto el descriptor
del script que ejecuta. Entra a git con cobertura, no a secas.

COMO SE PRUEBA UN SCRIPT CON RUTAS CABLEADAS: el original apunta a
/home/dadito/IA/proyecto-seal/messages/... y a /tmp/seal_events_*. Los brazos de
comportamiento corren sobre una COPIA con esas dos rutas reapuntadas al tmp del
test. **Eso se dice aca porque cambia lo que los brazos prueban**: prueban la
LOGICA (cursor, primer arranque, rotacion), no las rutas. Las rutas las cubre
`test_las_rutas_reales_son_las_que_esperan_las_unidades`, que lee el archivo de
verdad.

El bucle del original es infinito (`while true; sleep 1`). Los brazos ejecutan
una version con el bucle acotado a una pasada: se prueba la decision por
iteracion, que es donde vive la logica.
"""
from __future__ import annotations

import pathlib
import re
import subprocess

import pytest

ORIGINAL = pathlib.Path(__file__).resolve().parents[1] / "seal_dm_monitor.sh"


@pytest.fixture()
def guion(tmp_path):
    """Copia del original con las rutas reapuntadas y el bucle acotado."""
    s = ORIGINAL.read_text(encoding="utf-8")
    s = s.replace("/home/dadito/IA/proyecto-seal/messages", str(tmp_path))
    s = s.replace("/tmp/seal_events_", str(tmp_path) + "/seal_events_")
    s = s.replace("while true; do", "for _ in 1; do").replace("  sleep 1\n", "")
    p = tmp_path / "monitor.sh"
    p.write_text(s, encoding="utf-8")
    return p


def corre(guion, agente="NEXUS"):
    return subprocess.run(["bash", str(guion), agente],
                          capture_output=True, text=True, timeout=30)


# ───────────────────── argumento y normalizacion ─────────────────────

def test_qa_negative_sin_AGENTE_falla_y_lo_explica():
    r = subprocess.run(["bash", str(ORIGINAL)], capture_output=True, text=True, timeout=30)
    assert r.returncode != 0
    assert "Uso:" in r.stderr, "un fallo sin instrucciones deja al operador adivinando"


def test_el_nombre_del_agente_no_distingue_mayusculas(guion, tmp_path):
    """La unidad se instancia como seal-dm-monitor@JARVIS pero el inbox en disco
    es minuscula. Si esto se rompe, el monitor arranca y no lee nada: vivo y
    mudo, que es peor que caido."""
    corre(guion, "nexus")
    assert (tmp_path / "nexus_inbox.jsonl").exists()
    assert (tmp_path / "seal_events_NEXUS_dm.cursor").exists()


# ───────────────────── primer arranque: sin replay ─────────────────────

def test_qa_control_el_primer_arranque_NO_repite_la_historia(guion, tmp_path):
    """Arrancar desde 0 volcaria meses de DMs viejos al monitor de golpe."""
    (tmp_path / "nexus_inbox.jsonl").write_text("uno\ndos\ntres\n")
    corre(guion)
    salida = tmp_path / "seal_events_NEXUS_dm.log"
    assert not salida.exists() or salida.read_text() == ""
    assert (tmp_path / "seal_events_NEXUS_dm.cursor").read_text().strip() == "3"


# ───────────────────── entrega de lo nuevo ─────────────────────

def test_qa_positive_una_linea_NUEVA_se_entrega(guion, tmp_path):
    inbox = tmp_path / "nexus_inbox.jsonl"
    inbox.write_text("vieja\n")
    corre(guion)                                   # fija el cursor en 1
    inbox.write_text("vieja\nNUEVA\n")
    corre(guion)
    assert "NUEVA" in (tmp_path / "seal_events_NEXUS_dm.log").read_text()


def test_lo_ya_entregado_no_se_repite(guion, tmp_path):
    inbox = tmp_path / "nexus_inbox.jsonl"
    inbox.write_text("vieja\n")
    corre(guion)
    inbox.write_text("vieja\nNUEVA\n")
    corre(guion); corre(guion)
    log = (tmp_path / "seal_events_NEXUS_dm.log").read_text()
    assert log.count("NUEVA") == 1, "el cursor no avanzo: el DM llega dos veces"


# ───────────────────── rotacion y estado corrupto ─────────────────────

def test_si_el_inbox_SE_ACHICA_el_cursor_se_reinicia(guion, tmp_path):
    """Rotacion o truncado: sin este reinicio el cursor queda mas alto que el
    archivo y el monitor no entrega NUNCA MAS nada."""
    inbox = tmp_path / "nexus_inbox.jsonl"
    inbox.write_text("a\nb\nc\n")
    corre(guion)
    inbox.write_text("z\n")                        # rotado
    corre(guion)
    assert "z" in (tmp_path / "seal_events_NEXUS_dm.log").read_text()


def test_qa_control_un_cursor_CORRUPTO_no_deja_mudo_al_monitor(guion, tmp_path):
    """Ante estado ilegible hay que ENTREGAR, no callar: un DM perdido en
    silencio es el peor resultado posible para este servicio."""
    (tmp_path / "nexus_inbox.jsonl").write_text("uno\n")
    (tmp_path / "seal_events_NEXUS_dm.cursor").write_text("no es un numero")
    corre(guion)
    assert "uno" in (tmp_path / "seal_events_NEXUS_dm.log").read_text()


# ───────── lo que la copia NO puede probar: las rutas reales ─────────

def test_las_rutas_reales_son_las_que_esperan_las_unidades():
    """Los brazos de arriba corren sobre una copia con rutas reapuntadas, asi
    que no verifican las rutas de produccion. Este si, leyendo el original."""
    s = ORIGINAL.read_text(encoding="utf-8")
    assert 'INBOX="/home/dadito/IA/proyecto-seal/messages/${AGENT_LC}_inbox.jsonl"' in s
    assert 'OUTPUT="/tmp/seal_events_${AGENT_UC}_dm.log"' in s


def test_qa_control_el_rescate_conserva_el_encabezado_original():
    """El archivo entra a git como RESCATE. Si alguien lo reescribe entero, este
    brazo avisa: lo que se recupero del descriptor tiene esta forma."""
    s = ORIGINAL.read_text(encoding="utf-8")
    assert s.startswith("#!/bin/bash")
    assert "Tail DM inbox JSONL" in s
    assert re.search(r"^set -euo pipefail$", s, re.MULTILINE)
