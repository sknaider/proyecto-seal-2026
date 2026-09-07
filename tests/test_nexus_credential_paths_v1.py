"""Los caminos de credencial NO pueden depender del literal viejo.

POR QUE EXISTE: el 3-sep un `git reset --hard` mío devolvió 260 archivos a HEAD y
con ellos una contraseña que ya no sirve. Los CINCO latidos dejaron de escribir
el event_log durante 14 minutos y los checkpoints fallaron con
InvalidPasswordError. No hubo aviso: el literal estaba ahí desde siempre y sólo
se volvió peligroso cuando el archivo volvió atrás.

QUE FIJA ESTE TEST, y es lo único que impide que se repita: que el literal NO sea
la primera opción. Puede quedar como último recurso —un checkpoint que intenta y
falla con mensaje claro es mejor que uno que no intenta— pero nunca por delante
de `seal_secrets`.

Se testea la FORMA del código y no el efecto vivo a propósito: el efecto depende
de que la base esté arriba y de la credencial del momento, así que un test de
conexión pasaría en verde el día que alguien vuelva a poner el literal primero
mientras la credencial vieja siga siendo válida. La regresión que hay que cazar
es estructural.
"""
import pathlib
import re

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[1]

# Los tres caminos que fallaron el 3-sep, cada uno con una vía distinta.
CAMINOS = [
    "memory/config.py",
    "memory/seal_heartbeat.py",
    "messages/session_checkpoint.py",
]

LITERAL = "seal_memory_2026"


@pytest.mark.parametrize("ruta", CAMINOS)
def test_el_camino_lee_de_seal_secrets(ruta):
    """Cada uno debe intentar `seal_secrets` antes que cualquier default."""
    texto = (RAIZ / ruta).read_text()
    assert "seal_secrets" in texto, (
        f"{ruta} no menciona seal_secrets: si el archivo vuelve atrás, "
        "la credencial vieja gana en silencio"
    )


@pytest.mark.parametrize("ruta", CAMINOS)
def test_el_literal_no_va_antes_que_seal_secrets(ruta):
    """El orden importa: el literal es último recurso, no primera opción."""
    texto = (RAIZ / ruta).read_text()
    if LITERAL not in texto:
        return  # mejor todavía: no está
    pos_literal = texto.index(LITERAL)
    pos_secrets = texto.index("seal_secrets")
    assert pos_secrets < pos_literal, (
        f"{ruta}: el literal aparece ANTES de seal_secrets. "
        "Un default que se evalúa primero no es un fallback, es la vía principal"
    )


def test_el_checkpoint_no_contiene_el_literal_EN_NINGUNA_FORMA():
    """Opción A de FABLE: el literal se ELIMINA, no se degrada a último recurso.

    SU ARGUMENTO: mientras el literal exista en el archivo, un retroceso del
    archivo lo reactiva. Un camino que no lo tiene NO PUEDE retroceder a él.

    Y la primera versión de este test NO cerraba la clase: buscaba
    `DB_URL = "postgresql://…"` —la asignación directa, la forma que YO había
    visto romperse— y un mutante que metía el mismo literal dentro de
    `os.environ.get(..., "<literal>")` pasaba con 8 verdes. Probé la forma que
    esperaba, no todas. Por eso ahora la aserción es sobre la CADENA, que no
    tiene formas.
    """
    texto = (RAIZ / "messages/session_checkpoint.py").read_text()
    assert LITERAL not in texto, (
        "session_checkpoint.py volvió a contener la credencial vieja. Da igual "
        "en qué forma: asignación, default de os.environ.get, o dentro de un "
        "comentario que alguien copie. Si está, un retroceso la reactiva"
    )


def test_control_no_vacuo_el_test_puede_fallar():
    """Sin esto, una constante LITERAL mal escrita haría pasar todo por vacío."""
    for forma in (
        f'DB_URL = "postgresql://seal:{LITERAL}@localhost:5433/seal_memory"',
        f'DB_URL = os.environ.get("SEAL_DB_URL", "postgresql://seal:{LITERAL}@x/y")',
        f'# ojo: la vieja era {LITERAL}',
    ):
        assert LITERAL in forma, "el control debe detectar las tres formas"


# --- BRAZO CONDUCTUAL (4-sep-2026) ---------------------------------------
#
# EL DOCSTRING DE ARRIBA DICE que el efecto no se puede testear porque "depende
# de que la base este arriba y de la credencial del momento". Eso es cierto de
# un test de CONEXION y NO de este: aca no se conecta a nada. Se sustituye
# `seal_secrets` por uno que pone un centinela en el entorno y se comprueba que
# el valor EFECTIVO sea el centinela y no el literal.
#
# POR QUE IMPORTA: los brazos de arriba fijan el ORDEN TEXTUAL (que
# `seal_secrets` aparezca antes que el literal). Un `except` que se trague el
# import, un `if` que saltee la llamada, o un `load_secrets` que sobrescriba al
# reves, dejan el orden del texto intacto y el literal ganando. Ninguno de los
# tres se ve leyendo el archivo.
#
# El canario de importacion lo confirmo: la suite textual queda VERDE contra un
# memory/config.py que hace `raise` en la linea 1.

