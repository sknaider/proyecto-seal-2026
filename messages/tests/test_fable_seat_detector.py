"""Contrato del detector de asiento de FABLE (messages/fable_heartbeat_update.sh).

POR QUE EXISTE: el writer decide si el equipo ve vivo a FABLE. El 29-ago se
reescribio entero —de `ps -o args=` con substring a /proc/PID/cmdline por argv—
y no tenia ninguna prueba. Cada caso de aca MATA un defecto que ese dia estuvo
en produccion; si alguno deja de fallar al revertir el fix, el test no sirve.

El writer expone FABLE_PROC_ROOT justamente para poder inyectar un /proc
sintetico: sin esa costura estas ramas no se pueden ejercer y quedan
verdes-sin-probar.
"""
import json
import os
import shutil
import pathlib
import shlex
import subprocess
import sys
import tempfile
import pytest

REPO = "/home/dadito/IA/proyecto-seal"
WRITER = os.environ.get("FABLE_WRITER_UNDER_TEST", f"{REPO}/messages/fable_heartbeat_update.sh")
# ^ costura para VALIDAR el test con mutantes: sin poder apuntarlo a una copia
#   defectuosa, un verde de esta suite no prueba que discrimine.


def _proc(tmp, pid, comm, argv, seal_agent="FABLE"):
    """seal_agent=None omite el environ (proceso cuyo environ no se puede leer)."""
    d = os.path.join(tmp, "proc", str(pid))
    os.makedirs(d, exist_ok=True)
    if seal_agent is not None:
        with open(os.path.join(d, "environ"), "wb") as fh:
            fh.write(f"SEAL_AGENT={seal_agent}\0PATH=/usr/bin\0".encode())
    with open(os.path.join(d, "comm"), "w") as fh:
        fh.write(comm + "\n")
    with open(os.path.join(d, "cmdline"), "wb") as fh:
        fh.write(b"\0".join(a.encode() for a in argv) + b"\0")
    return os.path.join(tmp, "proc")


PROD = f"{REPO}/messages/fable_claude_heartbeat.json"


def _run(proc_root, tmp, extra_env=None, con_stderr=False):
    """Corre el writer aislado y PRUEBA que el aislamiento aisló.

    Sin este control la suite pasaria igual escribiendo el latido REAL: el
    29-ago 06:32 un test mio con `MESSAGES_DIR=` --que el script reasigna desde
    $HOME-- publico alive=false en produccion durante 17 s. La seguridad de este
    test depende de HOME, asi que HOME se verifica, no se supone.
    """
    home = os.path.join(tmp, "home")
    os.makedirs(os.path.join(home, "IA/proyecto-seal/messages"), exist_ok=True)
    antes = os.stat(PROD).st_mtime_ns if os.path.exists(PROD) else None
    env = {**os.environ, "HOME": home, "FABLE_PROC_ROOT": proc_root}
    if extra_env:
        env.update(extra_env)
    r = subprocess.run(["/bin/bash", WRITER], env=env, capture_output=True,
                       timeout=30, text=True)
    out = os.path.join(home, "IA/proyecto-seal/messages/fable_claude_heartbeat.json")
    assert os.path.exists(out), "el writer no escribio en el HOME aislado"
    despues = os.stat(PROD).st_mtime_ns if os.path.exists(PROD) else None
    assert antes == despues, (
        "EL AISLAMIENTO FALLO: el latido de produccion fue reescrito por el test"
    )
    with open(out) as fh:
        d = json.load(fh)
    return (d, r.stderr) if con_stderr else d


@pytest.fixture()
def tmp():
    d = tempfile.mkdtemp(prefix="fable-seat-")
    assert d.startswith("/tmp/")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_asiento_unico_presente(tmp):
    root = _proc(tmp, 900, "claude", ["claude", "--name", "FABLE — Profesor Neutro [Opus5]"])
    d = _run(root, tmp)
    assert d["runtime_detection_status"] == "present_unique"
    assert d["alive"] is True and d["process_pid"] == "900"


def test_mencion_en_argumento_ajeno_no_es_mi_asiento(tmp):
    """MATA el defecto del 29-ago: publicaba el PID de NEXUS como propio."""
    root = _proc(tmp, 901, "claude",
                 ["claude", "--name", "NEXUS — Team SEAL", "-p", "revisar --name FABLE — Profesor"])
    d = _run(root, tmp)
    assert d["runtime_detection_status"] == "absent"
    assert d["process_pid"] == "0"


def test_clon_con_guion_no_es_el_principal(tmp):
    root = _proc(tmp, 902, "claude", ["claude", "--name", "FABLE-u103"])
    assert _run(root, tmp)["runtime_detection_status"] == "absent"


@pytest.mark.parametrize("sep", [" ", "\t", "—", " ", " "])
def test_separadores_de_la_autoridad(tmp, sep):
    """La autoridad usa `\\s` de Python: U+00A0 y U+202F entran."""
    root = _proc(tmp, 903, "claude", ["claude", "--name", f"FABLE{sep}Profesor"])
    assert _run(root, tmp)["runtime_detection_status"] == "present_unique"


def test_forma_name_igual(tmp):
    root = _proc(tmp, 904, "claude", ["claude", "--name=FABLE — Profesor"])
    assert _run(root, tmp)["runtime_detection_status"] == "present_unique"


@pytest.mark.parametrize("extra", [["--output-format", "stream-json"],
                                   ["--output-format=stream-json"]])
