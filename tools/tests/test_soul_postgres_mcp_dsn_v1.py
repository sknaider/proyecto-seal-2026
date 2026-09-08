"""Brazos del DSN del MCP postgres — TODOS en subproceso aislado.

POR QUE EL SUJETO CAMBIO (7-sep-2026 19:25): el DSN vivía sólo en `POSTGRES_MCP_DSN`,
heredado al arrancar. Al reponer la credencial del observer roté el rol y los dos
servidores MCP arrancados el 2-sep quedaron con la clave vieja EN MEMORIA: el MCP
postgres se cayó para todos. **Una credencial que sólo vive en el entorno no se puede
rotar sin reiniciar a todos los que la heredaron, y nadie sabe quiénes son.**

POR QUE ESTE ARCHIVO NO IMPORTA EL MODULO (ADA, 19:32-19:38, cuatro correcciones):
el módulo liga `_OBSERVER_ENV` a `Path.home()/".config/seal/mcp_postgres_observer.env"`
AL IMPORTARSE. Un mutante que congele ese default apunta a la credencial de PRODUCCIÓN.

    1er intento   mover HOME antes del import
                  -> si otro test ya importó el módulo, llega tarde (sys.modules)
                  -> y contamina el HOME de los demás tests del proceso
    2do intento   sólo dos brazos al subproceso
                  -> los otros seis seguían importando `srv` en el padre y llamando
                     `_dsn()` ahí: bajo el mutante, LEEN el archivo real
    esto          NINGUN brazo importa el módulo en el padre. Cada uno corre en un
                  subproceso nuevo, con HOME saneado y sin variables POSTGRES_/SEAL_/PG,
                  y devuelve un VEREDICTO JSON — nunca un valor.

Y la razón de fondo: a las 19:26 un default mal ligado hizo que un brazo leyera el
archivo vivo y pytest publicó la contraseña en el diff del assert. Se rotó tres veces.
**Un test que MANEJA un secreto es un canal de publicación**; la única defensa robusta
es que el proceso que corre el brazo no pueda alcanzarlo.
"""
import json
import os
import pathlib
import subprocess
import sys

import pytest

# Testigo capturado al CARGAR el módulo. Comparar contra `Path.home()` sería CIRCULAR
# —lee ese mismo HOME— y el brazo no podría fallar nunca (ADA, 19:35).
_HOME_AL_CARGAR = os.environ.get("HOME")

RAIZ = pathlib.Path(__file__).resolve().parents[2]
SENUELO = "postgresql://mcp_observer:SENUELO-NO-ES-UNA-CLAVE@localhost:5433/x"

_PREAMBULO = """
import json, os, pathlib, sys
sys.path.insert(0, {tools!r})
import soul_postgres_mcp as srv
{cuerpo}
"""


def _en_subproceso(cuerpo: str, home: pathlib.Path) -> dict:
    """Corre `cuerpo` con el módulo importado FRESCO bajo un HOME saneado.

    El cuerpo debe imprimir una línea JSON con su veredicto. Nunca un DSN.
    """
    entorno = {
        k: v for k, v in os.environ.items()
        if not k.startswith(("POSTGRES_", "SEAL_", "PG"))
    }
    entorno["HOME"] = str(home)
    entorno["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, "-c", _PREAMBULO.format(tools=str(RAIZ / "tools"), cuerpo=cuerpo)],
        capture_output=True, text=True, timeout=120, env=entorno, cwd=str(RAIZ),
    )
    salida = proc.stdout.strip().splitlines()
    if proc.returncode != 0 or not salida:
        # NADA del stderr del hijo sale de acá, ni un fragmento (ADA, 19:40 y 19:41).
        # Mi primera versión recortaba a 160 caracteres —el riesgo es el CONTENIDO, no el
        # volumen: 160 alcanzan para un DSN entero—. La segunda conservaba "el tipo de
        # excepción", que era el texto antes del primer ":" de la última línea: eso puede
        # ser contenido del mensaje, no un tipo. Sólo el código de retorno, que no puede
        # llevar nada adentro.
        raise AssertionError(
            f"el subproceso falló con rc={proc.returncode}; su salida NO se reproduce "
            f"porque puede contener una credencial. Para depurar, corré el cuerpo a mano "
            f"con un señuelo."
        )
    return json.loads(salida[-1])