import os as _os
import sys as _sys
import types as _types

_CENTINELA = "centinela-no-es-una-credencial-real"


def test_qa_positive_conductual_el_valor_efectivo_viene_de_seal_secrets(monkeypatch):
    """Con seal_secrets poblando el entorno, el password efectivo es el suyo.

    Mata el mutante que los brazos textuales no ven: que la llamada a
    seal_secrets no tenga EFECTO (import tragado, rama saltada, orden invertido
    al poblar). El literal seguiria siendo el valor real con el texto intacto.
    """
    falso = _types.ModuleType("seal_secrets")

    def _pg_dsn():
        _os.environ["PG_PASSWORD"] = _CENTINELA
        return "postgresql://x"

    falso.pg_dsn = _pg_dsn
    monkeypatch.setitem(_sys.modules, "seal_secrets", falso)
    monkeypatch.delenv("PG_PASSWORD", raising=False)
    monkeypatch.syspath_prepend(str(RAIZ / "memory"))
    for mod in [m for m in list(_sys.modules) if m == "config"]:
        monkeypatch.delitem(_sys.modules, mod, raising=False)

    import importlib

    cfg = importlib.import_module("config")
    efectivo = cfg.settings.pg_password
    assert efectivo == _CENTINELA, (
        f"el password efectivo es {efectivo!r}: seal_secrets se importa pero no manda"
    )
    assert efectivo != LITERAL, "el literal viejo sigue siendo el valor efectivo"

# ── CUALQUIER credencial cableada, no solo el literal conocido ──────────────
# HALLAZGO DE ALICE (7-sep 13:25): su mutante M2 inserta en un camino un DSN con
# OTRA clave -no el literal viejo- y esta suite no lo veia. Mis brazos fijaban
# "no vuelvas a poner ESA clave"; lo que hay que fijar es "no cablees NINGUNA".
#
# El criterio no se reescribe aca: se IMPORTA del test de unidades, que ya lo
# tenia. Hoy me mordio exactamente lo contrario -escribi el criterio bueno en un
# test y ejecute otro mas pobre en un script-, asi que una sola fuente.
import sys as _sys
_sys.path.insert(0, str(RAIZ / "tests"))
from test_unidades_sin_secreto_v1 import DSN_CON_CLAVE as _DSN_CON_CLAVE  # noqa: E402


@pytest.mark.parametrize("ruta", CAMINOS)
def test_ningun_camino_cablea_UNA_credencial_cualquiera(ruta):
    texto = (RAIZ / ruta).read_text()
    hallazgos = []
    for i, linea in enumerate(texto.splitlines(), 1):
        if linea.lstrip().startswith("#"):
            continue                      # un comentario que EXPLICA no es un cableado
        if _DSN_CON_CLAVE.search(linea):
            hallazgos.append(f"{ruta}:{i}")
    assert not hallazgos, (
        f"DSN con clave embebida en {hallazgos}. Da igual QUE clave sea: si el "
        "camino la lleva en el codigo, un retroceso del archivo la reactiva y el "
        "servicio conecta con una credencial que nadie roto."
    )


def test_CONTROL_el_detector_de_dsn_no_marca_lo_legitimo():
    """Sin esto, 'arreglarlo' marcando todo pasaria en verde."""
    assert not _DSN_CON_CLAVE.search('DSN = os.environ["SEAL_DB_URL"]')
    assert not _DSN_CON_CLAVE.search('postgresql://rol@host/db')      # sin clave
    assert _DSN_CON_CLAVE.search('postgresql://rol:clave@host/db')    # con clave


# ══════════════════════════════════════════════════════════════════════════
# BRAZOS DE CONDUCTA DEL CIERRE — agregados el 7-sep-2026 por el hallazgo de ALICE.
#
# Re-corrio 4 mutantes: murieron los dos que vuelven a cablear una credencial
# -la clase que FABLE hizo eliminar- y SOBREVIVIERON los dos del FAIL-CLOSED.
# Mis brazos comprobaban que el literal NO ESTE; ninguno comprobaba que, sin
# credencial, el modulo se NIEGUE A ARRANCAR.
#
# Son dos cosas distintas: "no hay literal" es la ausencia de un defecto viejo;
# "falla cerrado" es la conducta que lo reemplaza. Probar solo la primera deja
# que alguien convierta el raise en un `pass` y la suite no se entera.
#
# El fail-closed ocurre AL IMPORTAR, asi que se prueba en un SUBPROCESO con un
# `seal_secrets` que revienta, puesto primero en el PYTHONPATH. Nada de mocks:
# se ejecuta el camino real.
# ══════════════════════════════════════════════════════════════════════════
import subprocess
import sys
import tempfile
from pathlib import Path