def test_worker_de_streaming_excluido(tmp, extra):
    root = _proc(tmp, 905, "claude", ["claude", "--name", "FABLE"] + extra)
    assert _run(root, tmp)["runtime_detection_status"] == "absent"


def test_ambiguo_es_presencia_no_ausencia(tmp):
    """present_ambiguous lleva alive=true: n>=2 es medicion CONCLUYENTE de vida."""
    root = _proc(tmp, 906, "claude", ["claude", "--name", "FABLE — X"])
    _proc(tmp, 907, "claude", ["claude", "--name", "FABLE — X"])
    d = _run(root, tmp)
    assert d["runtime_detection_status"] == "present_ambiguous"
    assert d["alive"] is True and d["process_pid"] == "0"


def test_proc_ilegible_no_es_ausencia(tmp):
    d = _run("/ruta/que/no/existe", tmp)
    assert d["runtime_detection_status"] == "indeterminate"
    assert d["alive"] is False


def test_ausencia_real(tmp):
    root = os.path.join(tmp, "proc")
    os.makedirs(root, exist_ok=True)
    d = _run(root, tmp)
    assert d["runtime_detection_status"] == "absent"


def test_otro_agente_no_es_mi_asiento(tmp):
    root = _proc(tmp, 908, "claude", ["claude", "--name", "ALICE — Team SEAL"])
    assert _run(root, tmp)["runtime_detection_status"] == "absent"


def test_comm_distinto_de_claude_no_cuenta(tmp):
    root = _proc(tmp, 909, "bash", ["bash", "-c", "echo --name FABLE — Profesor"])
    assert _run(root, tmp)["runtime_detection_status"] == "absent"


# ---------------------------------------------------------------------------
# ORACULO INYECTADO: LA AUTORIDAD ADJUDICA, NO YO.
#
# Este bloque existe porque el 29-ago le reproche a NEXUS que su corpus de stubs
# "es su propia hipotesis" y despues me lo aplique: los casos de ARRIBA los elijo
# yo Y los valores esperados los escribo yo. La lista de separadores la transcribi
# a mano desde el motor de la autoridad esta manana; si transcribi mal uno, todo
# lo de arriba confirma mi error con 16 verdes.
#
# Aca la respuesta correcta no la pongo yo: la calcula
# tools/seal_agent_runtime_supervisor.py, que es quien decide de verdad que asiento
# se adopta. Mi writer solo tiene que COINCIDIR con el.
import importlib.util

# COSTURA DE COMPLETITUD DEL ORACULO (29-ago, respuesta al limite que declaro ALICE):
# su arm cuenta los predicados de la autoridad que el test INVOCA, y solo puede
# contar los que son FUNCIONES -- 2 de 4. Mutar la autoridad no necesita enumerar
# nada: si le rompo UNA condicion y mi suite sigue VERDE, mi oraculo no la consulta.
# Sirve igual para condiciones inline, que es justo lo que su arm no alcanza.
AUTORIDAD_RUTA = os.environ.get(
    "FABLE_AUTHORITY_UNDER_TEST", f"{REPO}/tools/seal_agent_runtime_supervisor.py")


def _autoridad():
    ruta = AUTORIDAD_RUTA
    spec = importlib.util.spec_from_file_location("seal_authority", ruta)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["seal_authority"] = mod        # el dataclass del modulo lo exige
    spec.loader.exec_module(mod)
    return mod


CANDIDATOS = [
    ["claude", "--name", "FABLE", "--tail"],
    ["claude", "--name", "FABLE—Team SEAL", "--tail"],
    ["claude", "--name", "FABLE Team", "--tail"],
    ["claude", "--name", "FABLE Team", "--tail"],
    ["claude", "--name", "FABLE\tTeam", "--tail"],
    ["claude", "--name", "FABLE-CLONE", "--tail"],
    ["claude", "--name", "FABLE_CLONE", "--tail"],
    ["claude", "--name", "FABLE2", "--tail"],
    ["claude", "--name", "fable", "--tail"],
    ["claude", "--name", '"FABLE"', "--tail"],
    ["claude", "--name", "'FABLE'", "--tail"],
    ["claude", "--name=FABLE", "--tail"],
    ["claude", "--name", "FABLE", "--output-format", "stream-json"],
    ["claude", "--name", "ALICE", "--system-prompt", "se invoca con --name FABLE", "--tail"],
    ["claude", "--name", "ALICE", "--tail"],
]


# El corpus tiene que VARIAR cada condicion, o el oraculo no puede discriminarla.
# Medido 29-ago 15:31 mutando la autoridad: con todos los casos en
# seal_agent="FABLE", romperle la condicion SEAL_AGENT dejaba mi suite en 35 verdes.
# Comparar decisiones completas no alcanza si el corpus no mueve el campo.
CASOS = [(argv, "FABLE") for argv in CANDIDATOS] + [
    (["claude", "--name", "FABLE", "--tail"], "OTRO"),
    (["claude", "--name", "FABLE", "--tail"], "fable"),
    (["claude", "--name", "FABLE", "--tail"], ""),
]


@pytest.mark.parametrize("argv,seal_agent", CASOS,
                         ids=lambda a: "|".join(a[1:3]) if isinstance(a, list) else f"env={a}")
