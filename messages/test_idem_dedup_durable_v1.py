"""La dedup de idempotencia sobrevive al reinicio del chat server.

Subject = chat_server.py, helper `_fila_previa_por_clave`.

Qué generó este test (NEXUS, 3-sep-2026): `_enqueued_ids` es un LRU en memoria
de 2000 que no se rehidrata al arrancar, así que un reintento posterior a un
reinicio escribía una segunda fila. El broker de ALICE-V2 (`a1a1b0a`) reintenta
escrituras de chat pendientes confiando en esta idempotencia.

Cuatro brazos: unit (LRU frío consulta la DB) / positivo (clave ya persistida ->
devuelve el id anterior) / negativo (clave nueva -> None, deja escribir) /
control anti-vacuo (si la consulta revienta, falla ABIERTO con None).
"""
import asyncio, os, sys
from pathlib import Path
import pytest

os.environ.setdefault("SEAL_PG_DSN", "postgresql://test:test@127.0.0.1:1/test")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.append("/home/dadito/IA/proyecto-seal/messages")  # chat_db y demas, sin pisar chat_server
import chat_server  # noqa: E402


class _Pool:
    def __init__(self, valor=None, revienta=False):
        self.valor, self.revienta, self.consultas = valor, revienta, []
    async def fetchval(self, sql, *args):
        self.consultas.append((sql, args))
        if self.revienta:
            raise RuntimeError("conexion caida")
        return self.valor


def _correr(pool, clave):
    original = chat_server.chat_db.pool
    chat_server.chat_db.pool = pool
    try:
        return asyncio.run(chat_server._fila_previa_por_clave(clave))
    finally:
        chat_server.chat_db.pool = original


def test_positivo_clave_ya_persistida_devuelve_el_id_anterior():
    pool = _Pool(valor="api_alice-v2_111")
    assert _correr(pool, "alice-v2-grant:abc") == "api_alice-v2_111"
    sql, args = pool.consultas[0]
    assert "idempotency_key" in sql and args == ("alice-v2-grant:abc",)


def test_negativo_clave_nueva_no_bloquea_la_escritura():
    assert _correr(_Pool(valor=None), "clave-que-no-existe") is None


def test_control_la_consulta_rota_falla_ABIERTA():
    # Anti-vacuo: si esto devolviera algo distinto de None, una DB caída
    # dejaría al chat mudo. Perder una dedup es preferible a perder mensajes.
    assert _correr(_Pool(revienta=True), "alice-v2-grant:abc") is None


def test_unit_la_capa_esta_CABLEADA_en_el_handler_de_envio():
    """El helper puede estar perfecto y no ser llamado nunca.

    Por AST, no por substring: exige que alguna corrutina del modulo llame a
    `_fila_previa_por_clave`. Sin esto, borrar la llamada del handler deja el
    test verde -- lo medi: el mutante `if False:` sobrevivia.
    """
    import ast
    arbol = ast.parse(Path(chat_server.__file__).read_text())
    llamantes = {
        n.name
        for n in ast.walk(arbol)
        if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
        for c in ast.walk(n)
        if isinstance(c, ast.Call)
        and isinstance(c.func, ast.Name)
        and c.func.id == "_fila_previa_por_clave"
    }
    assert llamantes, "el helper existe pero no lo llama nadie"


def test_unit_la_clave_viaja_a_la_METADATA_persistida():
    """Anclado al diccionario `metadata`, no a cualquier aparicion de la clave.

    Mi primer intento buscaba la cadena suelta y el mutante golpeaba la
    ocurrencia de `entry`, cuatrocientas lineas antes: el test seguia verde
    mientras la metadata dejaba de llevar la clave.
    """
    import ast
    arbol = ast.parse(Path(chat_server.__file__).read_text())
    for n in ast.walk(arbol):
        if not isinstance(n, ast.Dict):
            continue
        claves = {k.value for k in n.keys if isinstance(k, ast.Constant)}
        if {"to", "legacy_id"} <= claves:
            assert "idempotency_key" in claves, (
                "la metadata persistida no lleva la clave: la capa contra la DB "
                "no tendria con que comparar"
            )
            return
    pytest.fail("no encontre el diccionario de metadata persistida")


def test_control_ningun_NOMBRE_INDEFINIDO_en_el_modulo():
    """Barrido de la CLASE, no del caso. Lo pidió el hallazgo de ALICE.

    Tres nombres se usaban sin existir: `log` (dos except que convertían un
    error atrapado en NameError) y `_SUPERADMIN_USERNAME` (el chequeo de admin
    del borrado, roto 15 días con 500 para todos). Este test los caza a los
    tres y a los que vengan.
    """
    import ast, builtins
    ruta = Path(chat_server.__file__)
    arbol = ast.parse(ruta.read_text())
    # Los dunder de modulo los inyecta Python, no el codigo: sin esta linea el
    # test grita por `__file__` y el ruido tapa los hallazgos de verdad.
    definidos = set(dir(builtins)) | {
        "__file__", "__name__", "__doc__", "__package__", "__spec__", "__loader__",
    }
    for n in ast.walk(arbol):
        if isinstance(n, ast.Assign):
            definidos |= {t.id for t in n.targets if isinstance(t, ast.Name)}
        elif isinstance(n, (ast.AnnAssign, ast.AugAssign)) and isinstance(n.target, ast.Name):
            definidos.add(n.target.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            definidos.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            definidos |= {(a.asname or a.name).split(".")[0] for a in n.names}
        elif isinstance(n, ast.arg):
            definidos.add(n.arg)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            definidos.add(n.id)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            definidos |= set(n.names)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            definidos.add(n.name)
        elif isinstance(n, (ast.comprehension,)):
            pass
    usados = {
        (x.id, x.lineno)
        for x in ast.walk(arbol)
        if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load)
    }
    faltan = sorted({(nom, ln) for nom, ln in usados if nom not in definidos})
    assert not faltan, f"nombres usados y nunca definidos: {faltan}"


def test_control_la_consulta_NO_corre_dentro_del_lock_de_la_cola():
    """Lo pidió JARVIS revisando, con el número: 160 ms de scan con 119.628
    filas y sin índice, y el MISS es el caso común. Adentro de `_queue_lock`
    eso serializa el servidor por cada POST.

    Verifica por AST que la llamada al helper NO ocurre dentro de un
    `async with _queue_lock`. Estructural, no de texto: renombrar la variable
    o reordenar líneas no lo engaña.
    """
    import ast
    arbol = ast.parse(Path(chat_server.__file__).read_text())
    def llama(nodo):
        return any(
            isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
            and c.func.id == "_fila_previa_por_clave"
            for c in ast.walk(nodo)
        )
    dentro = [
        n for n in ast.walk(arbol)
        if isinstance(n, ast.AsyncWith)
        and any(
            isinstance(i.context_expr, ast.Name) and i.context_expr.id == "_queue_lock"
            for i in n.items
        )
        and llama(n)
    ]
    assert not dentro, (
        f"la consulta corre dentro de _queue_lock (linea {dentro[0].lineno}): "
        "un scan de 160 ms serializa cada POST de agente"
    )
