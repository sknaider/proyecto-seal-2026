#!/usr/bin/env python3
"""Brazos de `merge_redundant`: ventana ACOTADA, fusión idempotente y dry_run que no escribe.

RECONSTRUIDO el 9-sep-2026 por JARVIS. El archivo original se firmó el 5-sep y **nunca entró
al índice de git**, así que se perdió con el home el 7-sep y el manifiesto quedó `REJECTED`
con `missing_test`. Se rehízo desde `quality/manifests/consolidate-dedup-bounded-20260905.json`:
sus `commands` nombran **una por una las ocho pruebas** que el archivo debía tener, así que el
contrato se recuperó entero aunque el código de prueba no. Los nombres son los del manifiesto,
no inventados — si no coinciden, los comandos no corren.

Qué protege (memory/consolidate.py:150-222): la versión anterior hacía
`memories a JOIN memories b` sobre 84.272 memorias y el timer del 5-sep murió en ese paso por
`TimeoutStartSec=600`. La versión acotada exige una ventana >0 y **falla cerrado**.

Los brazos corren con un pool FALSO: no tocan la DB y valen en cualquier máquina. El matiz que
decide en la ventana es «SIN CONSULTAR» — el `ValueError` tiene que saltar ANTES de tocar el
pool. Un mutante que mueva el chequeo detrás de la consulta seguiría lanzando la excepción,
pero ya habría disparado el JOIN que tumbó el timer. Por eso el brazo no mira sólo la
excepción: mira que el pool quede INTACTO.

LÍMITE DECLARADO: los brazos de forma de la consulta NO prueban que el planner elija el índice
HNSW —eso pedía un EXPLAIN contra la DB viva—. Prueban que la consulta conserva las piezas de
las que ese plan depende.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

MEMORY_DIR = Path(__file__).resolve().parents[1]
if str(MEMORY_DIR) not in sys.path:
    sys.path.insert(0, str(MEMORY_DIR))

from consolidate import merge_redundant  # noqa: E402


# --------------------------------------------------------------------------- dobles de prueba

class PoolEspia:
    """Pool falso que ANOTA si alguien intentó usarlo y explota si lo hacen.

    No es un mock permisivo: distingue «lanzó sin consultar» de «lanzó después de consultar»,
    que es exactamente la diferencia que el manifiesto pide probar.
    """

    def __init__(self) -> None:
        self.intentos = 0

    def acquire(self):
        self.intentos += 1
        raise AssertionError(
            "merge_redundant ADQUIRIO el pool con una ventana que no acota. "
            "El ValueError debe saltar ANTES de consultar: el JOIN sin cota es "
            "justo lo que mato al timer del 5-sep por TimeoutStartSec=600."
        )


class ConnFalsa:
    """Conexión que devuelve pares fijos y REGISTRA cada consulta y cada UPDATE."""

    def __init__(self, pares):
        self._pares = pares
        self.sql_recibido: list[str] = []
        self.args: tuple = ()
        self.invalidadas: list[int] = []

    async def fetch(self, sql, *args):
        self.sql_recibido.append(sql)
        self.args = args
        return self._pares

    async def execute(self, sql, *args):
        self.sql_recibido.append(sql)
        if "invalid_at" in sql and args:
            self.invalidadas.append(args[0])


class PoolFalso:
    def __init__(self, pares):
        self.conn = ConnFalsa(pares)

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool.conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def par(id_a, id_b, imp_a=5, imp_b=1, agent="JARVIS", category="tecnico", sim=0.95):
    return {"id_a": id_a, "id_b": id_b, "content_a": "a", "content_b": "b",
            "imp_a": imp_a, "imp_b": imp_b, "agent": agent, "category": category,
            "similarity": sim}


def _sql_de_una_corrida(pares=()):
    pool = PoolFalso(list(pares))
    asyncio.run(merge_redundant(pool, dry_run=True, candidate_hours=24))
    return pool.conn


# ------------------------------------------------ qa_positive: la consulta es acotada y lateral

def test_la_ventana_viaja_como_parametro_y_acota_por_created_at():
    """La cota temporal es lo que impide el escaneo completo, y viaja como PARAMETRO.

    Si la ventana se interpolara en el texto o dejara de aplicarse sobre `created_at`, la
    consulta volvería a mirar las 84.272 memorias: es el escenario exacto que mató al timer.
    """
    conn = _sql_de_una_corrida()
    sql = conn.sql_recibido[0]

    assert "a.created_at >" in sql, (
        "la cota ya no filtra por created_at: sin eso la ventana no acota nada"
    )
    assert "INTERVAL '1 hour'" in sql and "$1" in sql, (
        "la ventana dejo de aplicarse por parametro"
    )
    assert conn.args == (24,), (
        f"la ventana no llego a la consulta como parametro; llego {conn.args!r}"
    )


def test_la_shortlist_es_lateral_por_hnsw_y_no_un_join_cuadratico():
    """El `JOIN LATERAL` con `LIMIT 64` es lo que hace barato el plan.

    Sin la shortlist vuelve el producto contra toda la tabla. Este brazo mata al mutante que
    la borra. NO prueba que el planner use HNSW —eso pedia EXPLAIN contra la DB viva—; prueba
    que la consulta conserva la forma de la que ese plan depende.
    """
    sql = _sql_de_una_corrida().sql_recibido[0]

    assert "JOIN LATERAL" in sql, (
        "desaparecio el JOIN LATERAL: la busqueda dejo de ser 'vecino mas cercano por fila'"
    )
    assert "LIMIT 64" in sql, (
        "desaparecio la shortlist de 64: vuelve el JOIN completo sobre todas las memorias, "
        "que es el que murio a los 600 s"
    )
    assert "<=>" in sql, "sin el operador de distancia no hay busqueda vectorial"


def test_el_candidato_es_del_mismo_agente_y_categoria():
    """El filtro por agente y categoria vive en el LATERAL, no en Python.

    Es lo que impide fusionar un recuerdo de JARVIS con uno de ADA por parecerse.
    """
    sql = _sql_de_una_corrida().sql_recibido[0]

    assert "candidate.agent = a.agent" in sql, (
        "desaparecio el filtro por AGENTE: se podrian fusionar memorias de agentes distintos"
    )
    assert "candidate.category = a.category" in sql, "desaparecio el filtro por CATEGORIA"
    assert "0.90" in sql, "desaparecio el umbral de similitud: fusionaria pares poco parecidos"


# ------------------------------------------- qa_negative: falla cerrado y no repite invalidacion

@pytest.mark.parametrize("ventana", [0, 0.0, -1, -24, None])
def test_una_ventana_que_no_acota_falla_cerrado_sin_consultar(ventana):
    """Fail-closed: 0, negativa o None => ValueError nombrando candidate_hours, SIN consultar."""
    pool = PoolEspia()

    with pytest.raises(ValueError) as exc:
        asyncio.run(merge_redundant(pool, dry_run=True, candidate_hours=ventana))

    assert "candidate_hours" in str(exc.value), (
        "el error debe NOMBRAR el parametro para que quien lo lea sepa que arreglar;\n"
        f"  recibido: {exc.value!s}"
    )
    assert pool.intentos == 0, (
        "el ValueError salto DESPUES de tocar el pool: la guarda esta en el lugar "
        f"equivocado (intentos de acquire={pool.intentos})"
    )


def test_una_ventana_VALIDA_si_llega_a_consultar():
    """CONTROL NO VACUO — sin esto, un mutante que lance SIEMPRE pasaria el brazo de arriba.

    Medido en arena el 9-sep: el mutante `guarda-siempre-lanza` muere por ESTE brazo y por
    ningun otro. No es decoracion: es el unico que distingue 'rechaza lo malo' de 'rechaza todo'.
    """
    pool = PoolEspia()

    with pytest.raises(AssertionError) as exc:
        asyncio.run(merge_redundant(pool, dry_run=True, candidate_hours=24))

    assert "ADQUIRIO el pool" in str(exc.value), (
        f"se esperaba que la ventana valida (24 h) llegara a consultar; fallo con: {exc.value!s}"
    )
    assert pool.intentos == 1, (
        f"una ventana valida debe consultar exactamente una vez; intentos={pool.intentos}. "
        "Si es 0, la guarda esta rechazando el caso legitimo."
    )


def test_no_invalida_dos_veces_la_misma_memoria_en_una_corrida():
    """Con la shortlist lateral el par (a,b) puede volver como (b,a).

    Sin el conjunto `tocadas`, la misma memoria se invalidaria dos veces en una sola corrida.
    Se le dan a proposito los dos sentidos del mismo par, mas un tercero que reusa una id ya
    tocada. Solo debe invalidarse UNA vez.
    """
    pares = [par(1, 2, imp_a=9, imp_b=1),   # invalida la 2
             par(2, 1, imp_a=1, imp_b=9),   # el mismo par al reves: ya tocado
             par(2, 3, imp_a=1, imp_b=9)]   # reusa la 2, que ya fue invalidada
    pool = PoolFalso(pares)

    acciones = asyncio.run(merge_redundant(pool, dry_run=False, candidate_hours=24))

    inv = pool.conn.invalidadas
    assert len(inv) == len(set(inv)), f"se invalido la misma memoria mas de una vez: {inv}"
    assert inv == [2], (
        f"se esperaba invalidar solo la #2; se invalido {inv}. Si aparecen mas, el conjunto "
        "`tocadas` dejo de filtrar los pares repetidos."
    )
    assert len(acciones) == 1, f"una sola fusion deberia producir una sola accion; hubo {acciones}"


# ------------------------------------------------ qa_control: conserva la mas importante

def test_conserva_la_mas_importante_e_invalida_la_otra():
    """La fusion NO puede perder la memoria mas valiosa. Se prueba en los DOS sentidos.

    Un solo sentido dejaria vivo al mutante que invierte la comparacion: con `imp_a > imp_b`
    acertaria por casualidad en la mitad de los casos.
    """
    pool = PoolFalso([par(10, 20, imp_a=9, imp_b=2)])
    asyncio.run(merge_redundant(pool, dry_run=False, candidate_hours=24))
    assert pool.conn.invalidadas == [20], (
        f"con imp_a=9 > imp_b=2 debia invalidar la #20 y conservar la #10; invalido "
        f"{pool.conn.invalidadas}"
    )

    pool = PoolFalso([par(10, 20, imp_a=2, imp_b=9)])
    asyncio.run(merge_redundant(pool, dry_run=False, candidate_hours=24))
    assert pool.conn.invalidadas == [10], (
        f"con imp_b=9 > imp_a=2 debia invalidar la #10 y conservar la #20; invalido "
        f"{pool.conn.invalidadas}. Si conserva siempre la 'a', la comparacion se rompio."
    )


def test_a_igual_importancia_conserva_a_e_invalida_b():
    """CASO FRONTERA — `imp_a >= imp_b` decide el empate a favor de `a`.

    Es el unico brazo que distingue `>=` de `>`. Con `>` el empate se resolveria al reves y
    ningun otro test lo notaria: los dos casos desiguales seguirian pasando.
    """
    pool = PoolFalso([par(10, 20, imp_a=5, imp_b=5)])
    asyncio.run(merge_redundant(pool, dry_run=False, candidate_hours=24))

    assert pool.conn.invalidadas == [20], (
        f"a igual importancia debe conservarse 'a' (#10) e invalidarse 'b' (#20); se invalido "
        f"{pool.conn.invalidadas}. Si invalido la #10, la comparacion paso de >= a >."
    )


def test_dry_run_no_escribe():
    """En dry_run se reportan las fusiones pero NO se toca ni una fila.

    Es el brazo que hace seguro correr esto contra la DB viva para medir. Si un mutante
    borrara el `continue` del dry_run, aca apareceria un UPDATE.
    """
    pool = PoolFalso([par(1, 2), par(3, 4)])

    acciones = asyncio.run(merge_redundant(pool, dry_run=True, candidate_hours=24))

    assert pool.conn.invalidadas == [], (
        f"dry_run ESCRIBIO: invalido {pool.conn.invalidadas}. Es lo que jamas debe pasar."
    )
    assert not any("UPDATE" in s for s in pool.conn.sql_recibido), (
        f"dry_run mando un UPDATE a la DB: {pool.conn.sql_recibido}"
    )
    assert len(acciones) == 2 and all("[DRY RUN]" in a for a in acciones), (
        f"dry_run debe REPORTAR las 2 fusiones que haria, marcadas; devolvio {acciones}"
    )
