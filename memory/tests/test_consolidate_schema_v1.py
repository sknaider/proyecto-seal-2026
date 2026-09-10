#!/usr/bin/env python3
"""Paso 0 (validación de esquema vivo), paso 1 (consolidación) y el formateador de líneas.

RECONSTRUIDO el 9-sep-2026 por JARVIS. El archivo original se firmó el 5-sep y **nunca entró
al índice de git**, así que se perdió con el home el 7-sep y el manifiesto quedó `REJECTED`
con `missing_test`. Se rehízo desde `quality/manifests/consolidate-live-schema-20260905.json`,
que preservó **los nombres exactos de las 8 pruebas** en sus `commands`. El sujeto
(`memory/consolidate.py`) nunca se perdió.

Qué protege: el 4-sep la consolidación falló pidiéndole a `event_log` una columna `time` que
no existe. El arreglo fue doble — la consulta pide las columnas reales, y **antes de cualquier
escritura** `validate_live_schema` compara el esquema vivo contra `REQUIRED_COLUMNS` y revienta
nombrando tabla y columna. Un consolidador que escribe sobre un esquema que no es el que cree
es peor que uno parado.

Todo corre con dobles: no toca la DB ni Ollama, y por eso vale en cualquier máquina.
"""
from __future__ import annotations

import asyncio
import inspect
import sys
from pathlib import Path

import pytest

MEMORY_DIR = Path(__file__).resolve().parents[1]
if str(MEMORY_DIR) not in sys.path:
    sys.path.insert(0, str(MEMORY_DIR))

import consolidate  # noqa: E402
from consolidate import (  # noqa: E402
    REQUIRED_COLUMNS,
    _format_event_line,
    consolidate_events,
    validate_live_schema,
)


# --------------------------------------------------------------------------- dobles

class ConnEsquema:
    """Conexión que responde el catálogo de columnas que se le declare."""

    def __init__(self, catalogo: dict[str, tuple[str, ...]]):
        self._catalogo = catalogo

    async def fetch(self, sql, *args):
        return [{"table_name": t, "column_name": c}
                for t, cols in self._catalogo.items() for c in cols]


class Fila(dict):
    """Fila estilo asyncpg: se accede por clave y explota si la clave no existe."""

    def __getitem__(self, k):
        if k not in self:
            raise KeyError(
                f"la fila NO tiene la clave {k!r}; tiene {sorted(self)}. "
                "Es el fallo del 4-sep: se pidio una columna inexistente."
            )
        return super().__getitem__(k)


class ConnEventos:
    """Conexión que sirve eventos y REGISTRA cada SELECT/INSERT que recibe."""

    def __init__(self, agentes, eventos):
        self._agentes, self._eventos = agentes, eventos
        self.sql_recibido: list[str] = []
        self.escrituras: list[str] = []

    async def fetch(self, sql, *args):
        self.sql_recibido.append(sql)
        return self._agentes if "DISTINCT agent" in sql else self._eventos

    async def execute(self, sql, *args):
        self.sql_recibido.append(sql)
        self.escrituras.append(sql)


class PoolFalso:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool.conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def _evento(hora, tipo="decision", contenido="algo paso"):
    from datetime import datetime
    return Fila(created_at=datetime(2026, 9, 5, hora, 30), event_type=tipo, content=contenido)


# ------------------------------------- qa_positive: el paso 1 no pide columnas inexistentes

def test_consolidar_eventos_no_pide_columnas_que_event_log_no_tiene():
    """El SELECT del paso 1 pide `created_at`, NUNCA `time`. Es el fallo exacto del 4-sep.

    Las filas son `Fila`, que explota con KeyError si el codigo pide una clave que no existe:
    si alguien reintrodujera `row['time']`, este brazo se pone rojo en vez de fallar recien
    contra la DB viva.
    """
    conn = ConnEventos([{"agent": "JARVIS"}],
                       [_evento(9), _evento(10), _evento(11)])
    asyncio.run(consolidate_events(PoolFalso(conn), hours_back=24, dry_run=True))

    select_eventos = [s for s in conn.sql_recibido if "event_log" in s and "DISTINCT" not in s][0]
    assert "created_at" in select_eventos, "el SELECT dejo de pedir created_at"
    assert "time" not in select_eventos.replace("created_at", ""), (
        f"el SELECT volvio a nombrar una columna 'time' que event_log no tiene:\n{select_eventos}"
    )
    pedidas = {"created_at", "event_type", "content", "agent"}
    reales = set(REQUIRED_COLUMNS["event_log"]) | {"agent"}
    assert pedidas <= reales, f"el SELECT pide columnas fuera del contrato: {pedidas - reales}"


def test_la_linea_del_registro_usa_created_at_y_no_una_clave_inexistente():
    """`_format_event_line` lee `created_at`. Con `time` reventaria, y ese era el bug."""
    linea = _format_event_line(_evento(14, "fix", "arregle el poller"))

    assert "14:30" in linea, f"la hora no salio de created_at: {linea!r}"
    assert "fix" in linea and "arregle el poller" in linea, f"falta tipo o contenido: {linea!r}"

    with pytest.raises(KeyError):
        _format_event_line(Fila(hora="x", event_type="t", content="c"))