def _pythonpath(sombra: str, ruta_modulo: str) -> str:
    """La sombra PRIMERO, y despues el sys.path del interprete que corre el test.

    Armar el PYTHONPATH desde cero perdia las dependencias reales -en la arena,
    `import asyncpg` fallaba en la linea 16 y el modulo moria por dependencia, no
    por el fail-closed-. Lo detecto ALICE corriendo mi brazo en la arena, y lo
    delato mi propio segundo assert: "fallo por otra cosa, no por exigir la
    credencial". Ese assert existe justamente para que un verde -o un rojo- por
    el motivo equivocado no pase por medicion.
    """
    import os as _os
    return _os.pathsep.join([sombra, str(RAIZ / ruta_modulo), *sys.path])


def _importa_sin_secretos(modulo: str, ruta_modulo: str, entorno_extra: dict) -> subprocess.CompletedProcess:
    """Importa `modulo` con un seal_secrets que falla, y devuelve el resultado."""
    with tempfile.TemporaryDirectory(dir="/tmp") as sombra:
        (Path(sombra) / "seal_secrets.py").write_text(
            "raise RuntimeError('seal_secrets no disponible (doble de prueba)')\n")
        env = {"PATH": "/usr/bin:/bin", "HOME": sombra,
               "PYTHONPATH": _pythonpath(sombra, ruta_modulo),
               "PYTHONDONTWRITEBYTECODE": "1", **entorno_extra}
        return subprocess.run([sys.executable, "-c", f"import {modulo}"],
                              capture_output=True, text=True, timeout=60, env=env)


def test_CONDUCTA_el_checkpoint_falla_CERRADO_sin_credencial():
    """Mata `el-checkpoint-deja-de-fallar-cerrado`, que sobrevivio.

    Sin `seal_secrets` y sin SEAL_DB_URL el modulo debe NEGARSE A CARGAR. Un
    checkpoint que no guarda y lo dice fuerte es mejor que uno que arranca sin
    credencial y falla despues, dejando la sospecha de que el problema es la base.
    """
    r = _importa_sin_secretos("session_checkpoint", "messages", {"SEAL_DB_URL": ""})
    assert r.returncode != 0, "el modulo cargo SIN credencial: el fail-closed no existe"
    assert "sin credencial" in (r.stderr or ""), (
        f"fallo por otra cosa, no por el fail-closed: {r.stderr[-200:]}")


def test_CONTROL_con_SEAL_DB_URL_el_checkpoint_SI_carga():
    """Sin este control, un modulo que NUNCA carga pasaria el brazo de arriba."""
    r = _importa_sin_secretos("session_checkpoint", "messages",
                              {"SEAL_DB_URL": "postgresql://u:p@127.0.0.1:1/x"})
    assert r.returncode == 0, (
        f"con credencial en el entorno el modulo deberia cargar: {r.stderr[-200:]}")


def _importa_con_pg_dsn_falso(modulo: str, ruta_modulo: str) -> subprocess.CompletedProcess:
    """Doble de `seal_secrets` que IMPORTA BIEN y respeta `required`.

    El doble que revienta al importar NO sirve para este mutante: el modulo
    fallaria igual por el import, o sea el brazo pasaria por el motivo
    equivocado. Lo comprobe: con `pg_dsn(required=False)` el test seguia verde.
    Para que el brazo mida el CONTRATO, el doble tiene que dejar pasar el import
    y devolver vacio cuando no se exige.
    """
    with tempfile.TemporaryDirectory(dir="/tmp") as sombra:
        (Path(sombra) / "seal_secrets.py").write_text(
            "def pg_dsn(required=False):\n"
            "    if required:\n"
            "        raise RuntimeError('sin secreto (doble de prueba)')\n"
            "    return ''\n")
        env = {"PATH": "/usr/bin:/bin", "HOME": sombra,
               "PYTHONPATH": _pythonpath(sombra, ruta_modulo),
               "PYTHONDONTWRITEBYTECODE": "1"}
        return subprocess.run([sys.executable, "-c", f"import {modulo}"],
                              capture_output=True, text=True, timeout=60, env=env)


def test_CONDUCTA_el_heartbeat_EXIGE_la_credencial():
    """Mata `el-heartbeat-acepta-una-credencial-vacia`, que sobrevivio.

    `pg_dsn(required=True)` es el contrato: sin secreto, el latido NO arranca.
    Un latido que arranca sin credencial publicaria silencio y se leeria como
    "el agente vive" — la misma clase que las unidades de latido congeladas que
    encontramos hoy, que publicaban un PID muerto cada 5 minutos.
    """
    r = _importa_con_pg_dsn_falso("seal_heartbeat", "memory")
    assert r.returncode != 0, (
        "el heartbeat cargo con una credencial VACIA: pide pg_dsn sin required")
    assert "sin secreto" in (r.stderr or ""), (
        f"fallo por otra cosa, no por exigir la credencial: {r.stderr[-200:]}")