def test_mi_writer_coincide_con_la_autoridad(tmp_path, argv, seal_agent):
    """El veredicto lo da la AUTORIDAD; mi writer solo tiene que igualarlo."""
    auth = _autoridad()
    proc = _proc(str(tmp_path), 4242, "claude", argv, seal_agent=seal_agent)
    # LA DECISION COMPLETA, no dos helpers que elijo yo. Elegir el ALCANCE del
    # oraculo reintroduce mi hipotesis un nivel mas arriba: con
    # _argv_has_agent_name + _argv_is_stream_worker mi suite daba 15/15 verdes
    # mientras mi writer ignoraba SEAL_AGENT, la tercera condicion de la autoridad.
    # PROYECTO LOS DOS CAMPOS del RuntimeScan, no solo .pids. Adjudicar sobre
    # `.pids` ignoraba `.unreadable_candidates` y por eso mi oraculo COINCIDIA con
    # mi writer justo donde los dos estaban mal (29-ago 15:48). Tercera vez que
    # elijo el alcance del oraculo: 2 de 3 funciones, luego el corpus, ahora un campo.
    r = auth.scan_primary_runtimes("FABLE", pathlib.Path(proc))
    if len(r.pids) == 1 and not r.unreadable_candidates: esperado = "present_unique"
    elif len(r.pids) == 1:    esperado = "present_ambiguous"   # unicidad NO medida
    elif len(r.pids) > 1:     esperado = "present_ambiguous"
    elif r.unreadable_candidates: esperado = "indeterminate"
    else:                     esperado = "absent"
    estado = _run(proc, str(tmp_path))["runtime_detection_status"]
    assert estado == esperado, (
        f"DIVERGENCIA con la autoridad en {argv[1:3]}: "
        f"autoridad={esperado} writer={estado}"
    )


@pytest.mark.parametrize("seal_agent,esperado", [
    ("FABLE", "present_unique"),
    ("OTRO", "absent"),          # el P2 de la fixture de JARVIS
    ("", "absent"),
    # CORREGIDO 30-ago 02:20: este caso decia "environ ilegible" pero la fixture
    # construye un environ AUSENTE, que no es lo mismo. Preguntado a la AUTORIDAD:
    #   environ ausente -> pids=() unreadable_candidates=() -> NO candidato
    # o sea ausencia limpia. El test codificaba MI defecto como conducta esperada
    # y por eso sobrevivio 49 tests. El caso ILEGIBLE de verdad va abajo, aparte.
    (None, "absent"),
])
def test_seal_agent_del_environ_decide(tmp_path, seal_agent, esperado):
    """El defecto vivo del 29-ago 15:26: adoptaba asiento por argv SOLO."""
    proc = _proc(str(tmp_path), 9001, "claude",
                 ["claude", "--name", "FABLE", "--tail"], seal_agent=seal_agent)
    assert _run(proc, str(tmp_path))["runtime_detection_status"] == esperado


# --- FUERA DE DOMINIO != AUSENTE (29-ago 15:37) ------------------------------
# `scan_primary_runtimes` arbitra "que proceso CLAUDE es el asiento primario", no
# "el agente esta vivo". Para los cuatro coincide por accidente de runtime; ADA
# corre codex y la autoridad la da ausente estando viva. Mi detector NO PUEDE
# producir presencia fuera de claude: decir "absent" es afirmar lo que no medi.

def test_un_proceso_NO_claude_es_absent_como_la_autoridad(tmp_path):
    """ALCANCE DECLARADO: este detector responde «cual es el asiento CLAUDE».

    Aca hubo una rama `fuera_de_dominio` que puse el 29-ago 15:37 para el caso de
    ADA -un agente vivo bajo otro runtime-. La SAQUE a las 23:16 porque NEXUS me
    empujo a medirla y estaba mal en las DOS direcciones:

      el proceso REAL de ADA   argv = [node, .../codex, --profile, ada]
                               mi rama exigia `--name`: NUNCA habria disparado
      un bash con --name FABLE en argv  ->  yo publicaba `indeterminate`
                               la autoridad: ausencia limpia, ni candidato es

    Una heuristica que no distingue un runtime ajeno de mis propios hijos no es
    una senal. El alcance se DECLARA; no se adivina.
    """
    proc = _proc(str(tmp_path), 901, "bash",
                 ["claude", "--name", "FABLE"], seal_agent="FABLE")
    d = _run(proc, str(tmp_path))
    assert d["runtime_detection_status"] == "absent"
    assert d["alive"] is False


def test_un_hijo_mio_que_MENCIONA_el_flag_sigue_siendo_absent(tmp_path):
    """CONTROL. SEAL_AGENT lo heredan todos mis hijos: sin este caso, la señal de
    fuera-de-dominio se dispara con mis propios procesos de medicion."""
    proc = _proc(str(tmp_path), 8888, "bash",
                 ["bash", "-c", "echo --name FABLE"], seal_agent="FABLE")
    assert _run(proc, str(tmp_path))["runtime_detection_status"] == "absent"


def test_candidato_con_comm_ILEGIBLE_es_indeterminate(tmp_path):
    """La autoridad lo pone en unreadable_candidates; yo lo saltaba MUDO -> absent."""
    proc = _proc(str(tmp_path), 9100, "claude",
                 ["claude", "--name", "FABLE", "--tail"], seal_agent="FABLE")
    comm = os.path.join(proc, "9100", "comm")
    os.chmod(comm, 0o000)
    try:
        d = _run(proc, str(tmp_path))
        auth = _autoridad()
        r = auth.scan_primary_runtimes("FABLE", pathlib.Path(proc))
        assert r.unreadable_candidates, "la autoridad deberia marcarlo ilegible"
        assert d["runtime_detection_status"] == "indeterminate"
        assert d["runtime"] == "candidatos_ilegibles"
    finally:
        os.chmod(comm, 0o644)


