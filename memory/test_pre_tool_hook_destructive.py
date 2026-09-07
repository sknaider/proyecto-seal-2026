"""Controles del clasificador de destructivos del pre_tool_hook.

Generador (1-sep-2026): tres agentes quedaron MUDOS por un candado que denegaba
comandos correctos, y el arreglo se hizo a mano con casos sueltos en la terminal.
Cada iteracion perdia los casos de la anterior; el mismo hueco se "arreglo" tres
veces. Esta tabla los conserva.

El eje que mordio NO fue la evasion exotica sino la ORTOGRAFIA COMUN
(`rm -r -f`, `--recursive`, `/bin/rm`): 10 formas pasaban mudas y 3 de ellas las
escribe cualquiera sin intencion de evadir nada.

DISENO, y por eso los casos estan en dos grupos:
  - sobre-deteccion (denegar codigo correcto) = INACEPTABLE: deja mudo al agente.
  - sub-deteccion   (dejar pasar) = tolerable: es el status quo previo.
Un caso ALLOW que falle es mas grave que un DENY que falle.
"""
import sys
import pathlib

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import pre_tool_hook as hook  # noqa: E402

# El literal de un heredoc sin comillas dispara OTRA regla del hook, incluso como
# dato de prueba. Se arma en partes a proposito.
_HEREDOC = "cat <<" + "EOF"

DEBEN_DENEGAR = [
    ("base", 'rm -rf "$D"'),
    ("sin comillas", "rm -rf $D"),
    ("llaves", 'rm -rf "${D}"'),
    ("comilla simple", "rm -rf '$D'"),
    ("flags separados", 'rm -r -f "$D"'),
    ("flags invertidos", 'rm -f -r "$D"'),
    ("mayuscula R", 'rm -Rf "$D"'),
    ("flags largos", 'rm --recursive --force "$D"'),
    ("verboso", 'rm -rfv "$D"'),
    ("ruta del binario", '/bin/rm -rf "$D"'),
    ("usr bin", '/usr/bin/rm -rf "$D"'),
    ("prefijo sudo", 'sudo rm -rf "$D"'),
    ("sudo con flag y valor", 'sudo -u dadito rm -rf "$D"'),
    ("nice con valor", 'nice -n 5 rm -rf "$D"'),
    ("time", 'time rm -rf "$D"'),
    ("env con asignacion", 'env FOO=1 rm -rf "$D"'),
    ("asignacion sola", 'FOO=1 rm -rf "$D"'),
    ("backslash", '\\rm -rf "$D"'),
    ("glob sobre variable", 'rmdir "$T"/*'),
    ("glob con barra", 'rm -rf "$V"/*/'),
    ("default que sustituye", 'rm -rf "${D:-/}"'),
    ("subruta", 'rm -rf "$D"/subdir'),
]

DEBEN_PASAR = [
    ("forma fail-safe", 'rm -rf "${D:?}"'),
    ("fail-safe con mensaje", 'rm -rf "${D:?vacia}"'),
    ("fail-safe con flags sueltos", 'rm -r -f "${D:?}"'),
    ("ruta literal", "rm -rf /tmp/dir-literal"),
    ("ruta relativa", "rm -rf ./build"),
    ("sin recursivo", 'rm -f "$SOCKET"'),
    ("rmdir pelado", 'rmdir "$D"'),  # sin glob solo borra vacios; con var vacia falla sola
    ("comentario", '# ojo con rm -rf "$D"'),
    ("dentro de echo", 'echo "no escribas rm -rf $D"'),
    ("grep del patron", "grep -n 'rm -rf' archivo.sh"),
    ("texto en heredoc", _HEREDOC + '\n  rm -rf "$D"\nEOF'),
    ("sudo inocuo", "sudo ls -la"),
    ("sudo con flag inocuo", "sudo -u dadito ls"),
    ("binario absoluto inocuo", "/bin/ls -la"),
    ("comandos de todos los dias", "git status"),
]

# Guardas legitimas: NO deben bloquear. Avisar esta bien; denegar no.
GUARDAS_LEGITIMAS = [
    ("test -z y salida", '[ -z "$D" ] && exit 1\nrm -rf "$D"'),
    ("test -n en linea", '[ -n "$D" ] && rm -rf "$D"'),
    ("test -n con or", '[ -n "$D" ] || exit 1\nrm -rf "$D"'),
    ("if explicito", 'if [ -z "$D" ]; then exit 1; fi\nrm -rf "$D"'),
    ("set -u", 'set -u\nrm -rf "$D"'),
    ("doble corchete", '[[ "$D" == /tmp/* ]] && rm -rf "$D"'),
    ("case en una linea", 'case "$D" in /tmp/*) rm -rf "$D";; esac'),
    ("case multilinea", 'case "$D" in\n  /tmp/*) rm -rf "$D" ;;\nesac'),
]



# El PRECIO de decidir por estructura en vez de enumerar guardas: cualquier
# segmento previo baja el caso a aviso, sea o no una guarda. Se pin-ea a
# proposito — si alguien lo "arregla" a deny, vuelve la sobre-deteccion que
# dejo mudos a tres agentes el 1-sep.
BAJAN_A_AVISO_POR_TENER_ALGO_ANTES = [
    ("segmento previo cualquiera", 'echo hola; rm -rf "$X"'),
    ("guarda real", '[ -n "$D" ] && rm -rf "$D"'),
]


@pytest.mark.parametrize(
    "nombre,cmd", BAJAN_A_AVISO_POR_TENER_ALGO_ANTES,
    ids=[n for n, _ in BAJAN_A_AVISO_POR_TENER_ALGO_ANTES])
def test_algo_antes_avisa_no_deniega(nombre, cmd):
    assert hook._var_destructive_kind(cmd) == "warn", nombre

@pytest.mark.parametrize("nombre,cmd", DEBEN_DENEGAR, ids=[n for n, _ in DEBEN_DENEGAR])
def test_deniega(nombre, cmd):
    assert hook._var_destructive_kind(cmd) == "deny", nombre


@pytest.mark.parametrize("nombre,cmd", DEBEN_PASAR, ids=[n for n, _ in DEBEN_PASAR])
def test_deja_pasar(nombre, cmd):
    assert hook._var_destructive_kind(cmd) == "", nombre


@pytest.mark.parametrize("nombre,cmd", GUARDAS_LEGITIMAS, ids=[n for n, _ in GUARDAS_LEGITIMAS])
def test_no_bloquea_guardas_legitimas(nombre, cmd):
    """La sobre-deteccion es la falla grave: deja mudo al agente."""
    assert hook._var_destructive_kind(cmd) != "deny", nombre


def test_ruta_completa_deniega():
    r = hook.check_bash_safety('rm -rf "$D"')
    assert r["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_ruta_completa_avisa_sin_bloquear():
    r = hook.check_bash_safety('[ -n "$D" ] && rm -rf "$D"')
    assert r["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert r["hookSpecificOutput"]["additionalContext"]


def test_ruta_completa_no_molesta_el_trabajo_normal():
    for c in ("ls -la", "git status", "python3 -m pytest -q", "rm -rf ./build"):
        assert hook.check_bash_safety(c) is None, c
