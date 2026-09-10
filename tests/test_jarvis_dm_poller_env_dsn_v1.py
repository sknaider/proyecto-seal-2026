#!/usr/bin/env python3
"""El DSN del poller sale del ENTORNO, nunca del código. Fail-closed si falta.

RECONSTRUIDO el 9-sep-2026 por JARVIS. El archivo original se firmó el 5-sep y **nunca entró
al índice de git**, así que se perdió con el home el 7-sep y el manifiesto quedó `REJECTED`
con `missing_test`. Se rehízo desde `quality/manifests/jarvis-dm-poller-env-dsn-20260905.json`,
que preservó los nombres exactos de las 6 pruebas. El sujeto nunca se perdió.

Qué protege (`messages/jarvis_dm_poller.py:12-18`): **el poller de ALICE murió 26 h con un DSN
fijo cuya contraseña había rotado.** El de JARVIS sobrevivía sólo porque nunca tuvo que
reconectar — o sea, no estaba bien, estaba con suerte. El arreglo es doble:

  1. la credencial vive en la unidad (`EnvironmentFile`), no en el fuente;
  2. **fail-closed**: sin la variable el módulo NO arranca, en vez de seguir con una vieja.

**Por qué se importa POR NOMBRE y en proceso** (lo dice la nota del manifiesto y se respeta):
`coverage --source=jarvis_dm_poller` sólo reporta si el módulo entra por su nombre real. Por
ruta da «No data» y por directorio `messages` mide el 1 % de todo `messages/`. Los brazos de
fallo sí necesitan subproceso, porque el `SystemExit` es de nivel de módulo: importarlo sin la
variable mataría a pytest.

**Cuidado con el estado (JARVIS, 9-sep):** `STATE_FILE` es la constante `/tmp/jarvis_dm_last_ts.txt`
y es **el cursor del poller VIVO**. Un test que llame a `save_last_ts` sin redirigirla le pisa
la posición al proceso en producción y le hace saltear o repetir mensajes de William. Acá se
redirige siempre a un temporal.

Nunca se imprime el valor de un DSN: los brazos usan un señuelo que no abre nada.
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLLER = ROOT / "messages" / "jarvis_dm_poller.py"

# Señuelo: sintácticamente un DSN, pero no apunta a nada real ni lleva credencial verdadera.
DSN_SENUELO = "postgresql://usuario_senuelo:no-es-una-clave@127.0.0.1:1/base_senuelo"

# El módulo lee la variable AL IMPORTARSE, así que se pone antes. Se importa por su NOMBRE
# real para que `coverage --source=jarvis_dm_poller` recolecte (ver docstring).
os.environ["SEAL_POLLER_DSN"] = DSN_SENUELO
sys.path.insert(0, str(ROOT / "messages"))
import jarvis_dm_poller  # noqa: E402


def _importar_en_subproceso(valor: str | None) -> subprocess.CompletedProcess[str]:
    """Importa el módulo en un subproceso con `SEAL_POLLER_DSN` en el valor pedido.

    `None` borra la variable. Hace falta un subproceso porque el `SystemExit` del módulo es de
    nivel de import: probarlo en proceso mataría a pytest.
    """
    env = {k: v for k, v in os.environ.items() if k != "SEAL_POLLER_DSN"}
    if valor is not None:
        env["SEAL_POLLER_DSN"] = valor
    codigo = (
        "import importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('jdp', r'{POLLER}')\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(m)\n"
        "print('DSN_CARGADO=' + m.DSN)\n"
    )
    return subprocess.run([sys.executable, "-c", codigo],
                          capture_output=True, text=True, env=env, timeout=60)


# ------------------------------------------------------- qa_positive: el DSN sale del entorno

def test_el_dsn_sale_del_entorno():
    """El módulo toma el DSN de `SEAL_POLLER_DSN`, no de una constante del fuente."""
    assert jarvis_dm_poller.DSN == DSN_SENUELO, (
        "el DSN cargado NO es el que puso el entorno: hay otra fuente en juego"
    )
    r = _importar_en_subproceso(DSN_SENUELO)
    assert r.returncode == 0, f"no arranco con la variable puesta;\n  stderr={r.stderr[-400:]}"
    assert f"DSN_CARGADO={DSN_SENUELO}" in r.stdout, (
        "en un proceso limpio el DSN tampoco salio del entorno"
    )


def test_el_dsn_se_recorta():
    """Espacios y saltos alrededor se recortan: un `EnvironmentFile` los arrastra fácil.

    Sin el `.strip()`, un DSN con un salto al final se pasaria tal cual a asyncpg y fallaria al
    conectar con un error que no dice que el problema es un espacio.
    """
    r = _importar_en_subproceso(f"  {DSN_SENUELO}\n")

    assert r.returncode == 0, f"no arranco con espacios alrededor;\n  stderr={r.stderr[-400:]}"
    assert f"DSN_CARGADO={DSN_SENUELO}" in r.stdout, (
        f"el DSN conservo espacios o saltos: falta el .strip()\n  stdout={r.stdout[:200]!r}"
    )


def test_conecta_y_reconecta_con_el_dsn_del_entorno():
    """LAS DOS conexiones usan el DSN del entorno: la inicial y la RECONEXION tras un error.

    Es el brazo que cubre la falla de ALICE. Su poller murio 26 h porque reconectaba con una
    credencial vieja; el de JARVIS no fallaba solo porque nunca tuvo que reconectar. Si alguien
    dejara la reconexion apuntando a otra constante, todo seguiria verde hasta la primera caida.

    Se comprueba sobre el AST: toda llamada `.connect(...)` recibe el nombre `DSN`.
    """
    arbol = ast.parse(POLLER.read_text(encoding="utf-8"))
    conexiones = [n for n in ast.walk(arbol)
                  if isinstance(n, ast.Call)
                  and isinstance(n.func, ast.Attribute) and n.func.attr == "connect"]

    assert len(conexiones) >= 2, (
        f"se esperaban al menos 2 llamadas a connect (inicial y reconexion); hay {len(conexiones)}. "
        "Si desaparecio la reconexion, el poller muere en el primer error de red."
    )
    for c in conexiones:
        assert c.args, "una llamada a connect quedo sin argumento de conexion"
        arg = c.args[0]
        assert isinstance(arg, ast.Name) and arg.id == "DSN", (
            f"la llamada a connect de la linea {c.lineno} NO usa la variable DSN del entorno. "
            "Es exactamente como murio el poller de ALICE."
        )


# ------------------------------------------------- qa_negative: sin variable no arranca

def test_sin_variable_el_modulo_no_arranca_y_nombra_la_variable():
    """Fail-closed: sin `SEAL_POLLER_DSN` el modulo aborta, y dice CUAL falta.

    No basta con morir: si el mensaje no nombra la variable, el operador ve la unidad en failed
    sin saber que cargar.
    """
    r = _importar_en_subproceso(None)

    assert r.returncode != 0, (
        f"el modulo ARRANCO sin la variable: conectaria a ciegas.\n  stdout={r.stdout[:300]}"
    )
    assert "SEAL_POLLER_DSN" in r.stderr, (
        f"el error no nombra la variable que falta;\n  stderr={r.stderr[-400:]}"
    )
    assert "DSN_CARGADO=" not in r.stdout, "llego a cargar un DSN pese a no tener la variable"


def test_variable_vacia_o_en_blanco_tambien_falla():
    """Vacia y con solo espacios son el MISMO caso que ausente: un EnvironmentFile mal escrito.

    Es lo que hace util al `.strip()` mas alla de la cosmetica: sin el, `SEAL_POLLER_DSN='   '`
    pasaria el `if not DSN` y el poller intentaria conectar con espacios.
    """
    for valor, nombre in [("", "vacia"), ("   ", "solo espacios"), ("\n\t ", "solo blancos")]:
        r = _importar_en_subproceso(valor)
        assert r.returncode != 0, (
            f"el modulo arranco con la variable {nombre} ({valor!r}): deberia fallar cerrado.\n"
            f"  stdout={r.stdout[:200]}"
        )
        assert "SEAL_POLLER_DSN" in r.stderr, (
            f"con la variable {nombre} el error no la nombra;\n  stderr={r.stderr[-300:]}"
        )


# ------------------------------------------- qa_control: no hay credencial en el fuente

def test_control_no_hay_credencial_incrustada_en_el_fuente():
    """CONTROL — el fuente no puede llevar una credencial, ni de ejemplo.

    Es la razon de ser del carril: si el DSN vuelve al codigo, todo lo demas da igual. Se busca
    la FORMA `esquema://usuario:algo@host`, que es como se escribe un DSN con contrasena, en vez
    de una clave concreta -una lista de claves conocidas envejece.

    No se imprime jamas lo encontrado: el mensaje da la linea, no el valor.
    """
    fuente = POLLER.read_text(encoding="utf-8")
    con_credencial = re.compile(r"(?:postgres|postgresql)://[^\s:/@]+:[^\s@]+@")

    hallazgos = [n for n, linea in enumerate(fuente.splitlines(), 1)
                 if con_credencial.search(linea)]
    assert not hallazgos, (
        f"hay un DSN con credencial incrustado en {POLLER.name}, lineas {hallazgos}. "
        "La credencial va en el EnvironmentFile de la unidad. "
        "(No se reproduce el valor a proposito.)"
    )
    assert "os.environ" in fuente and "SEAL_POLLER_DSN" in fuente, (
        "el modulo dejo de leer la credencial del entorno"
    )


# ------------------------- brazos del estado: NUNCA contra el cursor del poller vivo

def test_el_cursor_se_lee_y_se_guarda_sin_tocar_el_del_poller_vivo(tmp_path, monkeypatch):
    """`load_last_ts`/`save_last_ts` hacen ida y vuelta sobre el archivo que se les indique.

    `STATE_FILE` apunta por defecto a `/tmp/jarvis_dm_last_ts.txt`, que es el cursor del poller
    EN PRODUCCION. Este brazo lo redirige a un temporal a proposito: un test que escriba el
    archivo real le hace saltear o repetir mensajes de William al proceso vivo.
    """
    estado = tmp_path / "cursor.txt"
    monkeypatch.setattr(jarvis_dm_poller, "STATE_FILE", estado)

    momento = datetime(2026, 9, 9, 18, 30, tzinfo=timezone.utc)
    jarvis_dm_poller.save_last_ts(momento)
    assert estado.exists(), "no se escribio el cursor en la ruta indicada"

    assert jarvis_dm_poller.load_last_ts() == momento, (
        "el cursor no sobrevivio la ida y vuelta: el poller repetiria o saltearia mensajes"
    )


def test_el_bucle_reconecta_con_el_DSN_del_entorno_tras_un_error(tmp_path, monkeypatch):
    """POR COMPORTAMIENTO — la reconexión real usa el DSN del entorno, no sólo en el AST.

    El brazo del AST prueba que el CODIGO nombra `DSN` en ambas llamadas. Este prueba que al
    EJECUTARSE, tras un fallo de la consulta, el poller vuelve a conectar y **con qué valor**.
    Es la falla de ALICE reproducida: si la reconexion tomara una credencial vieja, el AST
    seguiria verde y el poller moriria igual en la primera caida.

    `JARVIS_INBOX` y `STATE_FILE` se redirigen a temporales: ambas son constantes que apuntan a
    los archivos del poller EN PRODUCCION.
    """
    dsns_usados: list[str] = []
    vueltas = {"n": 0}

    class ConnFalsa:
        async def fetch(self, sql, *args):
            vueltas["n"] += 1
            if vueltas["n"] == 1:
                raise RuntimeError("conexion caida")     # fuerza la rama de reconexion
            return []

    async def _connect_falso(dsn):
        dsns_usados.append(dsn)
        return ConnFalsa()

    async def _sleep_que_corta(_segundos):
        if vueltas["n"] >= 2:
            raise asyncio.CancelledError()               # sale del `while True` sin colgarse

    import asyncio
    monkeypatch.setattr(jarvis_dm_poller.asyncpg, "connect", _connect_falso)
    monkeypatch.setattr(jarvis_dm_poller.asyncio, "sleep", _sleep_que_corta)
    monkeypatch.setattr(jarvis_dm_poller, "JARVIS_INBOX", tmp_path / "inbox.jsonl")
    monkeypatch.setattr(jarvis_dm_poller, "STATE_FILE", tmp_path / "cursor.txt")

    try:
        asyncio.run(jarvis_dm_poller.poll_dm())
    except asyncio.CancelledError:
        pass

    assert len(dsns_usados) >= 2, (
        f"hubo {len(dsns_usados)} conexion(es): tras el error el poller NO reconecto. "
        "Muere en la primera caida de red."
    )
    assert all(d == DSN_SENUELO for d in dsns_usados), (
        "una conexion NO uso el DSN del entorno. Es exactamente como murio el poller de ALICE: "
        "la reconexion tomaba una credencial vieja mientras la inicial estaba bien."
    )


def test_un_dm_de_william_se_escribe_en_el_inbox_y_avanza_el_cursor(tmp_path, monkeypatch):
    """Camino de éxito: una fila se convierte en línea del inbox y el cursor avanza.

    Sin esto, todo lo demas podria estar bien y el poller no entregar un solo mensaje. El
    inbox y el cursor van a temporales: los reales son los del proceso vivo.
    """
    import asyncio
    creado = datetime(2026, 9, 9, 18, 45, tzinfo=timezone.utc)
    fila = {"sender_name": "William", "content": "probando el poller",
            "created_at": creado, "channel": "dm:jarvis:william", "message_type": "dm"}
    vueltas = {"n": 0}

    class ConnFalsa:
        async def fetch(self, sql, *args):
            vueltas["n"] += 1
            return [fila] if vueltas["n"] == 1 else []

    async def _connect_falso(dsn):
        return ConnFalsa()

    async def _sleep_que_corta(_s):
        raise asyncio.CancelledError()

    inbox = tmp_path / "inbox.jsonl"
    cursor = tmp_path / "cursor.txt"
    monkeypatch.setattr(jarvis_dm_poller.asyncpg, "connect", _connect_falso)
    monkeypatch.setattr(jarvis_dm_poller.asyncio, "sleep", _sleep_que_corta)
    monkeypatch.setattr(jarvis_dm_poller, "JARVIS_INBOX", inbox)
    monkeypatch.setattr(jarvis_dm_poller, "STATE_FILE", cursor)

    try:
        asyncio.run(jarvis_dm_poller.poll_dm())
    except asyncio.CancelledError:
        pass

    assert inbox.exists(), "el DM no llego al inbox: el poller no entrega nada"
    import json as _json
    escrito = _json.loads(inbox.read_text(encoding="utf-8").strip())
    assert escrito["from"] == "William" and escrito["to"] == "JARVIS"
    assert escrito["message"] == "probando el poller"
    assert escrito["type"] == "dm"

    assert cursor.exists(), (
        "el cursor no avanzo tras entregar: al reiniciar, el poller volveria a entregar lo mismo"
    )


def test_sin_cursor_previo_arranca_mirando_hacia_atras(tmp_path, monkeypatch):
    """Sin archivo de estado se arranca desde hace unos minutos, no desde 'ahora'.

    Si arrancara exactamente en `now`, un mensaje llegado durante el arranque se perderia sin
    dejar rastro. El margen hacia atras es lo que evita ese hueco.
    """
    monkeypatch.setattr(jarvis_dm_poller, "STATE_FILE", tmp_path / "no_existe.txt")

    inicio = jarvis_dm_poller.load_last_ts()
    ahora = datetime.now(timezone.utc)

    assert inicio < ahora, "sin cursor previo se arranca en 'ahora': se perderia lo que llegue al arrancar"
    assert (ahora - inicio).total_seconds() >= 60, (
        f"el margen hacia atras quedo en {(ahora - inicio).total_seconds():.0f} s: "
        "muy corto para cubrir un arranque"
    )
