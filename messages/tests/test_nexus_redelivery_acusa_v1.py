"""El tick de reentrega debe ACUSAR cada re-push, o el bucle es infinito.

Qué lo generó (NEXUS + JARVIS, 4-sep-2026 04:15): `_redelivery_tick` empujaba
la fila y NUNCA llamaba a `mark_delivered`, mientras el comentario de arriba
afirmaba lo contrario. Consecuencia medida: `attempts` nunca crecía, así que el
backstop `attempts < MAX_ATTEMPTS` jamás se alcanzaba y `delivered_at` seguía
NULL — la fila quedaba elegible para siempre.

    2.115 filas sin acusar desde el 25-jun
    un DM de ADA re-empujado TRECE veces al JSONL de William
    la fila 8943 en attempts=0 nueve horas después de crearse

El defecto no era que fallara el reintento: era que nadie tomaba la fila.

Cuatro brazos: unit (la llamada existe en el cuerpo del tick) / positivo (se
acusa el msg_id que se empujó) / negativo (`mark_delivered` no pisa un `read`)
/ control anti-vacuo (el push sigue ocurriendo: acusar no debe reemplazarlo).
"""
import ast
import os
import sys
from pathlib import Path

os.environ.setdefault("SEAL_PG_DSN", "postgresql://test:test@127.0.0.1:1/test")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chat_server  # noqa: E402
import seal_message_delivery as smd  # noqa: E402


def _cuerpo_del_tick() -> ast.AST:
    arbol = ast.parse(Path(chat_server.__file__).read_text())
    for n in ast.walk(arbol):
        if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and "redelivery" in n.name:
            return n
    raise AssertionError("no encontre el tick de reentrega")


def _llamadas(nodo) -> set:
    nombres = set()
    for c in ast.walk(nodo):
        if isinstance(c, ast.Call):
            f = c.func
            if isinstance(f, ast.Name):
                nombres.add(f.id)
            elif isinstance(f, ast.Attribute):
                nombres.add(f.attr)
    return nombres


def test_unit_el_tick_acusa_lo_que_reentrega():
    assert "mark_delivered" in _llamadas(_cuerpo_del_tick()), (
        "el tick empuja sin acusar: attempts nunca crece y el bucle no termina"
    )


def test_control_el_tick_SIGUE_empujando():
    """Anti-vacuo: si acusar reemplazara al push, la reentrega dejaría de
    entregar y el bucle 'se arreglaría' no entregando nada."""
    assert "_push_to_agents" in _llamadas(_cuerpo_del_tick())


def test_positivo_mark_delivered_incrementa_attempts_y_sella_delivered_at():
    fuente = Path(smd.__file__).read_text()
    assert "attempts = attempts + 1" in fuente
    assert "delivered_at = now()" in fuente


def test_negativo_delivered_NO_pisa_read():
    """delivered != read: acusar el transporte no puede afirmar que el humano
    leyó. Ésa fue la razón de elegir mark_delivered y no mark_read para cortar
    el bucle de la fila 8943."""
    fuente = Path(smd.__file__).read_text()
    assert "CASE WHEN status='read' THEN status ELSE 'delivered' END" in fuente


def test_control_el_backstop_sigue_mirando_attempts():
    """Si el predicado dejara de filtrar por attempts, acusar no alcanzaría
    para terminar el bucle: la fila volvería igual."""
    fuente = Path(smd.__file__).read_text()
    # En LAS DOS ramas: mi version anterior pedia la cadena "en algun lado" y un
    # mutante que anulaba el backstop en UNA sola rama sobrevivia. Mismo defecto
    # que ya me habia pasado con el ORDER BY, dos horas antes.
    assert fuente.count("attempts < $2") == 2


# ── El filtro de agentes va en el SQL, no después del LIMIT ──────────────────
#
# Segundo defecto, medido el 4-sep 04:25 tras desplegar el acuse y NO ver
# efecto: el consumidor pedía las 100 filas más viejas y descartaba en Python
# las de agentes fuera del rollout. Como ésas nunca se acusaban, seguían siendo
# las más viejas y ocupaban la ventana entera para siempre:
#
#     ventana real de 100  ->  ADA 82 · ALICE 15 · FABLE 3
#     procesables          ->  0
#
# El tick corría cada 15 s sin entregar ni acusar nada. Filtrar después del
# LIMIT es pedir las primeras 100 de una lista y descartarlas todas.

def test_unit_la_consulta_acepta_filtrar_agentes_en_el_SQL():
    import inspect
    import seal_message_delivery as smd
    firma = inspect.signature(smd.pending_for_redelivery)
    assert "solo_agentes" in firma.parameters, (
        "sin filtro en el SQL, el consumidor sólo puede descartar DESPUÉS del LIMIT"
    )


def test_positivo_el_filtro_esta_en_el_WHERE_no_en_python():
    import seal_message_delivery as smd
    fuente = Path(smd.__file__).read_text()
    assert "upper(to_agent) = ANY(" in fuente, "el filtro debe ir en la consulta"


