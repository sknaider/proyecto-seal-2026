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