def test_el_prompt_al_llm_lleva_las_lineas_formateadas():
    """El resumen se le pide al LLM sobre las lineas YA formateadas, no sobre filas crudas.

    Si el prompt se armara sin pasar por el formateador, el LLM recibiria repr() de filas y el
    resumen saldria basura sin que nada falle: un fallo SILENCIOSO. Se atrapa mirando que el
    texto que llega al LLM contenga la linea formateada.
    """
    capturado = {}

    async def _falso_llm(prompt):
        capturado["prompt"] = prompt
        return ""

    original = consolidate.llm_summarize
    consolidate.llm_summarize = _falso_llm
    try:
        conn = ConnEventos([{"agent": "ADA"}],
                           [_evento(9, "a", "primero"), _evento(10, "b", "segundo"),
                            _evento(11, "c", "tercero")])
        asyncio.run(consolidate_events(PoolFalso(conn), hours_back=24, dry_run=False))
    finally:
        consolidate.llm_summarize = original

    prompt = capturado.get("prompt", "")
    assert prompt, "no se llamo al LLM: el paso 1 no llego a resumir"
    assert "[09:30] a: primero" in prompt, (
        f"el prompt no lleva las lineas formateadas por _format_event_line:\n{prompt[:400]}"
    )
    assert "ADA" in prompt, "el prompt no nombra al agente cuyo registro se resume"


# --------------------------- qa_negative: la deriva de esquema revienta ANTES de escribir

def test_validar_esquema_vivo_falla_nombrando_tabla_y_columna_ausente():
    """No basta con fallar: tiene que decir QUE falta, o el operador queda a ciegas."""
    catalogo = {t: tuple(c for c in cols if c != "embedding")
                for t, cols in REQUIRED_COLUMNS.items()}
    conn = ConnEsquema(catalogo)

    with pytest.raises(RuntimeError) as exc:
        asyncio.run(validate_live_schema(conn))

    msg = str(exc.value)
    assert "memories.embedding" in msg, (
        f"el error debe nombrar TABLA.COLUMNA que falta; dijo: {msg}"
    )
    assert "no se escribe nada" in msg, (
        "el mensaje debe dejar claro que se aborto antes de escribir"
    )


def test_validar_esquema_vivo_falla_si_la_tabla_no_existe():
    """Tabla ausente por completo: deben listarse TODAS sus columnas, no morir por KeyError."""
    conn = ConnEsquema({"memories": REQUIRED_COLUMNS["memories"]})   # falta event_log entera

    with pytest.raises(RuntimeError) as exc:
        asyncio.run(validate_live_schema(conn))

    msg = str(exc.value)
    for col in REQUIRED_COLUMNS["event_log"]:
        assert f"event_log.{col}" in msg, (
            f"falta event_log.{col} en el reporte; con la tabla ausente deben salir todas.\n{msg}"
        )


def test_run_consolidation_valida_antes_de_escribir_y_no_traga_el_error():
    """El paso 0 va ANTES del paso 1, y su RuntimeError NO se captura.

    Se comprueba sobre el codigo de `run_consolidation`: `validate_live_schema` aparece antes
    que `consolidate_events`, y la llamada no vive dentro de un `try`. Si alguien la envolviera
    en un `except`, el consolidador escribiria igual sobre un esquema equivocado — que es
    exactamente lo que el paso 0 existe para impedir.
    """
    src = inspect.getsource(consolidate.run_consolidation)

    i_val = src.index("validate_live_schema")
    i_paso1 = src.index("consolidate_events")
    assert i_val < i_paso1, (
        "validate_live_schema dejo de correr ANTES del paso 1: se escribiria sin validar"
    )

    antes = src[:i_val]
    assert "try" not in antes.split("async with")[-1], (
        "la validacion quedo dentro de un try: un esquema derivado se tragaria en silencio"
    )
    assert "except" not in src[i_val:i_paso1], (
        "hay un except entre la validacion y el paso 1: el fallo del paso 0 no debe absorberse"
    )


# ------------------------------------------------- qa_control: el contrato no es vacio

def test_validar_esquema_vivo_acepta_el_contrato_actual():
    """CONTROL NO VACUO — con el esquema completo NO debe fallar.

    Sin este brazo, un mutante que hiciera fallar SIEMPRE la validacion pasaria los dos brazos
    negativos de arriba, porque los dos esperan RuntimeError.
    """
    conn = ConnEsquema({t: cols for t, cols in REQUIRED_COLUMNS.items()})
    asyncio.run(validate_live_schema(conn))   # no debe levantar nada


