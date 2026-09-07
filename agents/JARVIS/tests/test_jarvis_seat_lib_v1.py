"""Contrato del DETECTOR de asiento: agents/JARVIS/seat_lib.sh.

PARTIDO DEL ARCHIVO ORIGINAL (29-ago, autorizado por ADA): un solo manifiesto
cubria libreria y writer, y HOY los reviso gente distinta -ALICE el detector,
NEXUS el writer-, cada uno de verdad y cada uno una mitad. El esquema admite UN
revisor, asi que el trabajo estaba hecho y no entraba en el formato.

Condicion de ADA que manda este corte: la unidad del manifest debe coincidir con
la unidad REALMENTE revisada. Un test que cubre los dos sujetos solo puede estar
en los dos manifiestos si los DOS revisores firmaron esos bytes; si no, se separa.
Por eso este archivo se queda solo con lo que ejerce `seat_lib.sh`.
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

def test_hb_apunta_a_resuelve_LOS_DOS_LADOS():
    """El guard de contencion compara DESTINOS, no ortografias.

    ROJO PRIMERO: el codigo anterior resolvia solo el destino y comparaba contra
    un literal crudo. Con un COMPONENTE SYMLINK en la raiz productiva, el
    resuelto no matchea el literal y el guard SE ABRE -- deja escribir en
    produccion desde una corrida con /proc sintetico.

    Este test no llama al writer: llama a la funcion con una raiz de
    LABORATORIO. Ese seam por parametro es de FABLE (30-ago 06:19); mi version
    previa lo hacia por variable de entorno, y esa variable era un hueco nuevo.

    CONTROL DE DISCRIMINACION incluido: se calcula tambien el veredicto del
    criterio VIEJO sobre el MISMO caso. Sin el, un verde aca no probaria que el
    fix hace algo -- que es el error que cometi dos veces esta madrugada.
    """
    import subprocess as sp
    with tempfile.TemporaryDirectory() as lab:
        real = pathlib.Path(lab) / "real" / "messages"
        real.mkdir(parents=True)
        (pathlib.Path(lab) / "link").symlink_to(pathlib.Path(lab) / "real")
        raiz_symlink = f"{lab}/link/messages"
        destino = sp.run(["realpath", "-m", str(real / "hb.json")],
                         capture_output=True, text=True, check=True).stdout.strip()

        def llamar(raiz: str) -> int:
            return sp.run(["bash", "-c",
                           f'. "{SEAT_LIB}"; _hb_apunta_a "$1" "$2"', "_", raiz, destino],
                          capture_output=True, text=True, timeout=60).returncode

        # CONTROL VIEJO: literal crudo, sin resolver la raiz.
        viejo_detecta = destino.startswith(raiz_symlink + "/")
        assert not viejo_detecta, (
            "CONTROL INVALIDO: el criterio viejo ya detectaba este caso, "
            "asi que un verde del nuevo no probaria nada")

        assert llamar(raiz_symlink) == 0, (
            "el guard NO detecto un destino dentro de la raiz declarada via symlink: "
            "se abre y deja escribir en produccion")
        assert llamar(str(real)) == 0, "raiz canonica: deberia detectar que cae dentro"
        assert llamar(f"{lab}/otra") == 1, "raiz ajena: deberia dar 1, no 0 ni 3"
