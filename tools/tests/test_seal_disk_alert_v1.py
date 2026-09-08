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
def _fake_disk_commands(tmp_path, monkeypatch):
    """Reemplaza df y du con versiones sinteticas para evitar lecturas reales del disco.

    Sin esto, tests que disparan nivel=aviso/critico ejecutan 'du -xh --max-depth=1 /'
    (seal_disk_alert.sh:54) que puede tardar minutos en un disco de 4 TB y colgar
    la suite global.  Los datos sinteticos: 500 GB libres, 30% uso.
    """
    bindir = tmp_path / "fakebin"
    bindir.mkdir()

    df_script = bindir / "df"
    df_script.write_text("""\
#!/usr/bin/env bash
# Sintetico: 500 GB libres, 30% uso — formato real de cada variante de df
[[ "$*" == *"--output=pcent"* ]] && { printf 'Use%%\\n  30%%\\n'; exit 0; }
[[ "$*" == *"-BG"* ]]           && { printf 'Avail\\n  500G\\n'; exit 0; }
[[ "$*" == *"-h"* ]]            && { printf 'Avail\\n  500G\\n'; exit 0; }
exec /usr/bin/df "$@"
""")
    df_script.chmod(0o755)

    du_script = bindir / "du"
    du_script.write_text("""\
#!/usr/bin/env bash
# Sintetico: salida instantanea que cubre el formato de "Lo que mas pesa"
# El script hace: du ... | sort -rh | sed -n '2,5p' | awk ...
# sort -rh espera tamanos con sufijo (G, M, etc.)
printf '2900G\\t/\\n'
printf '1200G\\t/home\\n'
printf '800G\\t/var\\n'
printf '650G\\t/opt\\n'
printf '250G\\t/usr\\n'
""")
    du_script.chmod(0o755)

    monkeypatch.setenv("PATH", f"{bindir}:{os.environ.get('PATH', '/usr/bin:/bin')}")


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
    # La unidad la elige `df -h` segun el disco: hoy hay 3,1T libres y el patron exigia "G",
    # asi que el brazo se puso rojo por el TAMANO DEL DISCO, no por un defecto de formato.
    # Se acepta cualquier unidad y el decimal con coma; lo que se afirma sigue siendo el ORDEN
    # y la FORMA de los dos campos, que es lo que estaba roto cuando publique "uso 632G% · libres 83".
    assert re.search(r"libres \d+(?:[.,]\d+)?[KMGTP]? +· +uso \d+%", r.stdout), r.stdout


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
    # NUNCA una ruta REAL aca. El 7-sep-2026 01:42:53 este caso decia
    # "/home/dadito" y borro 2,7 TB: el mutante M5 quito la guarda de `drop`
    # y este test le entrego el home de verdad a un `find -delete` sin freno.
    #
    # Un test negativo que usa una ruta REAL y peligrosa como entrada se
    # convierte en un arma en el instante en que se quita la guarda que lo
    # protege. Y la prueba de mutacion quita guardas: es su trabajo.
    #
    # /tmp/falso-home-no-existe prueba EXACTAMENTE lo mismo -que `drop` rechaza
    # una ruta absoluta que no es una arena- y no puede destruir nada.
    ("/tmp/falso-home-no-existe", "ruta absoluta que no es una arena"),
    ("/tmp/otra-cosa", "en /tmp pero no es una arena"),
])
def test_qa_negative_drop_rechaza_rutas_peligrosas(ruta, motivo):
    """`drop` es el UNICO camino a borrar una arena; debe negarse a todo lo demas."""
    r = subprocess.run(["bash", str(ARENA), "drop", ruta],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode != 0, f"drop ACEPTO {ruta!r} ({motivo})"
    assert "no borro nada" in (r.stderr + r.stdout), r.stderr


def test_qa_negative_una_variable_SEAL_DISK_desconocida_FALLA_ruidosa(tmp_path):
    """Regresión del error que publicó una alerta falsa al canal el 7-sep-2026 18:38.

    Escribí `SEAL_DISK_DRY_RUN=1` (la buena es `SEAL_DISK_DRYRUN`) y el modo prueba no
    existió por un guion bajo: el script publicó de verdad. Un interruptor de seguridad
    que se apaga solo por escribir mal su nombre no es un interruptor, es una trampa.
    """
    import os, subprocess
    env = dict(os.environ, SEAL_DISK_DRY_RUN="1")
    r = subprocess.run(["bash", str(ALERTA)], capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 2, f"deberia morir, no seguir: rc={r.returncode}\n{r.stdout[:200]}"
    assert "variable desconocida SEAL_DISK_DRY_RUN" in r.stderr
    assert "SEAL_DISK_DRYRUN" in r.stderr, "el error debe decir cuál es la forma correcta"


def test_qa_control_las_variables_VALIDAS_siguen_funcionando(tmp_path):
    """Control no vacuo: si la guarda matara también los nombres buenos, el script sería
    inusable y el brazo de arriba pasaría igual."""
    import os, subprocess
    env = dict(os.environ, SEAL_DISK_DRYRUN="1", SEAL_DISK_WARN_GB="999999", SEAL_DISK_CRIT_GB="1")
    r = subprocess.run(["bash", str(ALERTA)], capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0, r.stderr[:200]
    assert "DRYRUN" in r.stdout


def test_qa_positive_el_titulo_nombra_la_CONDICION_no_una_categoria(tmp_path):
    """JARVIS, 7-sep 18:39: «tu alerta "Disco alto" salió con 3,1 T libres: el título miente».

    Un aviso cuyo título no se corresponde con lo medido enseña a ignorarlo.
    """
    import os, re, subprocess
    env = dict(os.environ, SEAL_DISK_DRYRUN="1", SEAL_DISK_WARN_GB="999999")
    r = subprocess.run(["bash", str(ALERTA)], capture_output=True, text=True, env=env, timeout=120)
    assert "Espacio libre bajo" in r.stdout, r.stdout[:200]
    assert re.search(r"por debajo del umbral de \d+ GB", r.stdout), "el título debe citar el umbral"
    assert "Disco alto" not in r.stdout, "el título viejo nombraba una categoría, no la condición"


def test_qa_positive_el_titulo_CRITICO_tambien_cita_su_umbral(tmp_path):
    """Hueco que encontró NEXUS mutando (M5, 7-sep 18:56): `printf` tiene DOS ramas y mi
    brazo anterior sólo ejercitaba la de AVISO. Quitar el umbral del mensaje CRÍTICO
    cambiaba la salida visible y mis 15 brazos no se enteraban.

    La ironía es que la rama sin cubrir era la crítica: la que se lee cuando el disco
    de verdad se está llenando.
    """
    import os, re, subprocess
    env = dict(os.environ, SEAL_DISK_DRYRUN="1",
               SEAL_DISK_WARN_GB="999999", SEAL_DISK_CRIT_GB="999999")
    r = subprocess.run(["bash", str(ALERTA)], capture_output=True, text=True, env=env, timeout=120)
    assert "CRITICO" in r.stdout, f"no se alcanzó la rama crítica: {r.stdout[:200]}"
    assert re.search(r"por debajo del umbral de \d+ GB", r.stdout), (
        "el título CRÍTICO debe citar su umbral, igual que el de aviso")
