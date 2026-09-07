"""Ninguna unidad systemd puede llevar una credencial EN LINEA.

POR QUE EXISTE: el 7-sep, preparando el versionado de unidades (carril 4), medi
que 10 de ellas llevaban un DSN con contrasena embebida en una linea
`Environment=`. El repo se empuja a GitHub a diario: versionarlas asi habria
publicado credenciales en un repositorio remoto.

El fix fue mover cada valor a `~/.config/seal/env/<unidad>.env` con permisos 600
y dejar `EnvironmentFile=` en la unidad. **Pero un fix sin mecanismo se deshace
solo:** la proxima unidad que alguien escriba con la credencial en linea no la
va a notar nadie. Un `.gitignore` no lo impide; este test si.

ALCANCE, declarado: revisa las unidades VERSIONADAS en el repo (systemd/user/),
que son las que pueden llegar a GitHub. Las unidades instaladas fuera del repo
las cubre el mismo chequeo aplicado a `~/.config/systemd/user` — se expone como
funcion para que un vigilante pueda usarla, pero el test fija lo versionado.
"""
import pathlib
import re

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[1]
VERSIONADAS = RAIZ / "systemd" / "user"

# Un DSN con contrasena embebida, o una variable de secreto con valor sustantivo.
DSN_CON_CLAVE = re.compile(r"postgres(?:ql)?://[^:@\s]+:[^@\s]+@")
VAR_SECRETA = re.compile(r"^Environment=[\"']?\w*(PASSWORD|PASS|SECRET|_KEY|TOKEN)=(.+)$")


def secretos_en_linea(texto: str) -> list[str]:
    """Devuelve las lineas con una credencial embebida. Nunca su valor completo."""
    fuera = []
    for linea in texto.splitlines():
        if not linea.startswith("Environment="):
            continue
        if DSN_CON_CLAVE.search(linea):
            fuera.append(linea.split("=", 1)[0] + "= <DSN con clave>")
            continue
        m = VAR_SECRETA.match(linea)
        # Una ruta o un directorio no son un secreto: SEAL_TOKENS_DIR=%t/seal
        if m and len(m.group(2)) > 12 and not m.group(2).startswith(("%", "/")):
            fuera.append(m.group(1) + "= <valor largo>")
    return fuera


def _unidades():
    if not VERSIONADAS.is_dir():
        return []
    return sorted(p for p in VERSIONADAS.iterdir()
                  if p.suffix in {".service", ".timer"} and p.is_file())


@pytest.mark.parametrize("unidad", _unidades(), ids=lambda p: p.name)
def test_ninguna_unidad_versionada_lleva_credencial(unidad):
    hallazgos = secretos_en_linea(unidad.read_text())
    assert not hallazgos, (
        f"{unidad.name} lleva una credencial en linea: {hallazgos}. "
        "El repo va a GitHub: movela a ~/.config/seal/env/<unidad>.env (chmod 600) "
        "y deja EnvironmentFile= en la unidad."
    )


# ── El detector tiene que DISCRIMINAR, no decir que si a todo ────────────────
def test_DETECTA_un_dsn_con_clave():
    assert secretos_en_linea('Environment=SEAL_DB_URL=postgresql://rol:clave@host/db')


def test_DETECTA_una_variable_de_secreto_con_valor():
    assert secretos_en_linea('Environment=PG_PASSWORD=unvalorsuficientementelargo')


@pytest.mark.parametrize("linea", [
    "Environment=SEAL_TOKENS_DIR=%t/seal",                 # una ruta, no un secreto
    "Environment=SEAL_MCP_AGENT_CRED_DIR=%h/.config/seal", # un directorio
    "Environment=PYTHONUNBUFFERED=1",
    "Environment=SEAL_DB_URL=postgresql://rol@host/db",    # DSN SIN clave
    "ExecStart=/usr/bin/python3 x.py --token abc",         # no es Environment=
])
def test_CONTROL_lo_que_NO_es_secreto_no_dispara(linea):
    """Sin este brazo, un detector que marca todo pasaria en verde y bloquearia
    el versionado entero sin proteger nada."""
    assert not secretos_en_linea(linea)
