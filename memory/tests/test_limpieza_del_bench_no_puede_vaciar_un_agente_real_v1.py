#!/usr/bin/env python3
"""La limpieza del benchmark no puede vaciar a un agente real — brazos QA.

**Qué lo generó (ADA, 9-sep-2026; lo exigió JARVIS al autorizar identidad propia
para el benchmark).** La limpieza borraba `WHERE agent = $1` sin más. Inocuo mientras
el nombre sea sintético, y por eso nadie lo miró en meses. Pero su radio de explosión
escrito no era «lo que creó esta corrida» sino **todo lo que le pertenece a ese
agente**. La opción que yo misma había propuesto —que el benchmark escribiera como
ADA— habría ejecutado, al final de cada corrida, en el camino feliz y en silencio:

    DELETE FROM memories WHERE agent = 'ADA'    -> toda la memoria, no 55 filas
    DELETE FROM identity WHERE agent = 'ADA'    -> la identidad

Yo lo había estimado como «ensucia mi memoria con ~55 filas». Estaba mal por varios
órdenes de magnitud, y lo vio JARVIS porque le avisé que la limpieza borra filas.

**SEGURIDAD DE ESTOS TESTS — por qué se pueden correr en cualquier lado.**
`exigir_agente_de_bench` es una función PURA (compara cadenas y levanta), el SQL va a
un pool falso que sólo lo anota, y la mitad vectorial se sustituye por una doble. Los
nombres son SEÑUELO: ninguno nombra un agente real, así que ni con la guarda borrada
podría ejecutarse algo contra ADA, JARVIS, ALICE, NEXUS o DUM. Mutar una guarda que
borra no simula el peligro, lo ejecuta (7-sep-2026); ésta decide, no borra.

**Y una corrección, porque la escribí mal la primera vez:** este bloque decía «ninguno
toca la base». Era falso — la limpieza de Qdrant vivía dentro de la misma función y
los tests de control **abrían una conexión real y borraban puntos**. Eran puntos de
agentes de benchmark, así que el daño fue ninguno, pero la afirmación de seguridad no
era cierta. Lo delató un `UserWarning` de `qdrant_client` al final de la corrida, no
una revisión. Por eso ahora hay una doble y un brazo que la exige.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import seal_bench as sb  # noqa: E402


# La función REAL, capturada antes de que el fixture la sustituya. Sin esto, los dos
# brazos que la prueban llamaban a la doble y "pasaban" sin ejercer nada: la doble me
# tapaba el sujeto. Es la misma trampa que este archivo denuncia, un nivel más arriba.
_LIMPIAR_QDRANT_REAL = sb._limpiar_qdrant_de_bench


@pytest.fixture(autouse=True)
def sin_qdrant_de_verdad(monkeypatch):
    """Ningún test de este archivo abre una conexión vectorial real."""
    llamadas = []

    async def doble():
        llamadas.append(True)

    monkeypatch.setattr(sb, "_limpiar_qdrant_de_bench", doble)
    return llamadas

# Nombres SEÑUELO. No existen y no van a existir; si la guarda fallara, un DELETE
# contra ellos no tocaría una sola fila de nadie.
SEÑUELOS = (
    "SENUELO_QUE_NO_ES_BENCH",
    "AGENTE_INVENTADO_PARA_ESTE_TEST",
    "BENCH_ALPHA_EXTRA",   # empieza igual: la lista es cerrada, no un prefijo
    "bench_alpha",         # minúsculas: no es el mismo nombre
    "",
    "   ",
)


class _SettingsAPuertoMuerto:
    """`settings.qdrant_url` es una propiedad sin setter: se reemplaza el objeto.

    Apunta a un puerto muerto para que, aun si el candado fallara, no salga una
    petición real hacia el índice vectorial.
    """

    qdrant_url = "http://127.0.0.1:9"
    qdrant_collection = "coleccion_senuelo_de_test"


class PoolFalso:
    """Anota el SQL en vez de ejecutarlo. Nada de esto llega a PostgreSQL."""

    def __init__(self):
        self.sentencias: list[tuple[str, tuple]] = []

    async def execute(self, sql, *args):
        self.sentencias.append((" ".join(sql.split()), args))

    def deletes(self) -> list[str]:
        return [s for s, _ in self.sentencias if s.upper().startswith("DELETE")]


# ── qa_positive — los agentes de benchmark declarados sí pasan ────────────────

@pytest.mark.parametrize("nombre", sorted(sb._AGENTES_DE_BENCH))
def test_qa_positive_los_agentes_de_bench_pasan(nombre):
    assert sb.exigir_agente_de_bench(nombre) == nombre


def test_qa_positive_los_espacios_alrededor_no_rompen_una_corrida_legitima():
    assert sb.exigir_agente_de_bench("  BENCH_ALPHA  ") == "BENCH_ALPHA"


def test_qa_positive_la_lista_cubre_las_tres_generaciones():
    """v1, v3 y v4 usan nombres distintos; si falta uno, esa corrida no limpia."""
    for esperado in ("BENCH_ALPHA", "BENCH_V3_ALPHA", "BENCH_V4_ALPHA"):
        assert esperado in sb._AGENTES_DE_BENCH


# ── qa_negative — cualquier otro nombre LEVANTA ───────────────────────────────

@pytest.mark.parametrize("senuelo", SEÑUELOS)
def test_qa_negative_un_nombre_que_no_es_de_bench_levanta(senuelo):
    with pytest.raises(sb.LimpiezaFueraDeAlcance):
        sb.exigir_agente_de_bench(senuelo)


def test_qa_negative_none_levanta():
    with pytest.raises(sb.LimpiezaFueraDeAlcance):
        sb.exigir_agente_de_bench(None)


def test_qa_negative_ningun_agente_real_esta_en_la_lista():
    """El chequeo directo del escenario que JARVIS marcó como catastrófico.

    Es seguro: sólo mira la pertenencia a un conjunto, no llama a nadie ni borra nada.
    """
    for real in ("ADA", "JARVIS", "ALICE", "NEXUS", "DUM", "FABLE", "William"):
        assert real not in sb._AGENTES_DE_BENCH


def test_qa_negative_con_un_nombre_señuelo_NO_se_emite_un_solo_DELETE(monkeypatch):
    """La guarda tiene que actuar ANTES del primer borrado, no después del tercero."""
    monkeypatch.setattr(sb, "BENCH_AGENT_A", "SENUELO_QUE_NO_ES_BENCH")
    pool = PoolFalso()
    with pytest.raises(sb.LimpiezaFueraDeAlcance):
        asyncio.run(sb._cleanup_bench_data(pool))
    assert pool.deletes() == [], f"se emitieron borrados igual: {pool.deletes()}"


def test_qa_negative_tampoco_si_el_nombre_malo_es_el_SEGUNDO(monkeypatch):
    """El bucle recorre dos agentes: el primero válido no debe habilitar al segundo.

    Sin este brazo, una guarda puesta sólo antes del bucle pasaría el test anterior.
    """
    monkeypatch.setattr(sb, "BENCH_AGENT_B", "AGENTE_INVENTADO_PARA_ESTE_TEST")
    pool = PoolFalso()
    with pytest.raises(sb.LimpiezaFueraDeAlcance):
        asyncio.run(sb._cleanup_bench_data(pool))
    # El primer agente sí es legítimo, así que sus borrados pueden haber salido;
    # lo que no puede haber salido es uno con el nombre señuelo.
    assert not any("AGENTE_INVENTADO_PARA_ESTE_TEST" in str(a)
                   for _, a in pool.sentencias)


# ── qa_control — la limpieza legítima sigue funcionando y sigue acotada ───────

def test_qa_control_con_agentes_validos_si_borra():
    """Control: si nada borrara, los brazos negativos pasarían por la razón equivocada."""
    pool = PoolFalso()
    asyncio.run(sb._cleanup_bench_data(pool))
    assert pool.deletes(), "no se emitió ningún borrado: el test negativo no probaría nada"


def test_qa_control_la_ventana_acota_las_tablas_con_fecha():
    pool = PoolFalso()
    desde = datetime.now(timezone.utc)
    asyncio.run(sb._cleanup_bench_data(pool, desde=desde))
    con_fecha = [s for s in pool.deletes() if "created_at >= $2" in s]
    assert len(con_fecha) == len(sb._TABLAS_DE_BENCH_CON_FECHA) * 2, (
        "alguna tabla con created_at se sigue borrando entera"
    )


def test_qa_control_sin_ventana_se_conserva_el_comportamiento_anterior():
    pool = PoolFalso()
    sb._INICIO_DE_CORRIDA = None
    asyncio.run(sb._cleanup_bench_data(pool))
    assert not any("created_at" in s for s in pool.deletes())


def test_qa_control_todas_las_tablas_declaradas_se_limpian():
    """Si alguien saca una tabla de la lista, deja filas colgadas en silencio."""
    pool = PoolFalso()
    asyncio.run(sb._cleanup_bench_data(pool))
    juntas = " ".join(pool.deletes())
    for tabla in sb._TABLAS_DE_BENCH_CON_FECHA + ("identity",):
        assert f"FROM {tabla} " in juntas, f"{tabla} dejó de limpiarse"


def test_qa_control_la_limpieza_vectorial_se_sigue_llamando(sin_qdrant_de_verdad):
    """Si nadie la llama, quedan puntos huérfanos del benchmark en el índice.

    Y este brazo es además el que prueba que la doble del fixture está enganchada:
    sin ella, estos tests volverían a abrir una conexión real sin que nadie se entere.
    """
    asyncio.run(sb._cleanup_bench_data(PoolFalso()))
    assert sin_qdrant_de_verdad == [True]


# ── Dos brazos que faltaban, y los delataron los mutantes ────────────────────

def test_qa_control_el_SQL_acotado_es_exactamente_el_esperado():
    """Comprobar que el SQL CONTIENE `created_at >= $2` no prueba que acote.

    Lo delató un mutante: agregándole ` OR TRUE` al final, la condición pasa a ser
    siempre cierta —se borra todo, también lo de corridas anteriores— y el texto
    sigue mencionando `created_at` para quien lo lea por encima. Por eso acá se
    compara la sentencia ENTERA, no un fragmento.
    """
    pool = PoolFalso()
    asyncio.run(sb._cleanup_bench_data(pool, desde=datetime.now(timezone.utc)))
    esperadas = {
        f"DELETE FROM {t} WHERE agent = $1 AND created_at >= $2"
        for t in sb._TABLAS_DE_BENCH_CON_FECHA
    } | {"DELETE FROM identity WHERE agent = $1"}
    assert set(pool.deletes()) == esperadas, (
        f"el SQL de borrado cambió de forma: {sorted(set(pool.deletes()) - esperadas)}"
    )


def test_qa_negative_el_candado_NO_se_lo_traga_el_except_de_la_limpieza_vectorial(monkeypatch):
    """El `except Exception: pass` cubre un Qdrant caído, no un borrado mal apuntado.

    Sin la rama que re-levanta, la guarda queda anulada justo ahí y la corrida termina
    «bien». Lo delató un mutante que sobrevivía porque el resto de los brazos sustituye
    esta función por una doble y nunca la ejecutaba de verdad.

    SEGURO: el nombre es SEÑUELO y el candado actúa antes de enviar nada; además se
    apunta la URL a un puerto muerto para que ni por error salga una petición.
    """
    monkeypatch.setattr(sb, "BENCH_AGENT_A", "SENUELO_QUE_NO_ES_BENCH")
    monkeypatch.setattr(sb, "settings", _SettingsAPuertoMuerto())
    with pytest.raises(sb.LimpiezaFueraDeAlcance):
        asyncio.run(_LIMPIAR_QDRANT_REAL())


def test_qa_control_un_qdrant_caido_NO_rompe_la_corrida(monkeypatch):
    """El control del brazo anterior: el except sigue cumpliendo su función real.

    Sin esto, «re-levantar siempre» pasaría el test de arriba y rompería toda corrida
    con Qdrant apagado.
    """
    monkeypatch.setattr(sb, "settings", _SettingsAPuertoMuerto())
    asyncio.run(_LIMPIAR_QDRANT_REAL())  # no debe levantar