@pytest.fixture
def home(tmp_path):
    """Un HOME saneado y vacío: aunque el default se congele, no hay credencial ahí."""
    destino = tmp_path / "home"
    (destino / ".config/seal").mkdir(parents=True)
    return destino


def _archivo(tmp_path, nombre, contenido, modo=0o600):
    ruta = tmp_path / nombre
    ruta.write_text(contenido, encoding="utf-8")
    ruta.chmod(modo)
    return ruta


def test_qa_positive_el_entorno_sigue_teniendo_prioridad(home):
    """El fallback no cambia el camino que ya funcionaba."""
    v = _en_subproceso(
        f'os.environ["POSTGRES_MCP_DSN"] = {SENUELO + "?desde=entorno"!r}\n'
        'print(json.dumps({"ok": srv._dsn().endswith("desde=entorno")}))',
        home,
    )
    assert v["ok"]


def test_qa_positive_sin_entorno_cae_al_ARCHIVO(tmp_path, home):
    """El caso que motivó el cambio: un proceso nuevo toma la credencial VIGENTE."""
    env = _archivo(tmp_path, "obs.env", f"POSTGRES_MCP_DSN={SENUELO}\n")
    v = _en_subproceso(
        f'srv._OBSERVER_ENV = pathlib.Path({str(env)!r})\n'
        'print(json.dumps({"ok": "SENUELO-NO-ES-UNA-CLAVE" in srv._dsn()}))',
        home,
    )
    assert v["ok"]


def test_qa_positive_el_archivo_se_RELEE_en_cada_llamada(tmp_path, home):
    """El corazón del arreglo: rotar debe bastar para que el siguiente uso funcione.

    Un valor cacheado al importar reproduciría exactamente el defecto del 2-sep.
    """
    env = _archivo(tmp_path, "obs.env", f"POSTGRES_MCP_DSN={SENUELO}\n")
    v = _en_subproceso(
        f'p = pathlib.Path({str(env)!r})\n'
        'srv._OBSERVER_ENV = p\n'
        'antes = "SENUELO-NO-ES-UNA-CLAVE" in srv._dsn()\n'
        f'p.write_text("POSTGRES_MCP_DSN={SENUELO}?rotado=1\\n"); p.chmod(0o600)\n'
        'print(json.dumps({"antes": antes, "despues": srv._dsn().endswith("rotado=1")}))',
        home,
    )
    assert v["antes"] and v["despues"], "el DSN quedó cacheado: rotar no alcanzaría"


def test_qa_control_el_default_NO_queda_congelado_al_importar(tmp_path, home):
    """ALICE, 19:30 — mutante VIVO al revertir `if ruta is None` al default en la firma.
    ADA, 19:31 — pasar la ruta explícita NO lo distingue: un argumento pisa el default.

    Lo que discrimina es mover `_OBSERVER_ENV` de A a B ENTRE dos llamadas SIN argumento:
    con el default congelado se queda en A.
    """
    a = _archivo(tmp_path, "a.env", f"POSTGRES_MCP_DSN={SENUELO}?marca=A\n")
    b = _archivo(tmp_path, "b.env", f"POSTGRES_MCP_DSN={SENUELO}?marca=B\n")
    v = _en_subproceso(
        f'srv._OBSERVER_ENV = pathlib.Path({str(a)!r})\n'
        'leido_a = srv._dsn_del_archivo().endswith("marca=A")\n'
        f'srv._OBSERVER_ENV = pathlib.Path({str(b)!r})\n'
        'leido_b = srv._dsn_del_archivo().endswith("marca=B")\n'
        'print(json.dumps({"a": leido_a, "b": leido_b}))',
        home,
    )
    assert v["a"] and v["b"], "el default quedó ligado al importar"


def test_qa_control_el_modulo_NO_alcanza_la_credencial_de_PRODUCCION(home):
    """ADA, 19:32: matar el mutante no demuestra aislamiento. Esta es la otra propiedad.

    Se afirma sobre la RUTA -que no es un secreto-, nunca sobre el contenido.
    """
    v = _en_subproceso(
        'print(json.dumps({"ruta": str(srv._OBSERVER_ENV), "home": os.environ["HOME"]}))',
        home,
    )
    assert v["ruta"].startswith(v["home"]), (
        f"el módulo apunta fuera del home saneado: {v['ruta']}"
    )