def test_un_ilegible_junto_a_un_asiento_da_ambiguo_no_unico(tmp_path):
    """CONTROL. La incertidumbre sobre un proceso ajeno no puede tapar mi asiento."""
    proc = _proc(str(tmp_path), 9200, "claude",
                 ["claude", "--name", "FABLE", "--tail"], seal_agent="FABLE")
    _proc(str(tmp_path), 9300, "claude", ["claude", "--name", "ALICE"], seal_agent="ALICE")
    ajeno = os.path.join(proc, "9300", "comm")
    os.chmod(ajeno, 0o000)
    try:
        d = _run(proc, str(tmp_path))
        # CORRECCION de JARVIS (15:50): el ilegible PODRIA ser un segundo asiento.
        # Yo habia escrito `present_unique` aca 10 minutos antes -- afirmaba una
        # unicidad que no medi. La presencia si es concluyente: alive sigue true.
        assert d["runtime_detection_status"] == "present_ambiguous"
        assert d["alive"] is True
    finally:
        os.chmod(ajeno, 0o644)


def test_un_proceso_MUERTO_no_cuenta_como_ilegible(tmp_path):
    """LA REGRESION QUE ESTUVO VIVA (29-ago 15:51, ~60 s).

    Colapse `no existe` con `sin permiso` en un solo `[ -r ]`. En un sistema con
    1125 procesos alguno muere entre el listado y la lectura en CADA barrido, asi
    que produccion paso a `present_ambiguous pid=0` con mi asiento sano al lado.
    Los ilegibles REALES eran 0 de 1125: todo el ruido era gente muriendose.
    """
    proc = _proc(str(tmp_path), 9400, "claude",
                 ["claude", "--name", "FABLE", "--tail"], seal_agent="FABLE")
    muerto = os.path.join(proc, "9500")          # el dir existe, el proceso ya no
    os.makedirs(muerto, exist_ok=True)           # sin comm/cmdline: murio a mitad
    d = _run(proc, str(tmp_path))
    assert d["runtime_detection_status"] == "present_unique", (
        "un proceso MUERTO se esta contando como candidato ilegible")
    assert str(d["process_pid"]) == "9400"


def test_la_etiqueta_del_camino_SANO_esta_asertada(tmp_path):
    """Medido 29-ago 16:10 (clase de NEXUS sobre el writer de JARVIS): cambiar
    `claude_named` por cualquier cosa daba 43 passed. La etiqueta del estado mas
    comun --la que todo consumidor lee-- era la unica sin dueno en la suite."""
    proc = _proc(str(tmp_path), 6001, "claude",
                 ["claude", "--name", "FABLE", "--tail"], seal_agent="FABLE")
    d = _run(proc, str(tmp_path))
    assert d["runtime_detection_status"] == "present_unique"
    assert d["runtime"] == "claude_named"


PROD_HB = f"{REPO}/messages/fable_claude_heartbeat.json"


def _semantica(path=PROD_HB):
    """Los campos que IMPORTAN. `timestamp` cambia en CADA corrida legitima, asi
    que comparar bytes confunde 'el timer escribio bien' con 'una fixture escribio
    mal' -- me dio una falsa alarma de "produccion corrupta" el 29-ago 16:50, con
    el timer corriendo entre mis dos snapshots."""
    with open(path) as fh:
        d = json.load(fh)
    return (d.get("runtime_detection_status"), str(d.get("process_pid")), d.get("alive"))


def _reparar_produccion():
    """Regenera con el writer REAL en vez de restaurar bytes viejos: un snapshot
    podria pisar un latido legitimo mas fresco que el mio."""
    subprocess.run(["/bin/bash", WRITER], capture_output=True, timeout=60)


def test_una_fixture_NO_puede_alcanzar_el_sink_REAL(tmp_path):
    """Invariante de ADA (29-ago 16:42), tras el incidente en vivo de JARVIS:
    una fuente sintetica no puede tocar un sink real, y no puede depender de que
    el que llama exporte HOME. Mi trap era ese: `FABLE_PROC_ROOT` no influia en el
    destino, asi que un revisor siguiendo mis instrucciones publicaba un latido
    falso en mi produccion.

    ESTE TEST REPARA LO QUE DETECTA: si el guard no esta, la corrida escribe el
    latido real UNA vez; el test lo restaura desde el snapshot y ademas falla. Sin
    eso, correr un mutante de esa linea dejaria a FABLE muerto en el canal.
    """
    antes = _semantica()
    proc = _proc(str(tmp_path), 9999, "claude",
                 ["claude", "--name", "ALICE", "--tail"], seal_agent="ALICE")
    # la llamada EXACTA que rompio a JARVIS: solo PROC_ROOT, HOME real
    env = {**os.environ, "FABLE_PROC_ROOT": proc}
    env.pop("HOME", None)
    r = subprocess.run(["/bin/bash", WRITER], env={**env, "HOME": os.path.expanduser("~")},
                       capture_output=True, text=True, timeout=60)
    if _semantica() != antes:
        _reparar_produccion()                # regenerar ANTES de fallar
        raise AssertionError(
            "LA FIXTURE ALCANZO EL SINK REAL: el latido de produccion cambio de "
            "ESTADO. Regenerado con el writer real, pero el guard no funciona."
        )
    assert "MODO FIXTURE" in r.stderr, "el guard debe AVISAR, no redirigir en silencio"


