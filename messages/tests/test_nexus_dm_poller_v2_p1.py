"""El poller de DM v2: formato, cursor y lo que NUNCA debe ir al log.

Es un REEMPLAZO NUEVO, no una restauracion: el original se perdio el 7-sep y
corre solo en memoria. Por eso los brazos verifican dos cosas distintas:

  * COMPATIBILIDAD  — la linea que escribe tiene que ser la que
    `seal_dm_monitor.sh` sabe leer aguas abajo. Si eso cambia, el intercambio
    rompe la cadena entera y no se nota hasta que William escriba.
  * NO REGRESION DE PRIVACIDAD — el hermano `ada_dm_poller.py` imprime el
    CONTENIDO del DM al log del servicio. Ese texto va al journal, que lee
    cualquiera con acceso al diario. Estos brazos lo prohiben por construccion.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import nexus_dm_poller_v2 as p  # noqa: E402

SECRETO = "TEXTO-SINTETICO-DE-UN-DM-NO-DEBE-APARECER-EN-EL-LOG"


def fila(cuando: datetime, texto: str = "hola", quien: str = "William") -> dict:
    return {"sender_name": quien, "content": texto,
            "created_at": cuando, "channel": p.CANAL}


# ───────────────── compatibilidad con el monitor de abajo ─────────────────

def test_la_linea_tiene_las_claves_que_espera_el_monitor():
    d = p.a_linea(fila(datetime(2026, 9, 8, 22, 1, 24, tzinfo=timezone.utc)))
    assert set(d) == {"id", "from", "to", "timestamp", "type", "message", "channel"}
    assert d["to"] == "NEXUS" and d["type"] == "dm"
    assert d["id"].startswith("dm_"), "el monitor identifica los DM por ese prefijo"


def test_qa_positive_escribe_una_linea_JSON_por_DM(tmp_path):
    inbox = tmp_path / "inbox.jsonl"
    n = p.escribe([fila(datetime.now(timezone.utc)), fila(datetime.now(timezone.utc))], inbox)
    assert n == 2
    lineas = inbox.read_text().strip().splitlines()
    assert len(lineas) == 2
    assert all(json.loads(l)["to"] == "NEXUS" for l in lineas)


def test_anexa_sin_pisar_lo_que_ya_estaba(tmp_path):
    """El inbox es un historial: escribir con 'w' borraria los DM anteriores."""
    inbox = tmp_path / "inbox.jsonl"
    inbox.write_text('{"viejo":1}\n')
    p.escribe([fila(datetime.now(timezone.utc))], inbox)
    assert inbox.read_text().startswith('{"viejo":1}')


# ───────────────────────────── el cursor ─────────────────────────────

def test_qa_control_un_cursor_ILEGIBLE_arranca_en_AHORA_no_en_el_principio(tmp_path):
    """Decision opuesta a la del dedup de alertas, y a proposito: alli el riesgo
    es el silencio; aca es la INUNDACION. Un cursor roto que arranque en 1970
    vuelca la historia entera de DMs al inbox."""
    c = tmp_path / "cursor"; c.write_text("no es una fecha")
    ts = p.lee_cursor(c)
    assert (datetime.now(timezone.utc) - ts).total_seconds() < 5


def test_el_cursor_avanza_al_MAXIMO_procesado(tmp_path):
    inbox, cur = tmp_path / "i.jsonl", tmp_path / "c"
    t0 = datetime.now(timezone.utc) - timedelta(minutes=5)
    filas = [fila(t0), fila(t0 + timedelta(minutes=1)), fila(t0 + timedelta(minutes=2))]

    async def buscar(canal, desde):
        return filas
    asyncio.run(p.una_pasada(buscar, inbox, cur))
    assert p.lee_cursor(cur) == filas[-1]["created_at"]


def test_qa_negative_sin_filas_NO_toca_el_cursor(tmp_path):
    """Si avanzara con la busqueda vacia, un hueco de red saltearia DMs reales."""
    cur = tmp_path / "c"; cur.write_text(datetime(2020, 1, 1, tzinfo=timezone.utc).isoformat())
    async def vacia(canal, desde):
        return []
    asyncio.run(p.una_pasada(vacia, tmp_path / "i.jsonl", cur))
    assert p.lee_cursor(cur).year == 2020


# ───────── privacidad: el log NUNCA lleva el contenido del DM ─────────

def test_qa_negative_el_resumen_del_log_NO_lleva_el_texto():
    linea = p.resumen_sin_contenido(fila(datetime.now(timezone.utc), SECRETO))
    assert SECRETO not in linea
    assert "largo=" in linea, "debe decir CUANTO, para poder diagnosticar sin leer"


def test_qa_control_una_pasada_completa_no_filtra_el_texto(tmp_path, capsys):
    async def buscar(canal, desde):
        return [fila(datetime.now(timezone.utc), SECRETO)]
    asyncio.run(p.una_pasada(buscar, tmp_path / "i.jsonl", tmp_path / "c"))
    assert SECRETO not in capsys.readouterr().out


def test_qa_control_el_error_NO_registra_el_mensaje_de_la_excepcion():
    """Una excepcion de asyncpg puede traer la cadena de conexion COMPLETA. Por
    eso el bucle registra el TIPO y nada mas."""
    fuente = pathlib.Path(p.__file__).read_text(encoding="utf-8")
    assert 'print(f"[NEXUS-DM] error: {type(exc).__name__}"' in fuente
    assert "{exc}" not in fuente, "registrar la excepcion entera puede volcar el DSN"


def test_qa_control_no_hay_ninguna_credencial_en_el_archivo():
    """El hermano ada_dm_poller.py lleva el DSN con la contrasena adentro."""
    import re
    fuente = pathlib.Path(p.__file__).read_text(encoding="utf-8")
    assert not re.search(r"://[^\s'\"]*:[^\s'\"]*@", fuente)
    assert "seal_observer_credencial" in fuente, "la credencial se lee de archivo"


def test_el_canal_es_el_del_original_y_no_se_amplio():
    """Un reemplazo REEMPLAZA. Ampliar a todos los dm:nexus:* duplicaria con la
    via del monitor de canal; queda anotado como decision futura, no aplicada."""
    assert p.CANAL == "dm:nexus:william"


def test_qa_control_el_PISO_se_persiste_o_el_poller_no_entrega_NUNCA(tmp_path):
    """Defecto hallado en PRODUCCION el 8-sep, con v2 ya intercambiado.

    `lee_cursor` devolvia `ahora` cuando el archivo faltaba **sin escribirlo**, y
    el cursor solo se guardaba al encontrar filas. Arranque real: no hay DMs
    pendientes -> cero filas -> no se escribe cursor -> la pasada siguiente
    vuelve a leer `ahora`, mas alto. **El piso persigue al reloj y ningun DM lo
    alcanza nunca.** Bloqueo circular; el poller queda vivo y mudo.

    El escenario tiene que empezar SIN filas: si la primera pasada entrega algo,
    el cursor se escribe por la otra via y el brazo no distingue el defecto.
    (Mi primera version de este test hacia justo eso y no discriminaba.)
    """
    cur = tmp_path / "cursor-inexistente"
    inbox = tmp_path / "i.jsonl"
    pisos = []
    hay_dm = {"si": False}

    async def buscar(canal, desde):
        pisos.append(desde)
        if not hay_dm["si"]:
            return []                      # arranque real: no hay nada pendiente
        return [fila(desde + timedelta(seconds=1))]

    asyncio.run(p.una_pasada(buscar, inbox, cur))
    assert cur.exists(), (
        "sin filas, el piso igual debe quedar PERSISTIDO; si no, la proxima "
        "pasada lo vuelve a mover y el poller no entrega nunca")

    hay_dm["si"] = True                    # ahora llega un DM posterior al piso
    assert asyncio.run(p.una_pasada(buscar, inbox, cur)) == 1
    assert pisos[1] == pisos[0], "sin entregas, el piso no puede haberse movido"
