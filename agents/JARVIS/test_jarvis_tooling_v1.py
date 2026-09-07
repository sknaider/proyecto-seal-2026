"""Contrato de la DETECCION DE ASIENTO de JARVIS: seat_lib.sh y su writer.

gx.sh salio a agents/JARVIS/tests/test_jarvis_gx_v1.py: estaba aca por autoria
-lo escribi yo- y no por pertenecer a esta unidad de revision. Diagnostico de
NEXUS: agrupabamos por AUTOR, no por unidad revisable.

Cada sujeto trae su propio `--self-test`. Este archivo NO los reimplementa: los
EJERCE y ademas verifica que el self-test SEPA FALLAR, con un mutante.

Sin el caso negativo, "self-test dice fallos=0" no discrimina entre un sujeto
correcto y un self-test que siempre pasa.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]  # tests -> JARVIS -> agents -> raiz
SEAT_LIB = ROOT / "agents" / "JARVIS" / "seat_lib.sh"


def _self_test(script: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script), "--self-test"],
        check=False, capture_output=True, text=True, timeout=180,
    )


def test_seat_lib_self_test_passes():
    """POSITIVO: el self-test de seat_lib pasa sobre los bytes actuales."""
    r = _self_test(SEAT_LIB)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "fallos=0" in r.stdout, r.stdout



def test_seat_lib_self_test_detects_a_broken_matcher():
    """NEGATIVO: si el matcher vuelve a comparar por SUBCADENA, el self-test
    tiene que ponerse ROJO. Sin este caso, el verde de arriba no discrimina."""
    with tempfile.TemporaryDirectory() as tmp:
        mutant = Path(tmp) / "seat_lib_mutant.sh"
        original = SEAT_LIB.read_text(encoding="utf-8")
        # MUTANTE: revertir el matcher a comparacion por PREFIJO. Es el defecto
        # que ADA encontro y que aceptaba `JARVIS-CLONE` como si fuera `JARVIS`.
        broken = original.replace(
            'for c in "${_ws[@]}"; do [[ "$resto" == "$c"* ]] && return 0; done\n  return 1',
            'return 0',
        )
        assert broken != original, "la mutacion NO se aplico: el test no probaria nada"
        mutant.write_text(broken, encoding="utf-8")
        r = _self_test(mutant)
        assert r.returncode != 0, "el self-test aprobo un matcher por subcadena"
        assert "FALLA" in r.stdout, r.stdout



def test_bash_is_available():
    """CONTROL DE LA FIXTURE: si no hay bash, los casos de arriba serian no-ops
    que se leen como verdes."""
    assert shutil.which("bash"), "sin bash, este archivo no prueba nada"


# --------------------------------------------------------------------------
# ORACULO COMPLETO: las TRES condiciones, no solo el nombre.
#
# Hasta hoy mi unico oraculo comparaba `jarvis_name_matches` contra
# `_argv_has_agent_name` -- 1 de 3-- y el rotulo decia "vs la autoridad".
# FABLE encontro la misma clase en el suyo (2 de 3), NEXUS en el suyo (1 de 4),
# ALICE 0 de 4. **Un predicado que el oraculo no llama no puede contradecirte.**
#
# Esto compara el detector ENTERO contra `scan_primary_runtimes`, la funcion que
# el supervisor usa de verdad, sobre un /proc sintetico que los dos leen.
# --------------------------------------------------------------------------

def _mkproc(root, pid, comm, argv, env):
    d = pathlib.Path(root) / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "comm").write_text(comm + "\n", encoding="utf-8")
    (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
    (d / "environ").write_bytes(b"\0".join(f"{k}={v}".encode() for k, v in env.items()) + b"\0")


def _autoridad():
    import importlib.util, sys as _s
    ruta = ROOT / "tools" / "seal_agent_runtime_supervisor.py"
    spec = importlib.util.spec_from_file_location("seal_authority_jv", ruta)
    mod = importlib.util.module_from_spec(spec)
    _s.modules["seal_authority_jv"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_detector_completo_coincide_con_la_autoridad():
    auth = _autoridad()
    # CONTROL DEL ORACULO: tiene que aceptar Y rechazar, o no discrimina.
    with tempfile.TemporaryDirectory() as t:
        _mkproc(t, 11, "claude", ["claude", "--name", "JARVIS", "--tail"], {"SEAL_AGENT": "JARVIS"})
        assert auth.scan_primary_runtimes("JARVIS", pathlib.Path(t)).pids == (11,)
        assert auth.scan_primary_runtimes("NADIE", pathlib.Path(t)).pids == ()

    casos = {
        "legitimo":        (101, "claude", ["claude", "--name", "JARVIS", "--tail"], {"SEAL_AGENT": "JARVIS"}),
        "impostor_env":    (102, "claude", ["claude", "--name", "JARVIS", "--tail"], {"SEAL_AGENT": "OTRO"}),
        "worker":          (103, "claude", ["claude", "--name", "JARVIS", "--output-format", "stream-json"], {"SEAL_AGENT": "JARVIS"}),
        "worker_igual":    (104, "claude", ["claude", "--name", "JARVIS", "--output-format=stream-json"], {"SEAL_AGENT": "JARVIS"}),
        "mencion":         (105, "claude", ["claude", "--system-prompt", "usa --name JARVIS ahi"], {"SEAL_AGENT": "JARVIS"}),
        "clon_guion":      (106, "claude", ["claude", "--name", "JARVIS-CLONE"], {"SEAL_AGENT": "JARVIS"}),
        "raya_pegada":     (107, "claude", ["claude", "--name", "JARVIS—Team"], {"SEAL_AGENT": "JARVIS"}),
        "name_igual":      (108, "claude", ["claude", "--name=JARVIS"], {"SEAL_AGENT": "JARVIS"}),
        "comm_distinto":   (109, "python3", ["claude", "--name", "JARVIS"], {"SEAL_AGENT": "JARVIS"}),
        "env_minusculas":  (110, "claude", ["claude", "--name", "JARVIS"], {"SEAL_AGENT": "jarvis"}),
    }
    with tempfile.TemporaryDirectory() as t:
        for _n, (pid, comm, argv, env) in casos.items():
            _mkproc(t, pid, comm, argv, env)

        esperado = sorted(auth.scan_primary_runtimes("JARVIS", pathlib.Path(t)).pids)

        r = subprocess.run(
            ["bash", "-c", 'source "$1"; jarvis_seat_pids JARVIS', "_", str(SEAT_LIB)],
            check=False, capture_output=True, text=True, timeout=120,
            env={**os.environ, "JARVIS_PROC_ROOT": t},
        )
        obtenido = sorted(int(x) for x in r.stdout.split() if x.isdigit())

    assert esperado, "el oraculo no acepto NINGUN caso: no discriminaria"
    assert obtenido == esperado, (
        f"divergencia con la autoridad\n  autoridad={esperado}\n  espejo   ={obtenido}\n"
        f"  stderr={r.stderr[:300]}"
    )


def test_el_alcance_es_asiento_CLAUDE_no_presencia_de_agente():
    """FIJA EL ALCANCE POR EJECUCION, no por prosa en un docstring.

    QUE LO GENERO (29-ago-2026): la autoridad declaro AUSENTE a ADA, que estaba
    viva, porque su asiento corre bajo codex y el primer gate de
    scan_primary_runtimes es `comm != "claude" -> continue`. Su docstring lo dice
    -"Find only the interactive Claude principal"- y los cuatro leimos el NOMBRE
    de la funcion en vez de esa linea. Un contrato en prosa no frena nada.

    POR QUE ESTE TEST NO ES CEREMONIA: mi oraculo de equivalencia
    (test_detector_completo_coincide_con_la_autoridad) es ESTRUCTURALMENTE ciego
    a esta clase. Mi espejo copia el mismo gate, asi que coincide perfecto -y
    coincidiria perfecto aunque la pregunta fuera otra-. Cuanto mejor el espejo,
    mas invisible el defecto. Este caso mira otra cosa: no la equivalencia, sino
    QUE PREGUNTA contesta el par.

    SI ESTE TEST SE PONE ROJO no significa "se rompio el detector": significa que
    alguien AMPLIO el alcance a runtimes no-Claude. Eso puede estar bien, pero
    tiene que ser una decision EXPLICITA -y el writer de latido que consume esto
    debe revisarse en el mismo movimiento, porque hoy traduce ausencia a
    alive=false.
    """
    auth = _autoridad()
    casos = {
        # el asiento REAL de ADA el 29-ago: launcher node -> binario codex hijo
        # OJO CON LA FIXTURE: mi primera version les puso `--profile jarvis` en vez
        # de `--name JARVIS`. Se leia realista y el test pasaba... porque quedaban
        # rechazados por DOS filtros a la vez (comm Y nombre). Mutando el gate de
        # comm en la autoridad el resultado NO cambiaba: el otro filtro los seguia
        # tapando. Un caso rechazado por dos razones no prueba NINGUNA.
        # Estos cumplen TODO lo demas -nombre y environ- para que el gate de comm
        # sea el UNICO discriminador que queda en pie.
        "asiento_codex_launcher": (201, "node",   ["node", "/x/bin/codex", "--name", "JARVIS"], {"SEAL_AGENT": "JARVIS"}),
        "asiento_codex_binario":  (202, "codex",  ["codex", "--name", "JARVIS"],                {"SEAL_AGENT": "JARVIS"}),
        # CONTROL POSITIVO: sin el, el cero de arriba no probaria alcance sino
        # que el /proc sintetico esta vacio o mal sembrado.
        "asiento_claude":         (203, "claude", ["claude", "--name", "JARVIS"],                  {"SEAL_AGENT": "JARVIS"}),
    }
    with tempfile.TemporaryDirectory() as t:
        for _n, (pid, comm, argv, env) in casos.items():
            _mkproc(t, pid, comm, argv, env)

        autoridad = sorted(auth.scan_primary_runtimes("JARVIS", pathlib.Path(t)).pids)
        r = subprocess.run(
            ["bash", "-c", 'source "$1"; jarvis_seat_pids JARVIS', "_", str(SEAT_LIB)],
            check=False, capture_output=True, text=True, timeout=120,
            env={**os.environ, "JARVIS_PROC_ROOT": t},
        )
        espejo = sorted(int(x) for x in r.stdout.split() if x.isdigit())

    assert autoridad == [203], (
        "ALCANCE CAMBIADO en la autoridad: ya no ignora asientos no-Claude.\n"
        f"  esperado=[203] (solo el asiento claude)   obtenido={autoridad}\n"
        "  Si fue deliberado: revisa TAMBIEN messages/jarvis_heartbeat_update.sh,\n"
        "  que hoy traduce 'sin asiento' a alive=false."
    )
    assert espejo == autoridad, (
        f"mi espejo divergio del alcance de la autoridad\n"
        f"  autoridad={autoridad}  espejo={espejo}\n  stderr={r.stderr[:300]}"
    )
    assert 201 not in autoridad and 202 not in autoridad, (
        "un asiento con runtime no-Claude fue aceptado: el detector ya no responde\n"
        "'cual es el asiento CLAUDE de X' sino algo mas amplio."
    )


def test_un_hallazgo_no_tapa_la_incertidumbre():
    """LA RAMA CRUZADA: encontre UN asiento Y algo quedo sin leer.

    QUE LO GENERO (29-ago-2026, reportado por ALICE): `jarvis_seat_pid`
    consultaba `ilegibles` SOLO en la rama de la ausencia. Con un asiento
    encontrado devolvia rc=0 -"unico y determinado"- mientras la autoridad
    devolvia pids=(101,) CON determined=False. Yo afirmaba certeza donde la
    autoridad declaraba duda.

    LOS TRES TENIAMOS LA MISMA CLASE, en tres direcciones distintas:
      JARVIS  el hallazgo tapaba al ilegible      -> demasiado confiado
      FABLE   el ilegible pisaba al hallazgo      -> demasiado conservador
      NEXUS   la proyeccion descartaba `determined` -> el oraculo no lo veia

    Ninguno de los tres carecia del concepto: los tres lo teniamos ESCRITO en un
    comentario del propio archivo. Lo implementamos en UNA rama y ningun test
    nuestro las cruzaba. Este test cruza las dos.

    POR QUE NO LO VE MI ORACULO DE EQUIVALENCIA: compara solo `.pids`, y ahi los
    dos coinciden en [101]. El campo que nos separa -`determined`- se cae de LOS
    DOS LADOS antes de comparar. El oraculo no falla: no esta mirando.
    """
    auth = _autoridad()
    with tempfile.TemporaryDirectory() as t:
        _mkproc(t, 101, "claude", ["claude", "--name", "JARVIS"], {"SEAL_AGENT": "JARVIS"})
        _mkproc(t, 777, "claude", ["claude", "--name", "JARVIS"], {"SEAL_AGENT": "JARVIS"})
        ilegible = pathlib.Path(t) / "777" / "environ"
        ilegible.chmod(0o000)

        # CONTROL DEL INSTRUMENTO: si el archivo se pudiera leer, el caso no
        # existiria y el test pasaria por la razon equivocada.
        try:
            ilegible.read_bytes()
            raise AssertionError("el environ sembrado SI se puede leer: la fixture no vale")
        except PermissionError:
            pass

        r = auth.scan_primary_runtimes("JARVIS", pathlib.Path(t))
        assert r.pids == (101,) and r.unreadable_candidates == (777,), (
            f"la autoridad no reprodujo el caso: {r}")
        assert r.determined is False, "la autoridad deberia declarar NO determinado"

        p = subprocess.run(
            ["bash", "-c", 'source "$1"; jarvis_seat_pid JARVIS', "_", str(SEAT_LIB)],
            check=False, capture_output=True, text=True, timeout=120,
            env={**os.environ, "JARVIS_PROC_ROOT": t},
        )
        ilegible.chmod(0o700)

    assert p.returncode == 2, (
        "un hallazgo tapo la incertidumbre: devolvi rc="
        f"{p.returncode} sobre un caso que la autoridad declara NO determinado.\n"
        "  rc=0 aqui significa publicar 'asiento unico' sin haber podido mirar todo."
    )
    assert p.stdout.strip() == "", (
        "no debo imprimir el pid con rc=2: un llamador que ignore el rc lo leeria\n"
        f"  como asiento unico. stdout={p.stdout!r}"
    )


WRITER = ROOT / "messages" / "jarvis_heartbeat_update.sh"


def _correr_writer(proc_root, out_dir):
    """Corre el writer COMPLETO en dryrun y devuelve el JSON que publica."""
    subprocess.run(
        ["bash", str(WRITER)], check=False, capture_output=True, text=True, timeout=180,
        env={**os.environ, "JARVIS_PROC_ROOT": str(proc_root),
             "JARVIS_MESSAGES_DIR": str(out_dir), "JARVIS_HB_DRYRUN": "1"},
    )
    return json.loads((pathlib.Path(out_dir) / "jarvis_claude_heartbeat.json").read_text())


def test_el_writer_publica_la_incertidumbre_no_solo_la_libreria():
    """LA RAMA DEL WRITER, ejercitada de punta a punta. No la libreria: EL WRITER.

    QUE LO GENERO (29-ago, REJECT de NEXUS en revision independiente): agregue al
    writer la rama "hallazgo + ilegible" y era CODIGO MUERTO. `_JARVIS_ILEG`
    valia siempre 0 porque `jarvis_seat_pids` emite ILEGIBLE por STDERR y el
    writer capturaba con `$( )`, que toma solo stdout. La libreria devolvia rc=2
    (correcto) y el writer publicaba present_unique (mal), a la vez.

    QUIEN CAUSO ESO: yo, la misma manana. ALICE reporto que ILEGIBLE salia
    MEZCLADO con los pids por stdout y lo "arregle en la frontera" mandandolo a
    stderr. Ese fix reparo a los dos llamadores que IGNORAN la marca y rompio al
    unico que la USABA. Lo publique como un fix limpio sin abrir el tercer
    consumidor.

      arreglar en la frontera repara a quien IGNORA la senal
                              y rompe a quien la USA, en silencio

    Y el silencio es lo peor: un conteo de 0 es indistinguible de "mire y no
    habia". Ninguna de mis 8 pruebas lo vio porque TODAS ejercitaban la libreria;
    el writer no tenia ni una. Por eso este test corre el writer entero, y por eso
    el writer ahora tiene un seam de dryrun.
    """
    with tempfile.TemporaryDirectory() as pr, tempfile.TemporaryDirectory() as o1, \
         tempfile.TemporaryDirectory() as o2:
        _mkproc(pr, 101, "claude", ["claude", "--name", "JARVIS"], {"SEAL_AGENT": "JARVIS"})
        limpio = _correr_writer(pr, o1)

        _mkproc(pr, 777, "claude", ["claude", "--name", "JARVIS"], {"SEAL_AGENT": "JARVIS"})
        ileg = pathlib.Path(pr) / "777" / "environ"
        ileg.chmod(0o000)
        try:
            ileg.read_bytes()
            raise AssertionError("el environ sembrado SI se lee: la fixture no vale")
        except PermissionError:
            pass
        try:
            con_ilegible = _correr_writer(pr, o2)
        finally:
            ileg.chmod(0o700)

    # CONTROL POSITIVO: sin esto, dos filas iguales pasarian por "no cambio nada".
    assert limpio["runtime_detection_status"] == "present_unique", (
        f"el caso limpio no da present_unique: {limpio}")
    assert limpio["alive"] is True

    assert con_ilegible["runtime_detection_status"] == "present_ambiguous", (
        "RAMA MUERTA: con un candidato ilegible el writer publico "
        f"{con_ilegible['runtime_detection_status']!r}.\n"
        "  Causa tipica: capturar `jarvis_seat_pids` con $( ) sin redirigir stderr,\n"
        "  que deja _JARVIS_ILEG clavado en 0.")
    assert con_ilegible["alive"] is True, (
        "presente-pero-no-unico NO es muerto: un ilegible no debe matar un asiento vivo")
    # REVERTIDO 29-ago tras el REJECT de NEXUS: antes exigia pid=101 aqui.
    # scripts/seal_agent_stability_guard.py:617 rechaza cualquier pid con un
    # estado != present_unique, y ADA lo explico: "present_ambiguous + pid"
    # afirma que ESE pid es el runtime autorizado, y eso NO esta probado
    # mientras quede un candidato ilegible.
    assert str(con_ilegible.get("process_pid")) in ("0", ""), (
        "con incertidumbre no se puede seleccionar un PID: el guard lo rechaza.\n"
        f"  process_pid={con_ilegible.get('process_pid')!r}")


def test_apuntar_a_proc_sintetico_no_puede_tocar_produccion():
    """CONTENCION FAIL-CLOSED del seam. Es el test de un INCIDENTE real.

    QUE PASO (29-ago 16:27): ALICE corrio este writer con JARVIS_PROC_ROOT
    apuntando a un /proc vacio -para verificar la contencion- sin las otras dos
    variables. El detector midio el /proc sintetico y el writer publico ESE
    resultado en el latido REAL: alive=false, absent, pid=0, sobre un JARVIS
    vivo. La sonda que venia a comprobar el aislamiento fue la que lo rompio.

    EL DEFECTO ERA DEL SEAM, NO DE QUIEN LO USO. Yo diseñe la contencion como
    OPT-IN por variable -PROC_ROOT para el detector, MESSAGES_DIR y HB_DRYRUN
    para las escrituras- y publique el seam nombrando solo la primera. Una
    costura cuya contencion hay que RECORDAR no es una costura, es una trampa.

    CONTENCION DE ESTE TEST, y es deliberada: uso HOME para que la ruta de
    "produccion" caiga dentro de un tmp. Si el test dependiera del guard que
    esta probando, fallar significaria ESCRIBIR el latido real -un test que
    daña produccion cuando encuentra el defecto no es un test, es el defecto
    con otro nombre.
    """
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as pr:
        prod = pathlib.Path(home) / "IA" / "proyecto-seal" / "messages"
        prod.mkdir(parents=True)
        centinela = prod / "jarvis_claude_heartbeat.json"
        centinela.write_text('{"agent":"JARVIS","alive":true,"CENTINELA":1}\n')
        antes = centinela.read_text()

        # EXACTAMENTE la llamada de ALICE: solo JARVIS_PROC_ROOT.
        r = subprocess.run(
            ["bash", str(WRITER)], check=False, capture_output=True, text=True, timeout=180,
            env={**os.environ, "HOME": home, "JARVIS_PROC_ROOT": pr},
        )
        despues = centinela.read_text()

    assert despues == antes, (
        "EL WRITER PISO PRODUCCION con un /proc sintetico.\n"
        f"  antes={antes!r}\n  despues={despues!r}\n"
        "  El guard fail-closed de JARVIS_PROC_ROOT no actuo.")
    assert "DRYRUN" in r.stderr or "desvio la salida" in r.stderr, (
        f"el guard no dejo rastro en stderr; stderr={r.stderr[:400]!r}")


def test_un_rc_fuera_de_contrato_del_guard_NO_publica_estado():
    """FAIL-CLOSED del `case` que decide si el writer puede escribir.

    Hallado por NEXUS el 4-sep sobre este manifiesto. El `case "$?"` manejaba SOLO
    `3)` y `0)`, asi que cualquier otro rc caia FUERA del case y el script seguia
    derecho al `cat > "$HB_JSON"`. Con /proc sintetico y HB_JSON apuntando a
    produccion, ESCRIBIA EN PRODUCCION -- el mismo dano que el guard existe para
    evitar (incidente del 29-ago: alive=false publicado sobre un JARVIS vivo).

    El contrato de `_hb_apunta_a` declara 0=dentro, 1=fuera, 3=no canonizable. Un
    cuarto valor significa que la funcion cambio bajo nuestros pies: ante eso NO se
    escribe. Este test fuerza ese cuarto valor y exige el aborto.

    CONTENCION, igual que el test del /proc sintetico: HOME apunta a un tmp que
    PRESERVA la topologia, asi que el writer carga el seat_lib de laboratorio y la
    "produccion" de este caso vive dentro del tmp. Si el guard fallara, el centinela
    de abajo lo detecta sin tocar el arbol real.
    """
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as pr:
        raiz = pathlib.Path(home) / "IA" / "proyecto-seal"
        (raiz / "messages").mkdir(parents=True)
        (raiz / "agents" / "JARVIS").mkdir(parents=True)

        # seat_lib de laboratorio: identico salvo que el guard devuelve un rc FUERA
        # de contrato. Es la mutacion de NEXUS, aplicada a una COPIA.
        original = SEAT_LIB.read_text(encoding="utf-8")
        roto = original.replace(
            'case "$2" in "$r"/*) return 0 ;; *) return 1 ;; esac',
            "return 2",
            1,
        )
        assert roto != original, "la mutacion NO se aplico: el test no probaria nada"
        (raiz / "agents" / "JARVIS" / "seat_lib.sh").write_text(roto, encoding="utf-8")

        centinela = raiz / "messages" / "jarvis_claude_heartbeat.json"
        centinela.write_text('{"agent":"JARVIS","CENTINELA":1}\n', encoding="utf-8")
        antes = centinela.read_text(encoding="utf-8")

        r = subprocess.run(
            ["bash", str(WRITER)], check=False, capture_output=True, text=True, timeout=180,
            env={**os.environ, "HOME": home, "JARVIS_PROC_ROOT": pr},
        )
        despues = centinela.read_text(encoding="utf-8")

    assert despues == antes, (
        "EL WRITER ESCRIBIO con un rc fuera de contrato: el `case` no es fail-closed.\n"
        f"  antes={antes!r}\n  despues={despues!r}"
    )
    assert r.returncode != 0, f"deberia abortar; rc={r.returncode}"


def test_seat_lib_que_carga_pero_no_define_el_detector_NO_publica_estado():
    """La guarda HERMANA de la anterior, y la que nadie cubria (linea 82).

    Hallada por NEXUS el 4-sep, DESPUES de cerrar las dos primeras. Son dos guardas
    parecidas y separarlas importa:

        linea 76   seat_lib.sh NO CARGA           -> exit 3   (ya tenia test)
        linea 82   carga pero falta la funcion    -> exit 3   (sin test hasta hoy)

    El segundo es el caso raro: el `source` NO falla -una edicion a medias, un
    `return` temprano, un rename- y el writer sigue con el detector ausente,
    publicando un estado calculado SIN medir. Es el dano del 29-ago otra vez:
    un latido que afirma sobre un asiento que nadie miro.

    Contencion por HOME sintetico con topologia, igual que las hermanas.
    """
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as pr:
        raiz = pathlib.Path(home) / "IA" / "proyecto-seal"
        (raiz / "messages").mkdir(parents=True)
        (raiz / "agents" / "JARVIS").mkdir(parents=True)

        # seat_lib que CARGA LIMPIO pero no define jarvis_seat_pids: se renombra la
        # funcion. `source` devuelve 0 y el writer cree tener detector.
        original = SEAT_LIB.read_text(encoding="utf-8")
        sin_detector = original.replace("jarvis_seat_pids()", "jarvis_seat_pids_RENOMBRADA()", 1)
        assert sin_detector != original, "la mutacion NO se aplico: el test no probaria nada"
        (raiz / "agents" / "JARVIS" / "seat_lib.sh").write_text(sin_detector, encoding="utf-8")

        centinela = raiz / "messages" / "jarvis_claude_heartbeat.json"
        centinela.write_text('{"agent":"JARVIS","CENTINELA":1}\n', encoding="utf-8")
        antes = centinela.read_text(encoding="utf-8")

        r = subprocess.run(
            ["bash", str(WRITER)], check=False, capture_output=True, text=True, timeout=180,
            env={**os.environ, "HOME": home, "JARVIS_PROC_ROOT": pr},
        )
        despues = centinela.read_text(encoding="utf-8")

    assert despues == antes, (
        "EL WRITER PUBLICO sin detector: la guarda de la linea 82 no corto.\n"
        f"  antes={antes!r}\n  despues={despues!r}"
    )
    assert r.returncode == 3, f"esperaba rc=3 del guard; rc={r.returncode}"
    assert "sin jarvis_seat_pids" in r.stderr, f"stderr sin la razon: {r.stderr[:300]!r}"