@pytest.mark.parametrize("home_variante", [
    "/home/dadito/",     # barra final
    "/home/dadito/.",    # componente /./
])
def test_el_MISMO_sink_por_OTRO_NOMBRE_tampoco_se_alcanza(tmp_path, home_variante):
    """Limite que nombro ADA y que JARVIS produjo (16:47): mi guard comparaba
    CADENAS, y el mismo destino alcanzado por otro nombre no matcheaba. Medido:
    barra final, /./ y symlink escapaban los tres. No eran sinks nuevos.

    Repara lo que detecta, igual que el test de arriba.
    """
    antes = _semantica()
    proc = _proc(str(tmp_path), 9999, "claude",
                 ["claude", "--name", "ALICE", "--tail"], seal_agent="ALICE")
    env = {**os.environ, "FABLE_PROC_ROOT": proc, "HOME": home_variante}
    r = subprocess.run(["/bin/bash", WRITER], env=env, capture_output=True,
                       text=True, timeout=60)
    if _semantica() != antes:
        _reparar_produccion()
        raise AssertionError(
            f"ESCAPO por HOME={home_variante}: el mismo sink alcanzado por otro "
            "nombre. Produccion regenerada con el writer real."
        )
    assert "MODO FIXTURE" in r.stderr


# --- environ ILEGIBLE: la incertidumbre no pisa una presencia probada -----------
# Hallazgo de NEXUS (29-ago 23:08) revisando `fable-seat-detector`. Yo tenia
# `PS_OK=0` en la rama del environ, y PS_OK se evalua ANTES que FABLE_N: un proceso
# cuyo environ no podia leer PISABA un asiento hallado sin ambiguedad.
# Es el MISMO defecto que cerre para `comm` a las 18:40 -- arreglar una instancia
# no arregla la clase, y mi suite daba 47 passed con el agujero abierto.

def test_environ_ilegible_JUNTO_a_un_asiento_es_ambiguo_no_indeterminate(tmp_path):
    """La EXISTENCIA esta probada por el legible; lo que falta es la UNICIDAD."""
    proc = _proc(str(tmp_path), 101, "claude",
                 ["claude", "--name", "FABLE", "--tail"], seal_agent="FABLE")
    _proc(str(tmp_path), 999, "claude",
          ["claude", "--name", "FABLE", "--tail"], seal_agent="FABLE")
    env999 = os.path.join(proc, "999", "environ")
    os.chmod(env999, 0o000)
    try:
        d = _run(proc, str(tmp_path))
        assert d["runtime_detection_status"] == "present_ambiguous"
        assert d["alive"] is True
        assert str(d["process_pid"]) == "0"
    finally:
        os.chmod(env999, 0o644)


def test_environ_ilegible_SIN_asiento_sigue_siendo_indeterminate(tmp_path):
    """CONTROL en la otra direccion: sin legible, no hay existencia probada."""
    proc = _proc(str(tmp_path), 999, "claude",
                 ["claude", "--name", "FABLE", "--tail"], seal_agent="FABLE")
    env999 = os.path.join(proc, "999", "environ")
    os.chmod(env999, 0o000)
    try:
        d = _run(proc, str(tmp_path))
        assert d["runtime_detection_status"] == "indeterminate"
        assert d["alive"] is False
    finally:
        os.chmod(env999, 0o644)


# ---------------------------------------------------------------------------
# ROJOS del 30-ago 02:10 — los cinco defectos que NEXUS confirmo al retirar su
# APPROVE. Cada uno falla por SU causa exacta antes del fix.
# Prioridad: 5 (oculta incertidumbre) > 1 y 2 (la inventan) > 3 (canal) > 4 (razon).


def test_r5_proc_no_listable_no_es_ausencia(tmp):
    """DEFECTO 5, el peligroso: un fallo de `ls` se publica como ausencia CONFIADA.

    `:105 for _p in $(ls "$PROC_ROOT" ...)`. El `[ -d ]` previo ya puso PS_OK=1,
    asi que el bucle vacio cae al `else` -> absent. El asiento SIGUE AHI.
    """
    root = _proc(tmp, 900, "claude", ["claude", "--name", "FABLE"])
    os.chmod(root, 0o000)
    try:
        d = _run(root, tmp)
    finally:
        os.chmod(root, 0o755)
    assert os.path.isdir(os.path.join(root, "900")), "control: el asiento debia seguir ahi"
    assert d["runtime_detection_status"] == "indeterminate", (
        "un fallo del instrumento se publico como ausencia: %s" % d["runtime_detection_status"])
    assert d["alive"] is False


def test_r1_cmdline_ausente_es_muerte_no_incertidumbre(tmp):
    """DEFECTO 1: un AJENO que murio entre lecturas no debe borrar mi asiento."""
    root = _proc(tmp, 900, "claude", ["claude", "--name", "FABLE"])
    _proc(tmp, 901, "claude", ["claude", "--name", "OTRO"], seal_agent="OTRO")
    os.remove(os.path.join(root, "901", "cmdline"))      # murio: /proc/<pid> se vacia
    d = _run(root, tmp)
    assert d["runtime_detection_status"] == "present_unique", (
        "un proceso muerto se leyo como incertidumbre: %s" % d["runtime_detection_status"])
    assert d["process_pid"] == "900"


