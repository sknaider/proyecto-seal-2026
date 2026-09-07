"""La defensa en profundidad de `seal_arena.sh drop` no puede caer con UNA linea.

POR QUE EXISTE — 7-sep-2026 01:42:53. Un mutante mio (NEXUS) quito la unica guarda
de `drop` y el test negativo de ALICE, que le pasa `/home/dadito` para comprobar
que la RECHACE, ejecuto `find /home/dadito -mindepth 1 -delete` de verdad. Se
perdio el home: el repo, los venv, las unidades de systemd, `~/GTL`, `~/Documentos`.

El test de ALICE hizo bien su trabajo: reporto `2 failed`. **El defecto era que
comprobar el rechazo obligaba a LLAMAR a la operacion destructiva, y una sola
linea la separaba del borrado.**

QUE FIJA ESTE TEST, y es lo unico que impide que se repita:
  1. que existan DOS guardas independientes, no una;
  2. que la segunda siga bloqueando cuando la primera se quita (el mutante real);
  3. que exista el interruptor de efecto (`SEAL_ARENA_DRYRUN`) para poder probar
     el rechazo SIN que el borrado pueda ocurrir;
  4. el CONTROL: que una arena legitima SI se borre — sin esto, "arreglarlo"
     rechazando todo pasaria en verde y no protegeria nada.

SEGURIDAD DE ESTE TEST: nunca toca una ruta real. La "victima" es un directorio
creado por `tmp_path` de pytest, y todas las llamadas van con SEAL_ARENA_DRYRUN=1
como segunda red. Un test de limpieza es una operacion destructiva con otra ropa.
"""
import os
import pathlib
import subprocess

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[1]
ARENA = RAIZ / "tools" / "seal_arena.sh"
# La linea COMPLETA: la cadena suelta `/tmp/seal-arena-*)` aparece tambien dentro
# del mensaje de error, y un ancla que matchea 2 veces no identifica la guarda.
GUARDA_1 = "      /tmp/seal-arena-*) : ;;"


def corre(script, *args):
    """Siempre con el interruptor puesto: este test NO puede borrar nada."""
    entorno = dict(os.environ, SEAL_ARENA_DRYRUN="1")
    return subprocess.run(["bash", str(script), "drop", *args],
                          capture_output=True, text=True, env=entorno, timeout=60)


def _sin_la_primera_guarda(destino: pathlib.Path) -> pathlib.Path:
    """Reproduce el mutante EXACTO del 7-sep sobre una COPIA, nunca sobre el vivo."""
    texto = ARENA.read_text()
    assert texto.count(GUARDA_1) == 1, "cambio la forma de la primera guarda"
    copia = destino / "arena_mutada.sh"
    copia.write_text(texto.replace(GUARDA_1, "/*)"))
    return copia


def test_hay_DOS_guardas_no_una(tmp_path):
    """Si alguien colapsa las dos en una, este test cae y avisa por que.

    OJO: comprueba PRESENCIA, y eso NO alcanza — `realpath` aparece tambien en
    los comentarios, asi que un mutante que lo reemplaza por `real="$ruta"`
    sobrevive a esta asercion. Lo medi: sobrevivio. El brazo que SI discrimina
    es `test_la_SEGUNDA_guarda_se_EJERCITA`, abajo. Este queda como senal barata
    de que alguien toco la estructura.
    """
    texto = ARENA.read_text()
    assert GUARDA_1 in texto, "falta la guarda por prefijo"
    assert "realpath" in texto, "falta la segunda guarda, la que resuelve la ruta"


def test_la_SEGUNDA_guarda_se_EJERCITA(tmp_path):
    """El caso que SOLO la segunda guarda puede atrapar.

    Un symlink LLAMADO /tmp/seal-arena-* pasa la primera guarda (compara el
    TEXTO del nombre) y apunta fuera. Solo resolver la ruta real lo detiene.
    Sin este brazo, quitar la segunda guarda pasaba con 8 verdes: medido.
    """
    afuera = tmp_path / "no_es_arena"
    afuera.mkdir()
    enlace = pathlib.Path("/tmp") / f"seal-arena-{os.getpid()}link"
    enlace.symlink_to(afuera)
    try:
        r = corre(ARENA, str(enlace))
        assert r.returncode != 0, "paso la guarda por nombre y nadie resolvio la ruta real"
        assert "FUERA" in (r.stdout + r.stderr) or "no borro nada" in (r.stdout + r.stderr)
    finally:
        enlace.unlink()


def test_EL_MUTANTE_DEL_7SEP_ya_no_borra(tmp_path):
    """El caso exacto que destruyo el home, contra una victima ficticia."""
    victima = tmp_path / "home_falso"
    (victima / "datos").mkdir(parents=True)
    (victima / "datos" / "importante.txt").write_text("no me borres")

    mutada = _sin_la_primera_guarda(tmp_path)
    r = corre(mutada, str(victima))

    assert r.returncode != 0, "con la guarda 1 quitada, la 2 DEBE frenar"
    assert (victima / "datos" / "importante.txt").read_text() == "no me borres"


def test_existe_el_interruptor_de_efecto():
    """Sin el, la unica forma de probar el rechazo es llamar al borrado real."""
    assert "SEAL_ARENA_DRYRUN" in ARENA.read_text()


@pytest.mark.parametrize("ruta", ["", ".", "relativa/x", "/tmp/otra-cosa"])
def test_rutas_que_no_son_arena_se_rechazan(ruta):
    r = corre(ARENA, ruta)
    assert r.returncode != 0, f"drop ACEPTO {ruta!r}"
    assert "no borro nada" in (r.stdout + r.stderr)


def test_CONTROL_una_arena_legitima_NO_se_rechaza(tmp_path, monkeypatch):
    """Sin este brazo, rechazar TODO pasaria en verde y no protegeria nada."""
    arena = pathlib.Path("/tmp") / f"seal-arena-{os.getpid()}test"
    arena.mkdir(exist_ok=True)
    try:
        r = corre(ARENA, str(arena))
        assert r.returncode == 0, f"rechazo una arena valida: {r.stderr}"
        assert "DRYRUN" in r.stdout, "el interruptor no freno el borrado"
        assert arena.exists(), "DRYRUN no debe borrar"
    finally:
        arena.rmdir()