def test_qa_negative_sin_entorno_y_sin_archivo_falla_RUIDOSO(tmp_path, home):
    v = _en_subproceso(
        f'srv._OBSERVER_ENV = pathlib.Path({str(tmp_path / "no-existe.env")!r})\n'
        'try:\n'
        '    srv._dsn(); r = {"lanzo": False, "dice": ""}\n'
        'except RuntimeError as e:\n'
        '    r = {"lanzo": True, "dice": "no pude leer" in str(e)}\n'
        'print(json.dumps(r))',
        home,
    )
    assert v["lanzo"] and v["dice"]


def test_qa_negative_un_archivo_ILEGIBLE_no_explota_con_OSError(tmp_path, home):
    """Un permiso denegado debe dar el RuntimeError con su explicación, no un OSError
    crudo que el llamador no espera."""
    env = _archivo(tmp_path, "obs.env", f"POSTGRES_MCP_DSN={SENUELO}\n", modo=0o000)
    try:
        v = _en_subproceso(
            f'srv._OBSERVER_ENV = pathlib.Path({str(env)!r})\n'
            'try:\n'
            '    srv._dsn(); r = {"runtime": False}\n'
            'except RuntimeError:\n'
            '    r = {"runtime": True}\n'
            'except OSError:\n'
            '    r = {"runtime": False}\n'
            'print(json.dumps(r))',
            home,
        )
    finally:
        env.chmod(0o600)
    assert v["runtime"]


def test_qa_negative_un_archivo_con_permisos_ANCHOS_se_rechaza(tmp_path, home):
    """ADA, 19:29: que hoy tenga 0600 es evidencia del DESPLIEGUE, no garantía del CÓDIGO.

    El stability guard exige modo 600 sobre esta credencial; el lector exige lo mismo, o
    el día que alguien la afloje nadie se entera desde acá.
    """
    env = _archivo(tmp_path, "obs.env", f"POSTGRES_MCP_DSN={SENUELO}\n", modo=0o644)
    v = _en_subproceso(
        f'srv._OBSERVER_ENV = pathlib.Path({str(env)!r})\n'
        'try:\n'
        '    srv._dsn(); r = {"dice_modo": False}\n'
        'except RuntimeError as e:\n'
        '    r = {"dice_modo": "modo 600" in str(e)}\n'
        'print(json.dumps(r))',
        home,
    )
    assert v["dice_modo"]


def test_qa_control_un_archivo_SIN_dsn_no_devuelve_basura(tmp_path, home):
    """Si el archivo existe pero no tiene ninguna clave conocida, es 'no hay DSN',
    NO la primera línea que aparezca."""
    env = _archivo(tmp_path, "obs.env", "# solo un comentario\nOTRA_COSA=valor\n")
    v = _en_subproceso(
        f'srv._OBSERVER_ENV = pathlib.Path({str(env)!r})\n'
        'try:\n'
        '    srv._dsn(); r = {"lanzo": False}\n'
        'except RuntimeError:\n'
        '    r = {"lanzo": True}\n'
        'print(json.dumps(r))',
        home,
    )
    assert v["lanzo"]


def test_qa_control_el_reintento_es_SOLO_por_clave_invalida_y_UNA_vez(tmp_path, home):
    """ADA, 19:29: una credencial VIEJA en el entorno seguía ganando — que es exactamente
    lo que rompió el MCP a las 19:08.

    El reintento va acotado: sólo `InvalidPasswordError`, una vez, y sólo si el archivo
    ofrece un DSN distinto. Ante otro error se propaga: un reintento amplio escondería
    una caída real de la base.
    """
    env = _archivo(tmp_path, "obs.env", f"POSTGRES_MCP_DSN={SENUELO}?desde=archivo\n")
    v = _en_subproceso(
        f'import asyncio\n'
        f'srv._OBSERVER_ENV = pathlib.Path({str(env)!r})\n'
        f'os.environ["POSTGRES_MCP_DSN"] = {SENUELO + "?viejo=1"!r}\n'
        'intentos = []\n'
        'class C:\n'
        '    async def fetchrow(self, *a, **k):\n'
        '        return {"current_user": "mcp_observer", "session_user": "mcp_observer",\n'
        '                "transaction_read_only": "on", "rolsuper": False,\n'
        '                "rolcreaterole": False, "rolcreatedb": False, "rolcanlogin": True,\n'
        '                "rolreplication": False, "rolbypassrls": False}\n'
        '    async def execute(self, *a, **k): return "SET"\n'
        '    async def close(self): return None\n'
        'async def sin_contrato(_c): return None\n'
        'srv._verify_view_contract = sin_contrato\n'
        'async def conecta(dsn, **kw):\n'
        '    intentos.append(dsn)\n'
        '    if "viejo=1" in dsn:\n'
        '        raise srv.asyncpg.InvalidPasswordError("auth failed")\n'
        '    return C()\n'
        'srv.asyncpg.connect = conecta\n'
        'asyncio.run(srv._connect())\n'
        'reintentos = len(intentos)\n'
        'uso_el_archivo = "desde=archivo" in intentos[-1]\n'
        'intentos.clear()\n'
        'async def caida(dsn, **kw):\n'
        '    intentos.append(dsn); raise OSError("la base no responde")\n'
        'srv.asyncpg.connect = caida\n'
        'try:\n'
        '    asyncio.run(srv._connect()); propago = False\n'
        'except OSError:\n'
        '    propago = True\n'
        'print(json.dumps({"reintentos": reintentos, "uso_el_archivo": uso_el_archivo,\n'
        '                  "propago": propago, "sin_reintento": len(intentos)}))',
        home,
    )
    assert v["reintentos"] == 2, f"debe reintentar exactamente una vez, hubo {v['reintentos']}"
    assert v["uso_el_archivo"], "el reintento debe usar el DSN del ARCHIVO"
    assert v["propago"] and v["sin_reintento"] == 1, (
        "una caída de la base NO debe disparar el reintento"
    )