def test_r2_environ_ausente_es_muerte_no_incertidumbre(tmp):
    """DEFECTO 2: identico al 1, en el tercer punto de lectura."""
    root = _proc(tmp, 900, "claude", ["claude", "--name", "FABLE"])
    _proc(tmp, 901, "claude", ["claude", "--name", "FABLE"], seal_agent=None)
    d = _run(root, tmp)
    assert d["runtime_detection_status"] == "present_unique", (
        "environ ausente (muerte) se leyo como incertidumbre: %s"
        % d["runtime_detection_status"])
    assert d["process_pid"] == "900"


def test_r2b_environ_ilegible_SI_es_incertidumbre(tmp):
    """CONTROL del 2: sin permiso NO es lo mismo que ausente. Debe degradar."""
    root = _proc(tmp, 900, "claude", ["claude", "--name", "FABLE"])
    _proc(tmp, 901, "claude", ["claude", "--name", "FABLE"], seal_agent="FABLE")
    os.chmod(os.path.join(root, "901", "environ"), 0o000)
    try:
        d = _run(root, tmp)
    finally:
        os.chmod(os.path.join(root, "901", "environ"), 0o644)
    assert d["runtime_detection_status"] in ("present_ambiguous", "indeterminate"), (
        "un ilegible real debe degradar, no ser ignorado: %s"
        % d["runtime_detection_status"])


@pytest.mark.parametrize("salida", ["Unable to determine the device handle", "[N/A]", ".5", "5.", "01"])
def test_r3_gpu_no_numerico_produce_json_valido(tmp, salida):
    """DEFECTO 3: nvidia-smi con rc=0 y salida no-numerica rompe el JSON entero.

    `.5`, `5.` y `01` pasan un `case [!0-9.]` ingenuo y NO son numeros JSON validos.
    """
    bindir = os.path.join(tmp, "bin")
    os.makedirs(bindir, exist_ok=True)
    fake = os.path.join(bindir, "nvidia-smi")
    with open(fake, "w") as fh:
        # sin formateo `%`: el `%s` del script shell lo consumiria Python
        fh.write("#!/bin/bash\nprintf '%s\\n' " + shlex.quote(salida) + "\n")
    os.chmod(fake, 0o755)
    root = _proc(tmp, 900, "claude", ["claude", "--name", "FABLE"])
    d = _run(root, tmp, extra_env={"PATH": bindir + ":" + os.environ.get("PATH", "")})
    assert d["runtime_detection_status"] == "present_unique"
    assert d["gpu_temp"] is None or isinstance(d["gpu_temp"], (int, float)), (
        "gpu_temp no numerico rompe a todo consumidor: %r" % (d.get("gpu_temp"),))


@pytest.mark.parametrize("caso,marca", [
    ("ambiguo_por_ilegibles", "AMBIGUO"),
    ("candidatos_ilegibles", "INDETERMINADO"),
    ("absent", "AUSENTE"),
])
def test_r4_las_ramas_mudas_declaran_su_causa(tmp, caso, marca):
    """DEFECTO 4: tres ramas no dejan rastro del POR QUE en el journal."""
    root = _proc(tmp, 900, "claude", ["claude", "--name", "FABLE"])
    if caso == "ambiguo_por_ilegibles":
        _proc(tmp, 901, "claude", ["claude", "--name", "FABLE"])
        os.chmod(os.path.join(root, "901", "comm"), 0o000)
    elif caso == "candidatos_ilegibles":
        os.chmod(os.path.join(root, "900", "comm"), 0o000)
    else:
        os.remove(os.path.join(root, "900", "cmdline"))
        os.remove(os.path.join(root, "900", "comm"))
    try:
        d, err = _run(root, tmp, con_stderr=True)
    finally:
        for pid in ("900", "901"):
            c = os.path.join(root, pid, "comm")
            if os.path.exists(c):
                os.chmod(c, 0o644)
    assert marca in err, (
        "la rama %s no dejo su causa en stderr (estado=%s, stderr=%r)"
        % (caso, d["runtime_detection_status"], err[-200:]))


def test_environ_ILEGIBLE_de_verdad_si_degrada(tmp_path):
    """El caso que el parametrizado de arriba CREIA cubrir y no cubria.

    Ausente (murio) y sin permiso (vivo, no puedo leerlo) son estados distintos
    para la autoridad y ahora tambien para el writer.
    """
    proc = _proc(str(tmp_path), 9001, "claude",
                 ["claude", "--name", "FABLE", "--tail"], seal_agent="FABLE")
    env = os.path.join(proc, "9001", "environ")
    os.chmod(env, 0o000)
    try:
        estado = _run(proc, str(tmp_path))["runtime_detection_status"]
    finally:
        os.chmod(env, 0o644)
    assert estado in ("present_ambiguous", "indeterminate"), (
        "un environ SIN PERMISO debe degradar, no leerse como ausencia: %s" % estado)


# ---------------------------------------------------------------------------
# ROJOS del REJECT de NEXUS (30-ago 02:19): la clase del defecto 5 vivia en
# cat/tr/sed/head. El fix no es once parches: es un CANARIO de la cadena.


def _romper(tmp, cmd):
    """Sombra de PATH que hace fallar UN instrumento. Devuelve el bindir."""
    b = os.path.join(tmp, "bin")
    os.makedirs(b, exist_ok=True)
    f = os.path.join(b, cmd)
    with open(f, "w") as fh:
        fh.write("#!/bin/bash\nexit 127\n")
    os.chmod(f, 0o755)
    return b


