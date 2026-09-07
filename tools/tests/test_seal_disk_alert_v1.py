"""Alerta de disco y limpieza de arenas — prueba la DECISION, no la publicacion.

POR QUE ASI: el 7-sep probe publicando y salio mal dos veces. Primero mi "control
positivo" no publico nada -la idempotencia lo deduplico- y lei el exit 0 como
exito. Despues publique al canal una alerta CRITICA FALSA con los campos cruzados
("uso 632G% · libres 83") que JARVIS y NEXUS tuvieron que desmentirle a William.
Un test que publica no es un test: es un incidente. Por eso todo va con
SEAL_DISK_DRYRUN=1 y umbrales forzados por entorno.
"""
import os
import subprocess
import pathlib

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[2]
ALERTA = RAIZ / "tools" / "seal_disk_alert.sh"
ARENA = RAIZ / "tools" / "seal_arena.sh"
ESTADO = pathlib.Path("/tmp/seal_disk_alert_last")


def corre(script, *args, **env):
    e = dict(os.environ, SEAL_DISK_DRYRUN="1", **env)
    return subprocess.run(["bash", str(script), *args], capture_output=True,
                          text=True, env=e, timeout=60)


@pytest.fixture(autouse=True)
def _sin_estado_previo():
    ESTADO.unlink(missing_ok=True)
    yield
    ESTADO.unlink(missing_ok=True)


# ── qa_positive ────────────────────────────────────────────────────────────
def test_qa_positive_poco_espacio_dispara_aviso():
    r = corre(ALERTA, SEAL_DISK_WARN_GB="999999")
    assert "nivel=aviso" in r.stdout, r.stdout


def test_qa_positive_muy_poco_espacio_dispara_critico():
    r = corre(ALERTA, SEAL_DISK_CRIT_GB="999999")
    assert "nivel=critico" in r.stdout, r.stdout


def test_qa_positive_los_campos_en_su_lugar():
    """EL test que habria cazado el bug publicado: 'uso 632G% · libres 83'."""
    import re
    r = corre(ALERTA, SEAL_DISK_WARN_GB="999999")
    assert re.search(r"libres \d+G +· +uso \d+%", r.stdout), r.stdout


# ── qa_negative ────────────────────────────────────────────────────────────
def test_qa_negative_disco_sano_NO_dispara():
    """El umbral real (100 G) con el disco actual: silencio."""
    r = corre(ALERTA)
    assert r.stdout.strip() == "", r.stdout


def test_qa_negative_no_repite_el_mismo_nivel():
    corre(ALERTA, SEAL_DISK_WARN_GB="999999")
    r = corre(ALERTA, SEAL_DISK_WARN_GB="999999")
    assert r.stdout.strip() == "", "una alerta que se repite es ruido"


def test_qa_negative_la_arena_NO_copia_el_modelo():
    """`cp -r memory` traia 95 G de GGUF y lleno el disco. La arena debe excluirlo."""
    r = subprocess.run(["bash", str(ARENA), "new", "memory"],
                       capture_output=True, text=True, cwd=RAIZ, timeout=180)
    ruta = r.stdout.strip()
    arena = pathlib.Path(ruta)
    assert arena.is_dir() and any(arena.rglob("*.py")), f"arena vacia: {ruta!r} {r.stderr[:200]}"
    try:
        assert not (arena / "memory" / "minimax-m2.5").exists()
    finally:
        # El borrado va por el helper: la guarda vive AHI, no aca (JARVIS 01:25).
        # Antes este test armaba su propio `find <ruta> -mindepth 1 -delete` y con
        # stdout vacio Path("") == "." borraba el directorio actual. Que el llamador
        # se acuerde del guard no es garantia; que el unico camino pase por `drop`, si.
        subprocess.run(["bash", str(ARENA), "drop", ruta], timeout=120)


# ── qa_control: el test puede fallar ───────────────────────────────────────
def test_qa_control_el_test_NO_es_vacuo():
    """Si el script no existiera o no imprimiera nada, los positivos pasarian
    solos. Este brazo comprueba que la asercion distingue: una salida SIN el
    formato correcto debe hacer fallar el patron que usa test_los_campos."""
    import re
    roto = "uso 632G% · libres 83"     # la alerta que publique de verdad
    bueno = "libres 632G  ·  uso 83%"
    patron = r"libres \d+G +· +uso \d+%"
    assert not re.search(patron, roto), "el patron aceptaria la version ROTA"
    assert re.search(patron, bueno), "el patron rechazaria la version BUENA"


# ── qa_negative: la guarda del borrado ─────────────────────────────────────
@pytest.mark.parametrize("ruta,motivo", [
    ("", "ruta vacia -> Path('') es '.' y borraria el cwd"),
    (".", "ruta relativa"),
    ("relativa/x", "relativa con subdir"),
    ("/home/dadito", "ruta real fuera de /tmp"),
    ("/tmp/otra-cosa", "en /tmp pero no es una arena"),
])
def test_qa_negative_drop_rechaza_rutas_peligrosas(ruta, motivo):
    """`drop` es el UNICO camino a borrar una arena; debe negarse a todo lo demas."""
    r = subprocess.run(["bash", str(ARENA), "drop", ruta],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode != 0, f"drop ACEPTO {ruta!r} ({motivo})"
    assert "no borro nada" in (r.stderr + r.stdout), r.stderr