def test_control_sin_filtro_el_comportamiento_es_el_HISTORICO():
    """Anti-vacuo: el parámetro es opcional. Si filtrara siempre, romperíamos a
    los otros llamadores del módulo (fable_flood_17 pide la cola completa)."""
    import inspect
    import seal_message_delivery as smd
    assert inspect.signature(smd.pending_for_redelivery).parameters["solo_agentes"].default is None


def test_control_el_tick_PASA_los_agentes_habilitados():
    """El filtro puede existir y no usarse: es el mismo 'helper perfecto y
    desconectado' que ya me sobrevivió dos veces esta madrugada."""
    fuente = Path(chat_server.__file__).read_text()
    assert "solo_agentes=_ACK_ENABLED_AGENTS" in fuente


def test_positivo_lo_NUNCA_entregado_va_antes_que_un_reintento():
    """Tercer defecto encadenado, medido el 4-sep 05:15 tras arreglar el filtro.

    La ventana volvió a llenarse sola, esta vez con 100 REINTENTOS de filas ya
    entregadas y CERO nunca-entregadas: con `ORDER BY priority, created_at` un
    mensaje que nunca llegó pierde contra un reintento más viejo, y los 20 DMs
    pendientes desde el 31-jul quedaban afuera otra vez.

    Un mensaje no entregado no puede perder contra uno que ya llegó.
    """
    import seal_message_delivery as smd
    fuente = Path(smd.__file__).read_text()
    # Anclado a la LINEA de SQL, no a la cadena suelta: mi primera version
    # contaba tambien la mencion dentro del docstring y daba 3 en vez de 2.
    linea = "ORDER BY priority ASC, (delivered_at IS NOT NULL) ASC, created_at ASC"
    assert fuente.count(linea) == 2, (
        f"las DOS ramas de pending_for_redelivery deben priorizar lo nunca "
        f"entregado; encontradas {fuente.count(linea)}"
    )


# ══════════════════════════════════════════════════════════════════════════
# CONDICION DE FABLE (7-sep-2026, #151605): "el tope declarado no es el tope
# efectivo". Lo midio en la base real y lo confirme yo:
#
#     attempts  8 -> 234 filas    9 -> 2910    10 -> 4    11 -> 84
#     de esas 3226, TODAS con status='delivered'; ninguna atascada
#
# LA CAUSA, ubicada: `attempts` lo incrementan DOS caminos y solo uno esta
# acotado.
#
#     camino 1  el lazo de reintento (seal_message_delivery:148)
#               SELECT ... WHERE status <> 'read' AND attempts < MAX
#               -> un mensaje con attempts >= MAX ya NO se vuelve a elegir
#
#     camino 2  la entrega EN VIVO por WebSocket (chat_server:806)
#               llama mark_delivered() -> attempts = attempts + 1 SIN tope
#
# O sea: MAX_ATTEMPTS acota A QUIEN SE REINTENTA, no cuanto puede crecer el
# contador. `attempts` cuenta ENTREGAS, no fracasos.
#
# NO se "arregla" poniendo un tope en mark_delivered: eso haria que el contador
# mienta sobre cuantas veces se entrego. Lo que estaba mal era la AFIRMACION del
# manifiesto, no el codigo. Estos brazos fijan la conducta real para que nadie
# vuelva a leer el tope como una cota del contador.
# ══════════════════════════════════════════════════════════════════════════

RAIZ = Path(__file__).resolve().parents[2]


def test_el_tope_acota_A_QUIEN_SE_REINTENTA_no_al_contador():
    """El filtro vive en el SELECT del lazo, no en el UPDATE del contador."""
    entrega = (RAIZ / "messages/seal_message_delivery.py").read_text()
    i = entrega.index("async def mark_delivered")
    cuerpo = entrega[i:i + 420]
    assert "attempts = attempts + 1" in cuerpo
    assert "attempts <" not in cuerpo, (
        "si mark_delivered acotara el contador, `attempts` dejaria de contar "
        "entregas reales y el numero mentiria")
    assert "attempts < $2" in entrega.split("async def mark_delivered")[0], (
        "el tope debe seguir estando en el SELECT del lazo de reintento")


def test_el_SEGUNDO_camino_existe_y_esta_declarado():
    """La entrega en vivo por WS tambien incrementa: es el camino que hace que
    el contador supere el tope, y tiene que quedar VISIBLE en el codigo."""
    chat = (RAIZ / "messages/chat_server.py").read_text()
    assert "_msgdelivery.mark_delivered" in chat, (
        "el camino en vivo ya no llama a mark_delivered: si desaparece, el "
        "outbox deja de saber que el mensaje llego por WS")
    i = chat.index("_msgdelivery.mark_delivered")
    contexto = chat[max(0, i - 500): i]
    assert "attempts++" in contexto or "attempts" in contexto, (
        "el efecto sobre attempts debe estar dicho donde se llama, no descubrirse "
        "midiendo la base seis meses despues")