@pytest.mark.parametrize("cmd", ["cat", "tr", "sed", "head", "ls", "awk", "date"])
def test_instrumento_roto_nunca_produce_ausencia_falsa(tmp, cmd):
    """NEXUS, 02:19 y 02:25: con el asiento PRESENTE, romper un instrumento daba
    `absent pid=0` con un aviso que CERTIFICABA la completitud.

    ASERTA LA PROPIEDAD, NO EL MECANISMO. La primera version de este test exigia
    `indeterminate` --el remedio del canario-- y se puso ROJA cuando el arreglo
    mejoro: al pasar el camino de medicion a builtins, romper `cat` dejo de
    afectar la deteccion y el estado correcto paso a ser `present_unique`.
    Un test que fija UNA implementacion bloquea la implementacion MEJOR.

    Lo unico inaceptable es una AUSENCIA FALSA: decir "medi y no esta" con el
    asiento presente. Inmunidad (present_unique) y deteccion (indeterminate) son
    las dos respuestas correctas.
    """
    root = _proc(tmp, 900, "claude", ["claude", "--name", "FABLE"])
    b = _romper(tmp, cmd)
    d, err = _run(root, tmp, extra_env={"PATH": b + ":" + os.environ.get("PATH", "")},
                  con_stderr=True)
    assert d["runtime_detection_status"] != "absent", (
        "romper `%s` se publico como AUSENCIA con el asiento presente" % cmd)
    if d["runtime_detection_status"] == "indeterminate":
        assert "INDETERMINADO" in err, "degrado sin declarar la causa"
    else:
        assert d["runtime_detection_status"] == "present_unique", d
        assert d["process_pid"] == "900"
    assert "MEDICION, no un fallo" not in err, (
        "certifica una completitud que no tuvo")


def test_control_instrumentos_sanos_no_disparan_el_canario(tmp):
    """CONTROL: sin romper nada, el canario NO debe degradar el estado."""
    root = _proc(tmp, 900, "claude", ["claude", "--name", "FABLE"])
    d, err = _run(root, tmp, con_stderr=True)
    assert d["runtime_detection_status"] == "present_unique"
    assert "canario" not in err


def test_emision_fallida_no_destruye_el_latido_anterior(tmp):
    """NEXUS: `printf` roto rompia el CANAL. El `>` trunca ANTES de saber si el
    comando funciona. Ahora: temporal + verificacion + mv, o nada."""
    root = _proc(tmp, 900, "claude", ["claude", "--name", "FABLE"])
    home = os.path.join(tmp, "home")
    msgs = os.path.join(home, "IA/proyecto-seal/messages")
    os.makedirs(msgs, exist_ok=True)
    hb = os.path.join(msgs, "fable_claude_heartbeat.json")
    with open(hb, "w") as fh:
        fh.write('{"agent":"FABLE","alive":true,"marca":"ANTERIOR"}')
    antes = open(hb).read()
    os.chmod(msgs, 0o500)                      # no se puede crear el temporal
    try:
        env = {**os.environ, "HOME": home, "FABLE_PROC_ROOT": root}
        r = subprocess.run(["/bin/bash", WRITER], env=env, capture_output=True,
                           timeout=30, text=True)
    finally:
        os.chmod(msgs, 0o755)
    assert r.returncode != 0, "una emision imposible debe fallar RUIDOSA"
    assert "INTACTO" in r.stderr
    assert open(hb).read() == antes, "el latido anterior fue destruido"
    json.loads(open(hb).read())                # y sigue siendo JSON valido


# ---------------------------------------------------------------------------
# SEMANTICA DE LECTURA (NEXUS, 02:30): "read y mapfile no se comportan igual que
# cat y tr; ahi no hay un fallo, hay una DIVERGENCIA SILENCIOSA".
# Tenia razon: 3 de 9. Una la introduje yo al pasar a builtins (comm sin salto
# final: `read` devuelve rc!=0 pero SI deja lo leido). Dos eran PREEXISTENTES
# --comm con espacio inicial/final-- y ningun corpus mio de 42 casos las toco.

@pytest.mark.parametrize("comm_bytes,environ_bytes,desc,esperado", [
    (b"claude\n",        b"SEAL_AGENT=FABLE\0",       "normal",              True),
    (b"claude",           b"SEAL_AGENT=FABLE\0",       "comm sin salto final", True),
    (b"claude \n",       b"SEAL_AGENT=FABLE\0",       "comm espacio final",   True),
    (b" claude\n",       b"SEAL_AGENT=FABLE\0",       "comm espacio inicial", True),
    (b"claude\\\n",    b"SEAL_AGENT=FABLE\0",       "comm backslash",       False),
    (b"claude\n",        b"SEAL_AGENT=FABLE",          "environ sin NUL",      True),
    (b"claude\n",        b"X=1\0SEAL_AGENT=FABLE\0", "environ var previa",   True),
    (b"claude\n",        b"SEAL_AGENT= FABLE \0",     "environ con espacios", True),
])
def test_lectura_coincide_con_la_autoridad(tmp, comm_bytes, environ_bytes, desc, esperado):
    """El veredicto lo da la AUTORIDAD; el writer solo tiene que igualarlo."""
    auth = _autoridad()
    root = os.path.join(tmp, "proc")
    d = os.path.join(root, "100")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "comm"), "wb") as fh:
        fh.write(comm_bytes)
    with open(os.path.join(d, "cmdline"), "wb") as fh:
        fh.write(b"claude\x00--name\x00FABLE\x00".replace(b"\\x00", b"\0"))
    with open(os.path.join(d, "environ"), "wb") as fh:
        fh.write(environ_bytes)
    halla_autoridad = bool(auth.scan_primary_runtimes("FABLE", pathlib.Path(root)).pids)
    assert halla_autoridad is esperado, (
        "la AUTORIDAD cambio de opinion en %r: el test hay que revisarlo, no el writer" % desc)
    estado = _run(root, tmp)["runtime_detection_status"]
    mio = (estado == "present_unique")
    assert mio is halla_autoridad, (
        "DIVERGENCIA SILENCIOSA en %r: writer=%s autoridad=%s" % (desc, estado, halla_autoridad))