def test_un_agente_con_menos_de_tres_eventos_se_saltea():
    """Guard del paso 1: con <3 eventos no vale la pena molestar al LLM.

    Sin el, cada agente con un solo evento dispararia una llamada al modelo y una escritura.
    Se comprueba por EFECTO: no se llama al LLM y no se escribe.
    """
    llamadas = []

    async def _falso_llm(prompt):
        llamadas.append(prompt)
        return "no deberia llegar aca"

    original = consolidate.llm_summarize
    consolidate.llm_summarize = _falso_llm
    try:
        conn = ConnEventos([{"agent": "NEXUS"}], [_evento(9), _evento(10)])   # solo 2
        acciones = asyncio.run(consolidate_events(PoolFalso(conn), hours_back=24, dry_run=False))
    finally:
        consolidate.llm_summarize = original

    assert llamadas == [], "se llamo al LLM con menos de 3 eventos"
    assert conn.escrituras == [], f"se escribio con menos de 3 eventos: {conn.escrituras}"
    assert acciones == [], f"no deberia reportar accion alguna; reporto {acciones}"


def test_el_insert_escribe_las_ocho_columnas_del_contrato():
    """Camino de exito completo: resumen -> embedding -> INSERT con las 8 columnas.

    Ata el INSERT real al contrato de `REQUIRED_COLUMNS['memories']`. Si alguien agregara una
    columna al INSERT sin sumarla al contrato, la validacion del paso 0 no la protegeria.
    """
    orig_llm, orig_emb = consolidate.llm_summarize, consolidate.get_embedding

    async def _llm(prompt):
        return "un resumen util"

    async def _emb(texto):
        return [0.1, 0.2, 0.3]

    consolidate.llm_summarize, consolidate.get_embedding = _llm, _emb
    try:
        conn = ConnEventos([{"agent": "ALICE"}], [_evento(9), _evento(10), _evento(11)])
        acciones = asyncio.run(consolidate_events(PoolFalso(conn), hours_back=24, dry_run=False))
    finally:
        consolidate.llm_summarize, consolidate.get_embedding = orig_llm, orig_emb

    assert len(conn.escrituras) == 1, f"se esperaba UN insert; hubo {len(conn.escrituras)}"
    insert = conn.escrituras[0]
    for col in REQUIRED_COLUMNS["memories"]:
        assert col in insert, f"el INSERT dejo de escribir la columna del contrato: {col}"
    assert any("Consolidated 3 events for ALICE" in a for a in acciones), (
        f"no se reporto la consolidacion; acciones={acciones}"
    )


def test_un_agente_que_falla_no_tumba_la_corrida_de_los_demas():
    """El `except` por agente es DELIBERADO: un modelo caido no debe perder a los otros cuatro.

    Se distingue de la validacion del paso 0, que si aborta todo: alli el esquema esta mal para
    todos; aca el problema es de UN agente. El brazo fija esa diferencia de diseno.
    """
    orig = consolidate.llm_summarize

    async def _llm_que_revienta(prompt):
        raise RuntimeError("ollama no responde")

    consolidate.llm_summarize = _llm_que_revienta
    try:
        conn = ConnEventos([{"agent": "DUM"}], [_evento(9), _evento(10), _evento(11)])
        acciones = asyncio.run(consolidate_events(PoolFalso(conn), hours_back=24, dry_run=False))
    finally:
        consolidate.llm_summarize = orig

    assert conn.escrituras == [], "se escribio pese a que el resumen fallo"
    assert any("ERROR consolidating DUM" in a for a in acciones), (
        f"el fallo del agente debe REPORTARSE, no desaparecer; acciones={acciones}"
    )
    assert any("ollama no responde" in a for a in acciones), (
        "el reporte debe llevar la causa; si no, el fallo es invisible en el registro"
    )


def test_el_contrato_cubre_lo_que_el_select_y_el_insert_usan():
    """El contrato tiene que listar las columnas que el codigo REALMENTE toca.

    Un `REQUIRED_COLUMNS` vacio o recortado pasaria la validacion siempre y no protegeria de
    nada: seria un guardian que no mira. Se ata el contrato al INSERT real del paso 1.
    """
    assert REQUIRED_COLUMNS, "el contrato quedo vacio: validaria cualquier esquema"

    src = inspect.getsource(consolidate.consolidate_events)
    insert = src[src.index("INSERT INTO memories"):]
    columnas_insert = insert[insert.index("(") + 1:insert.index(")")]

    for col in ("agent", "category", "content", "embedding", "importance",
                "source", "valid_from", "metadata"):
        assert col in columnas_insert, f"el INSERT dejo de escribir {col}"
        assert col in REQUIRED_COLUMNS["memories"], (
            f"el INSERT escribe '{col}' pero el contrato no la exige: una deriva en esa "
            "columna pasaria la validacion y reventaria recien al escribir"
        )

    for col in ("created_at", "event_type", "content"):
        assert col in REQUIRED_COLUMNS["event_log"], (
            f"el paso 1 lee '{col}' de event_log pero el contrato no la exige"
        )