def test_qa_control_el_rol_del_servidor_sigue_siendo_mcp_observer(home):
    """Control anti-vacuo: el servidor exige un rol concreto. Si cambia sin querer, el
    fallback estaría alimentando una identidad distinta de la auditada."""
    v = _en_subproceso('print(json.dumps({"rol": srv.EXPECTED_ROLE}))', home)
    assert v["rol"] == "mcp_observer"


def test_qa_control_el_HOME_del_proceso_de_test_NO_se_toca():
    """ADA, 19:34 y 19:35: mi primera versión movía el HOME del PROCESO sin restaurarlo
    —contaminando a los demás tests— y el brazo que lo vigilaba era CIRCULAR."""
    assert os.environ.get("HOME") == _HOME_AL_CARGAR, (
        "algún brazo dejó HOME modificado: contaminaría a cualquier test posterior que "
        "lea ~/.config o ~/.claude"
    )


def test_qa_control_una_ruta_EXPLICITA_no_se_pisa_con_OBSERVER_ENV(tmp_path, home):
    """ALICE, 19:48 — segundo mutante, distinto del de la firma: `ruta = _OBSERVER_ENV`
    en el CUERPO, que ignora el argumento recibido. Sobrevivía porque ningún brazo pasaba
    una ruta explícita DISTINTA de `_OBSERVER_ENV`.

    Yo tenía este brazo y lo BORRÉ a las 19:32, cuando ADA demostró que no discriminaba
    el mutante de la FIRMA. Tenía razón sobre ese mutante — y el brazo servía para otro.
    **Un brazo no se juzga contra "el" defecto: se juzga contra CADA defecto.**
    """
    senuelo = tmp_path / "explicito.env"
    senuelo.write_text(
        "POSTGRES_MCP_DSN=postgresql://mcp_observer:SENUELO-EXPLICITO@h:1/x\n",
        encoding="utf-8",
    )
    senuelo.chmod(0o600)
    otro = tmp_path / "otro.env"
    otro.write_text("POSTGRES_MCP_DSN=postgresql://mcp_observer:SENUELO-OTRO@h:1/x\n",
                    encoding="utf-8")
    otro.chmod(0o600)
    v = _en_subproceso(
        # _OBSERVER_ENV apunta a OTRO archivo válido: si el cuerpo pisa el argumento,
        # devuelve el de _OBSERVER_ENV en vez del pedido, y se nota.
        f'srv._OBSERVER_ENV = pathlib.Path({str(otro)!r})\n'
        f'leido = srv._dsn_del_archivo(pathlib.Path({str(senuelo)!r}))\n'
        'print(json.dumps({"respeto_el_argumento": "SENUELO-EXPLICITO" in leido,\n'
        '                  "piso_con_observer_env": "SENUELO-OTRO" in leido}))',
        home,
    )
    assert v["respeto_el_argumento"] and not v["piso_con_observer_env"], (
        "el cuerpo pisó la ruta explícita con _OBSERVER_ENV"
    )
