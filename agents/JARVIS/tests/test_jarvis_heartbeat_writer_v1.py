"""Contrato del WRITER de latido: messages/jarvis_heartbeat_update.sh.

PARTIDO DEL ARCHIVO ORIGINAL (29-ago, autorizado por ADA). Ver el docstring de
test_jarvis_seat_lib_v1.py para el motivo del corte.

QUE VIVE ACA: los tres casos que ejercen el WRITER de punta a punta -no la
libreria-. Los tres nacieron de defectos reales de hoy: la rama muerta que
encontro NEXUS, el latido que ALICE piso con un /proc sintetico, y el pid que el
Stability Guard rechaza con un estado != present_unique.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import tempfile
from pathlib import Path

# COSTURA DE SUJETO INTERCAMBIABLE (31-ago). Sin esto la suite resuelve su sujeto
# por SU PROPIA ubicacion: el revisor copia el sujeto a /tmp, apunta el override,
# y la suite sigue midiendo PRODUCCION -- su mutante queda inerte y el veredicto
# "sobrevive M5" es falso. Medido esta noche sobre 108 suites: solo 6 tenian costura.
#     JARVIS_WRITER_PATH=/tmp/arbol/messages/jarvis_heartbeat_update.sh \
#     JARVIS_SEAT_LIB_PATH=/tmp/arbol/agents/JARVIS/seat_lib.sh \
#     python3 -m pytest <este archivo>
# Y el WRITER resuelve su propia dependencia por $HOME -- verificado por efecto:
# con HOME apuntado a un arbol replicado, sourcea LA COPIA (marcador presente);
# con HOME real, produccion (marcador ausente). O sea que el sujeto YA era
# atacable; lo que no lo era era esta suite.
ROOT = Path(__file__).resolve().parents[3]  # tests -> JARVIS -> agents -> raiz
SEAT_LIB = Path(os.environ.get("JARVIS_SEAT_LIB_PATH", str(ROOT / "agents" / "JARVIS" / "seat_lib.sh")))



WRITER = Path(os.environ.get("JARVIS_WRITER_PATH", str(ROOT / "messages" / "jarvis_heartbeat_update.sh")))

def _self_test(script: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script), "--self-test"],
        check=False, capture_output=True, text=True, timeout=180,
    )


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


def _correr_writer(proc_root, out_dir):
    """Corre el writer COMPLETO en dryrun y devuelve el JSON que publica."""
    subprocess.run(
        ["bash", str(WRITER)], check=False, capture_output=True, text=True, timeout=180,
        env={**os.environ, "JARVIS_PROC_ROOT": str(proc_root),
             "JARVIS_MESSAGES_DIR": str(out_dir), "JARVIS_HB_DRYRUN": "1"},
    )
    return json.loads((pathlib.Path(out_dir) / "jarvis_claude_heartbeat.json").read_text())


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


# (3-sep-2026) Acá había `WRITER = ROOT / "messages" / ...` que PISABA la costura de arriba:
# con JARVIS_WRITER_PATH puesto, la suite seguía midiendo producción y todo mutante quedaba inerte.
# Medido: 3 mutantes del writer «sobrevivieron» hasta quitar esta línea; con ella fuera, 3/3 mueren.


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

def test_bash_is_available():
    """CONTROL DE LA FIXTURE: si no hay bash, los casos de arriba serian no-ops
    que se leen como verdes."""
    assert shutil.which("bash"), "sin bash, este archivo no prueba nada"


# ── Ocupación de contexto en el latido (3-sep-2026) ──────────────────────────────────
# ALICE estuvo 6,4 h muda con proceso vivo y latido fresco: su barra de tmux decía
# «100% context used». El latido debe llevar ese número. Los tests apuntan el writer a
# un MEDIDOR DE PRUEBA por JARVIS_CONTEXT_METER (el árbol temporal no tiene medidor), y
# son diferenciales: el de éxito exige EL número (no «int o null»), el de fallo exige
# null con rc 0, y el de 100 % exige que no se recorte ni se descarte.

def _stub_meter(dirpath, body: str) -> str:
    stub = Path(dirpath) / "meter_stub.py"
    stub.write_text(body, encoding="utf-8")
    return str(stub)


def _correr_writer_con_medidor(proc_root, out_dir, meter_path) -> tuple[dict, int]:
    r = subprocess.run(
        ["bash", str(WRITER)], capture_output=True, text=True, timeout=60,
        env={**os.environ, "JARVIS_PROC_ROOT": str(proc_root), "JARVIS_MESSAGES_DIR": str(out_dir),
             "JARVIS_HB_DRYRUN": "1", "JARVIS_CONTEXT_METER": meter_path},
    )
    data = json.loads((Path(out_dir) / "jarvis_claude_heartbeat.json").read_text(encoding="utf-8"))
    return data, r.returncode


def test_context_percent_es_el_numero_del_medidor():
    with tempfile.TemporaryDirectory() as pr, tempfile.TemporaryDirectory() as o:
        _mkproc(pr, 101, "claude", ["claude", "--name", "JARVIS"], {"SEAL_AGENT": "JARVIS"})
        meter = _stub_meter(o, "print('#[fg=colour220]⚕ JARVIS │ 773K/950K auto │ [████████░░] 81% │ 35s#[default]', end='')\n")
        data, rc = _correr_writer_con_medidor(pr, o, meter)
    assert rc == 0
    assert data["context_percent"] == 81, data.get("context_percent")
    assert data["context_source"] == "seal_context_meter"


def test_medidor_roto_da_null_y_no_cambia_alive_ni_rc():
    with tempfile.TemporaryDirectory() as pr, tempfile.TemporaryDirectory() as o:
        _mkproc(pr, 101, "claude", ["claude", "--name", "JARVIS"], {"SEAL_AGENT": "JARVIS"})
        casos = {
            "explota": "import sys; sys.exit(3)\n",
            "basura": "print('sin porcentaje aca', end='')\n",
            "fuera_de_rango": "print('⚕ JARVIS │ 999% │', end='')\n",
            "cuelga": "import time; time.sleep(30)\n",
        }
        for nombre, body in casos.items():
            data, rc = _correr_writer_con_medidor(pr, o, _stub_meter(o, body))
            assert rc == 0, f"{nombre}: el medidor no puede tumbar el writer (rc={rc})"
            assert data["context_percent"] is None, f"{nombre}: {data.get('context_percent')!r}"
            assert data["context_source"] == "unavailable", nombre
            assert data["alive"] is True, f"{nombre}: alive cambió por culpa del medidor"
        # Medidor ausente del todo (ruta inexistente): mismo contrato.
        data, rc = _correr_writer_con_medidor(pr, o, str(Path(o) / "no_existe.py"))
        assert rc == 0 and data["context_percent"] is None and data["context_source"] == "unavailable"


def test_cien_por_ciento_se_escribe_como_100():
    # El caso que motivó el campo: 100 % debe llegar como 100, no recortado ni descartado.
    with tempfile.TemporaryDirectory() as pr, tempfile.TemporaryDirectory() as o:
        _mkproc(pr, 101, "claude", ["claude", "--name", "JARVIS"], {"SEAL_AGENT": "JARVIS"})
        meter = _stub_meter(o, "print('⚕ JARVIS │ 950K/950K auto │ [██████████] 100% │ 2s', end='')\n")
        data, rc = _correr_writer_con_medidor(pr, o, meter)
    assert rc == 0 and data["context_percent"] == 100 and data["context_source"] == "seal_context_meter"


def test_brazo_guard_kitty_no_publica_pid():
    """BRAZO — guarda pid: el pid asignado por el fallback kitty se BORRA antes de publicar.

    Este es el único camino donde la guarda de la línea 380 borra un pid NO vacío:
      1. /proc sintético vacío  →  _JARVIS_N=0, _JARVIS_ILEG=0
      2. Fallback kitty (lns 241-248) detecta pgrep→kitty→bash→claude y asigna
         JARVIS_PID="<pid>" con JARVIS_RUNTIME="claude_kitty"
      3. case claude_kitty → RUNTIME_STATUS="indeterminate"
      4. Guarda: [ "indeterminate" = "present_unique" ] || JARVIS_PID=""  <- dispara aquí
      5. JARVIS_PID="" antes de escribir el JSON

    Sin la guarda (mutante: quitar o neutralizar la línea 380), el pid del proceso
    claude bajo kitty se publicaría con status=indeterminate → violation del contrato.

    Fixture: pgrep y ps inyectados por PATH para simular la cadena kitty→bash→claude.
    Control del instrumento: si JARVIS_RUNTIME no es claude_kitty, la fixture no vale
    y el assert de status falla primero.
    """
    FAKE_KITTY = "99901"
    FAKE_BASH  = "99902"
    FAKE_CLAUD = "99903"

    with tempfile.TemporaryDirectory() as pr, \
         tempfile.TemporaryDirectory() as o, \
         tempfile.TemporaryDirectory() as bintmp:
        # Proc raíz vacío → N=0, ILEG=0 → activa fallback kitty
        binpath = pathlib.Path(bintmp)

        # pgrep sintético: intercepta búsqueda de kitty, pasa el resto al real
        pgrep_sh = binpath / "pgrep"
        pgrep_sh.write_text(
            f"#!/bin/sh\n"
            f"for a in \"$@\"; do case \"$a\" in *kitty*) echo {FAKE_KITTY}; exit 0;; esac; done\n"
            f"exec \"$(command -v pgrep 2>/dev/null || echo /usr/bin/pgrep)\" \"$@\" 2>/dev/null\n",
            encoding="utf-8",
        )
        pgrep_sh.chmod(0o755)

        # ps sintético: devuelve la cadena kitty→bash→claude para nuestros pids
        ps_sh = binpath / "ps"
        ps_sh.write_text(
            f"#!/bin/sh\n"
            f"ppid=''; prev=''\n"
            f"for a in \"$@\"; do [ \"$prev\" = '--ppid' ] && ppid=\"$a\"; prev=\"$a\"; done\n"
            f"if [ \"$ppid\" = '{FAKE_KITTY}' ]; then echo {FAKE_BASH}; exit 0; fi\n"
            f"if [ \"$ppid\" = '{FAKE_BASH}'  ]; then echo '{FAKE_CLAUD} claude'; exit 0; fi\n"
            f"exec \"$(command -v ps 2>/dev/null || echo /bin/ps)\" \"$@\" 2>/dev/null\n",
            encoding="utf-8",
        )
        ps_sh.chmod(0o755)

        new_path = f"{bintmp}:{os.environ.get('PATH', '/usr/bin:/bin')}"
        subprocess.run(
            ["bash", str(WRITER)],
            check=False, capture_output=True, text=True, timeout=180,
            env={**os.environ,
                 "JARVIS_PROC_ROOT": str(pr),
                 "JARVIS_MESSAGES_DIR": str(o),
                 "JARVIS_HB_DRYRUN": "1",
                 "PATH": new_path},
        )
        data = json.loads((pathlib.Path(o) / "jarvis_claude_heartbeat.json").read_text())

    # Control del instrumento: si no es indeterminate (claude_kitty), la fixture falló
    assert "indeterminate" in data["runtime_detection_status"], (
        f"fixture no activó el camino kitty: status={data['runtime_detection_status']!r}\n"
        f"  esperado: runtime_detection_status contiene 'indeterminate' (via claude_kitty)\n"
        f"  verificar: pgrep/ps sintéticos accesibles por PATH={bintmp}")
    # La guarda de la línea 380 debe haber limpiado el pid que asignó el fallback
    assert str(data.get("process_pid", "0")) in ("0", ""), (
        f"pid del claude-bajo-kitty NO debe publicarse con status=indeterminate: "
        f"process_pid={data.get('process_pid')!r}\n"
        f"  guarda (línea 380): [ \"$RUNTIME_STATUS\" = \"present_unique\" ] || JARVIS_PID=\"\"\n"
        f"  sin la guarda, {FAKE_CLAUD!r} saldría publicado con status=indeterminate"
    )