def test_M6_emision_vacia_no_pisa_el_latido_anterior(tmp):
    """MATA el mutante M6 de NEXUS: quitar `if [ ! -s "$_HB_TMP" ]`.

    Mi test anterior rompia `mktemp`, que ejercita la rama de "no pude crear el
    temporal" -- OTRA. Con `printf` roto el temporal SI se crea y queda VACIO;
    sin el `-s` el writer lo mueve encima del latido bueno. 79 tests pasaban con
    el mutante vivo: el fix estaba sin cubrir.
    """
    root = _proc(tmp, 900, "claude", ["claude", "--name", "FABLE"])
    home = os.path.join(tmp, "home")
    msgs = os.path.join(home, "IA/proyecto-seal/messages")
    os.makedirs(msgs, exist_ok=True)
    hb = os.path.join(msgs, "fable_claude_heartbeat.json")
    with open(hb, "w") as fh:
        fh.write('{"agent":"FABLE","alive":true,"marca":"ANTERIOR"}')
    antes = open(hb).read()
    env = {**os.environ, "HOME": home, "FABLE_PROC_ROOT": root,
           "BASH_FUNC_printf%%": "() { return 1; }"}
    # PRECONDICION: el mutante tiene que estar aplicado en el hijo
    pre = subprocess.run(["/bin/bash", "-c", "type -t printf"], env=env,
                         capture_output=True, text=True).stdout.strip()
    assert pre == "function", "el mutante de printf NO estaba aplicado: %r" % pre
    subprocess.run(["/bin/bash", WRITER], env=env, capture_output=True, timeout=30)
    assert os.path.getsize(hb) > 0, "el latido quedo en CERO BYTES"
    assert open(hb).read() == antes, "el latido anterior fue pisado"
    json.loads(open(hb).read())


def test_los_separadores_no_dependen_del_charmap_del_proceso():
    """El fix de ALICE/NEXUS, protegido en MI espejo.

    `$'\\uXXXX'` lo resuelve bash usando el charmap del PROCESO: sin locale
    UTF-8 deja los 6 caracteres literales y el separador NO existe. Medido antes
    de arreglarlo: 20 de 30 separadores fallaban bajo LANG=C, 0 bajo UTF-8 --
    un verde en mi terminal y un espejo roto en cualquier entorno sin locale.

    EL CONTROL ES EL PAR: si sólo probara UTF-8 este test pasaba con el bug
    puesto. Los escapes de BYTE (`$'\\xNN'`) no dependen de nada.
    """
    import re as _re
    # La autoridad por la MISMA costura que el resto de la suite: asi este test
    # tambien se rompe si alguien la muta con FABLE_AUTHORITY_UNDER_TEST.
    _argv_has_agent_name = _autoridad()._argv_has_agent_name
    seps = [chr(c) for c in range(0x110000) if _re.match(r"\s", chr(c))] + ["\u2014"]
    src = open(WRITER).read().split("\n")
    ini = next(i for i, l in enumerate(src) if l.startswith("_es_mi_nombre() {"))
    fin = next(i for i, l in enumerate(src[ini:], ini) if l == "}")
    with tempfile.TemporaryDirectory() as d:
        sh = os.path.join(d, "f.sh")
        with open(sh, "w") as fh:
            fh.write("\n".join(src[ini:fin + 1]) +
                     '\n_es_mi_nombre "$1" && echo SI || echo NO\n')
        assert subprocess.run(["/bin/bash", "-n", sh]).returncode == 0
        for nombre, env in (("UTF-8", {"LANG": "es_ES.utf8", "PATH": "/usr/bin:/bin"}),
                            ("LANG=C", {"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"})):
            malos = []
            for s in seps:
                cand = "FABLE" + s + "x"
                esperado = _argv_has_agent_name(["claude", "--name", cand], "FABLE")
                r = subprocess.run(["/bin/bash", sh, cand], capture_output=True,
                                   text=True, env=env)
                assert r.returncode == 0, r.stderr
                if (r.stdout.strip() == "SI") != esperado:
                    malos.append(hex(ord(s)))
            assert not malos, "%s: %d separadores divergen de la autoridad: %s" % (
                nombre, len(malos), malos[:8])
        # CONTROL NEGATIVO: un caracter que NO es separador no debe pasar
        r = subprocess.run(["/bin/bash", sh, "FABLE:x"], capture_output=True,
                           text=True, env={"LANG": "C", "PATH": "/usr/bin:/bin"})
        assert r.stdout.strip() == "NO", "acepto ':' como separador"
