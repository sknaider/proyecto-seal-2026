"""Contrato de detección de asiento en `messages/nexus_heartbeat_update.sh`.

Este archivo decide si el equipo me ve vivo. Sus dos defectos corregidos el
29-ago tienen acá su caso que los mata:

1. `head -1` desempataba a ciegas: con DOS procesos `--name NEXUS` anclaba el
   latido a un PID arbitrario. **Ambigüedad no es "el primero": es no medible.**
2. La muestra del asiento iba DESPUÉS de dos llamadas a `nvidia-smi`, y en ese
   hueco un relevo de proceso hacía escribir `alive=false` sobre un agente vivo.

## Las dos costuras del test, declaradas

- **`ps`, `pgrep` y `nvidia-smi` se sustituyen por stubs en el `PATH`.** El
  sujeto se corre SIN modificar: sólo cambia lo que el sistema le responde.
- **`$VENV` es una ruta ABSOLUTA al intérprete de producción**, y ejecutarla
  escribiría un latido REAL en `event_log`. Por eso el test corre sobre una
  copia con **exactamente una línea** distinta, y lo verifica: si alguien toca
  otra cosa del script, `test_la_copia_difiere_en_una_sola_linea` se pone rojo.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SUJETO = REPO / "messages" / "nexus_heartbeat_update.sh"

VENV_REAL = 'VENV="/home/dadito/IA/seal-spark/.venv/bin/python3"'
VENV_STUB = 'VENV="/bin/true"'


def _copia_sin_escritura_a_produccion(destino: Path) -> Path:
    original = SUJETO.read_text(encoding="utf-8")
    assert VENV_REAL in original, (
        "el sujeto ya no declara el venv esperado; revisá la costura antes de confiar"
    )
    destino.write_text(original.replace(VENV_REAL, VENV_STUB), encoding="utf-8")
    destino.chmod(0o755)
    return destino


def _stub(directorio: Path, nombre: str, cuerpo: str) -> None:
    p = directorio / nombre
    p.write_text("#!/bin/bash\n" + cuerpo + "\n", encoding="utf-8")
    p.chmod(0o755)


def _sembrar_proc(root: Path, pid: int, *, comm: str, argv: list[str], seal_agent: str | None) -> None:
    """Crea /proc/<pid>/{comm,cmdline,environ} con el formato REAL (NUL)."""
    d = root / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "comm").write_text(comm + "\n", encoding="utf-8")
    (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
    env = [] if seal_agent is None else [f"SEAL_AGENT={seal_agent}"]
    (d / "environ").write_bytes(b"\0".join(e.encode() for e in env) + (b"\0" if env else b""))


def _correr(tmp_path: Path, procesos: list[dict] | None = None) -> tuple[dict, str]:
    """Corre el sujeto contra un /proc SINTETICO. `procesos` = lista de dicts."""
    bins = tmp_path / "bin"
    bins.mkdir(parents=True, exist_ok=True)
    _stub(bins, "pgrep", "exit 1")
    _stub(bins, "nvidia-smi", "echo 41")

    proc = tmp_path / "proc"
    proc.mkdir(parents=True, exist_ok=True)
    for i, spec in enumerate(procesos or []):
        _sembrar_proc(
            proc,
            spec.get("pid", 4000 + i),
            comm=spec.get("comm", "claude"),
            argv=spec["argv"],
            seal_agent=spec.get("seal_agent", "NEXUS"),
        )

    home = tmp_path / "home"
    (home / "IA" / "proyecto-seal" / "memory").mkdir(parents=True, exist_ok=True)
    (home / "IA" / "proyecto-seal" / "messages").mkdir(parents=True, exist_ok=True)

    script = _copia_sin_escritura_a_produccion(tmp_path / "hb.sh")
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["PROC_ROOT"] = str(proc)
    env["PATH"] = f"{bins}:{env.get('PATH','')}"

    r = subprocess.run(
        ["bash", str(script)], env=env, capture_output=True, text=True, timeout=60
    )
    escrito = home / "IA" / "proyecto-seal" / "messages" / "nexus_claude_heartbeat.json"
    assert escrito.is_file(), f"el sujeto no escribió el latido: {r.stderr}"
    return json.loads(escrito.read_text(encoding="utf-8")), r.stderr


def test_la_copia_difiere_en_una_sola_linea(tmp_path):
    """La costura no puede crecer sin que alguien la vea."""
    copia = _copia_sin_escritura_a_produccion(tmp_path / "hb.sh")
    a = SUJETO.read_text(encoding="utf-8").splitlines()
    b = copia.read_text(encoding="utf-8").splitlines()
    assert len(a) == len(b)
    distintas = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
    assert len(distintas) == 1, f"la copia difiere en {len(distintas)} líneas, no en 1"


def test_la_corrida_no_toca_el_artefacto_VIVO(tmp_path):
    """La segunda costura, y no la cubre la de arriba (idea de FABLE, 29-ago).

    `test_la_copia_difiere_en_una_sola_linea` prueba INTEGRIDAD DEL MUTANTE.
    Esto prueba CONTENCIÓN DEL EFECTO: si alguien saca el sandbox de `HOME`,
    los demás tests siguen verdes **escribiendo el latido real** — que es
    justamente el artefacto que dice si estoy vivo.
    """
    vivo = REPO / "messages" / "nexus_claude_heartbeat.json"
    antes = vivo.stat().st_mtime_ns if vivo.exists() else None

    _correr(tmp_path, [{"pid": 4242, "argv": ["claude", "--name", "NEXUS"]}])

    despues = vivo.stat().st_mtime_ns if vivo.exists() else None
    assert antes == despues, (
        "la corrida de prueba modificó el latido VIVO: el aislamiento no aisló"
    )


def test_un_solo_asiento_ancla_el_latido(tmp_path):
    """VERDE: un proceso `--name NEXUS` -> vivo, anclado a ESE pid."""
    hb, _ = _correr(tmp_path, [{"pid": 4242, "argv": ["claude", "--name", "NEXUS", "--model", "opus"]}])
    assert hb["alive"] is True
    assert hb["runtime"] == "claude_named"
    assert hb["process_pid"] == "4242"


def test_dos_asientos_no_anclan_nada(tmp_path):
    """ROJO que mata el `head -1`: con DOS, el latido NO elige uno."""
    hb, err = _correr(tmp_path, [
        {"pid": 4242, "argv": ["claude", "--name", "NEXUS"]},
        {"pid": 4243, "argv": ["claude", "--name", "NEXUS"]},
    ])
    assert hb["process_pid"] == "0", "ancló un PID sobre una medición ambigua"
    # `alive=True`: dos asientos prueban EXISTENCIA aunque no identidad.
    # Este assert decía `is False` y estaba VERDE porque el código tenía el
    # mismo defecto: el test defendía la proyección equivocada. Corregido al
    # contrato de ADA cuando FABLE encontró el defecto en el sujeto (16:19).
    assert hb["alive"] is True
    assert "AMBIGUO" in err, "la ambigüedad no se reportó: quedaría muda"


def test_sin_asiento_reporta_ausencia(tmp_path):
    """ROJO: ningún proceso -> ausencia declarada, no un pid inventado."""
    hb, _ = _correr(tmp_path, [])
    assert hb["alive"] is False
    assert hb["runtime"] == "none"
    assert hb["process_pid"] == "0"


def test_no_confunde_a_otro_agente_con_NEXUS(tmp_path):
    """Un asiento de ALICE no puede contarme a mí como vivo."""
    hb, _ = _correr(tmp_path, [{"pid": 9001, "argv": ["claude", "--name", "ALICE"], "seal_agent": "ALICE"}])
    assert hb["alive"] is False, "matcheó el asiento de otro agente"


@pytest.mark.parametrize(
    "candidato",
    ["NEXUS-CLONE", "NEXUS-u103", "NEXUSX"],
)
def test_un_clon_no_es_mi_asiento(tmp_path, candidato):
    """Un `--name NEXUS-CLONE` NO es mi asiento — la autoridad lo rechaza.

    Encontrado revisando el detector de ALICE (29-ago): su patrón era más
    ESTRICTO que la autoridad y el mío es más PERMISIVO. Mi `awk /--name NEXUS/`
    no tiene frontera, así que matchea cualquier sufijo.

    Daño: con un clon corriendo y sin mi asiento -> `alive=true` anclado al PID
    del clon. Con clon Y asiento -> AMBIGUO -> `alive=false` sobre NEXUS viva.

    Re-derivable contra la autoridad:
      python3 -c "import importlib.util,sys;..."
      sup._argv_has_agent_name(["--name","NEXUS-CLONE"],"NEXUS")  -> False
    """
    hb, _ = _correr(tmp_path, [{"pid": 4242, "argv": ["claude", "--name", candidato]}])
    assert hb["alive"] is False, f"conté {candidato} como mi asiento"
    assert hb["process_pid"] == "0"


def test_una_MENCION_no_es_un_asiento(tmp_path):
    """El asiento de OTRO agente que MENCIONA `--name NEXUS` no es mío.

    Hallazgo de FABLE revisándome (29-ago). Mi patrón corría sobre la línea
    ENTERA de `ps`, no sobre el argumento de `--name`: el prompt de ALICE que
    cita el contrato contaba como asiento mío.

    Daño: con mi asiento real presente, N=2 -> AMBIGUO -> `alive=false` sobre
    NEXUS viva. Es la misma clase que le advertí a JARVIS por la mañana —un
    patrón sobre `cmdline` matchea a quien MENCIONA— y la tenía yo.

    La autoridad, sobre el argv real, dice False.
    """
    hb, _ = _correr(tmp_path, [{
        "pid": 9001,
        "argv": ["claude", "--name", "ALICE", "--system-prompt",
                 "se invoca con --name NEXUS y listo"],
        "seal_agent": "ALICE",
    }])
    assert hb["alive"] is False, "una mención contó como mi asiento"
    assert hb["process_pid"] == "0"


def test_los_tres_estados_son_distinguibles(tmp_path):
    """El control que hace valer a los anteriores: los 3 casos NO dan lo mismo.

    Sin esto, tres asserts podrían pasar contra un escritor que devuelve
    siempre `alive=false`.
    """
    uno, _ = _correr(tmp_path / "a", [{"pid": 4242, "argv": ["claude", "--name", "NEXUS"]}])
    dos, _ = _correr(tmp_path / "b", [{"pid": 1, "argv": ["claude", "--name", "NEXUS"]}, {"pid": 2, "argv": ["claude", "--name", "NEXUS"]}])
    cero, _ = _correr(tmp_path / "c", [])

    firmas = {
        (uno["alive"], uno["process_pid"]),
        (dos["alive"], dos["process_pid"]),
        (cero["alive"], cero["process_pid"]),
    }
    assert len(firmas) >= 2, f"el escritor no discrimina: {firmas}"
    assert (uno["alive"], uno["process_pid"]) not in {
        (dos["alive"], dos["process_pid"]),
        (cero["alive"], cero["process_pid"]),
    }, "el caso vivo no se distingue de los muertos"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


# ---------------------------------------------------------------------------
# COBERTURA DEL ORACULO — el arm que ningun manifiesto nuestro tenia (FABLE).
#
# Los tres nos encontramos el mismo defecto revisandonos el ORACULO, no el
# writer: FABLE llamaba 2 de 3 predicados de la autoridad, yo 1 de 4, ALICE 0.
# **El predicado que no llamas no puede contradecirte, asi que sale VERDE.**
#
# `scan_primary_runtimes(agent, proc_root)` es la decision COMPLETA y acepta el
# proc_root, asi que corre sobre la MISMA fixture que mi detector. Ya no comparo
# contra un predicado que elegi yo: comparo contra lo que la autoridad concluye.
# ---------------------------------------------------------------------------

def _autoridad_pids(proc_root: Path, agente: str = "NEXUS") -> list[int]:
    import importlib.util

    aut = REPO / "tools" / "seal_agent_runtime_supervisor.py"
    spec = importlib.util.spec_from_file_location("sup_oraculo", aut)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sup_oraculo"] = mod
    spec.loader.exec_module(mod)
    return sorted(mod.primary_runtime_pids(agente, proc_root=proc_root))


CASOS_ORACULO = [
    ("asiento legitimo", [{"pid": 4242, "argv": ["claude", "--name", "NEXUS"]}]),
    ("forma --name=",    [{"pid": 4243, "argv": ["claude", "--name=NEXUS"]}]),
    ("clon",             [{"pid": 4244, "argv": ["claude", "--name", "NEXUS-CLONE"]}]),
    ("otro agente",      [{"pid": 4245, "argv": ["claude", "--name", "ALICE"],
                           "seal_agent": "ALICE"}]),
    ("mencion en prompt", [{"pid": 4246,
                            "argv": ["claude", "--name", "ALICE", "--system-prompt",
                                     "se invoca con --name NEXUS"],
                            "seal_agent": "ALICE"}]),
    ("worker stream-json", [{"pid": 4247,
                             "argv": ["claude", "--name", "NEXUS",
                                      "--output-format", "stream-json"]}]),
    ("SEAL_AGENT ausente", [{"pid": 4248, "argv": ["claude", "--name", "NEXUS"],
                             "seal_agent": None}]),
    ("SEAL_AGENT de otro", [{"pid": 4249, "argv": ["claude", "--name", "NEXUS"],
                             "seal_agent": "ALICE"}]),
    ("comm distinto",     [{"pid": 4250, "comm": "bash",
                            "argv": ["claude", "--name", "NEXUS"]}]),
    ("raya em",           [{"pid": 4251, "argv": ["claude", "--name", "NEXUS—Team"]}]),
    ("NBSP U+00A0",       [{"pid": 4252, "argv": ["claude", "--name", "NEXUS Team"]}]),
    ("NNBSP U+202F",      [{"pid": 4253, "argv": ["claude", "--name", "NEXUS Team"]}]),
]


def test_mi_detector_no_diverge_de_la_decision_COMPLETA_de_la_autoridad(tmp_path):
    """Compara contra `scan_primary_runtimes`, no contra un predicado elegido.

    Cubre las CUATRO condiciones que mi detector decide (comm, --name,
    SEAL_AGENT, worker), no sólo la del nombre. Es el arm que FABLE señaló que
    ninguno de los tres manifiestos tenía.
    """
    divergencias = []
    acepta = rechaza = 0
    for nombre, procesos in CASOS_ORACULO:
        base = tmp_path / nombre.replace(" ", "_")
        hb, _ = _correr(base, procesos)
        esperado = _autoridad_pids(base / "proc")
        mio = [int(hb["process_pid"])] if hb["process_pid"] != "0" else []
        if esperado:
            acepta += 1
        else:
            rechaza += 1
        if sorted(mio) != esperado:
            divergencias.append((nombre, esperado, mio))

    # CONTROL: sin casos aceptados Y rechazados, cero divergencias no prueba nada.
    assert acepta > 0 and rechaza > 0, (
        f"el oráculo no discrimina: acepta={acepta} rechaza={rechaza}"
    )
    assert not divergencias, f"divergencias con la autoridad: {divergencias}"


def _autoridad_scan(proc_root: Path, agente: str = "NEXUS"):
    """La autoridad COMPLETA, no su proyección `primary_runtime_pids`.

    Esa proyección declara en su docstring que es «for read-only callers that
    only need known PIDs»: descarta `unreadable_candidates`. Usarla como oráculo
    hace que "no pude medir" y "no hay" se vean iguales — la MISMA omisión que
    tenía mi detector, así que las dos partes coincidían estando equivocadas.
    """
    import importlib.util

    aut = REPO / "tools" / "seal_agent_runtime_supervisor.py"
    spec = importlib.util.spec_from_file_location("sup_scan", aut)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sup_scan"] = mod
    spec.loader.exec_module(mod)
    return mod.scan_primary_runtimes(agente, proc_root=proc_root)


def test_un_candidato_ILEGIBLE_no_se_reporta_como_ausencia(tmp_path):
    """«No pude medir» no es «no hay» — y mi latido no puede decir que sí.

    Hallazgo mío sobre mí, disparado por el defecto de ORDEN que FABLE encontró
    en el suyo (29-ago 15:48). ALICE me reportó esta clase a las 15:18 en SU
    detector y yo se la confirmé; la tenía en el mío sin mirarlo.

    El control: la autoridad completa dice `determined=False`. Mi latido no
    puede publicar `alive=false` a secas sobre esa incertidumbre.
    """
    proc = tmp_path / "proc"
    _sembrar_proc(proc, 777, comm="claude",
                  argv=["claude", "--name", "NEXUS"], seal_agent="NEXUS")
    (proc / "777" / "comm").chmod(0o000)
    try:
        scan = _autoridad_scan(proc)
        assert list(scan.pids) == [], "fixture inválida: la autoridad no debía ver pids"
        assert list(scan.unreadable_candidates) == [777], (
            "fixture inválida: el candidato no quedó ilegible para la autoridad"
        )
        assert scan.determined is False, "fixture inválida: la autoridad no dudó"

        hb, err = _correr(tmp_path, [])   # el /proc lo sembramos nosotros arriba
        # el detector debe DECLARAR la incertidumbre, no silenciarla
        assert "INDETERMINADO" in err or hb.get("runtime") == "indeterminado", (
            f"un candidato ilegible se reportó como ausencia limpia: {hb} / {err!r}"
        )
    finally:
        (proc / "777" / "comm").chmod(0o644)


def test_un_ilegible_junto_a_un_asiento_NO_es_unicidad(tmp_path):
    """1 pid legible + 1 ILEGIBLE no es «único»: el ilegible podría ser el segundo.

    El caso anterior sólo cubría `0 pids + 1 ilegible`, así que revertir el fix
    lo dejaba VERDE — lo cazó el mutante, no yo. Es el mismo hueco que JARVIS
    describió en el suyo: *«consulto la incertidumbre SÓLO en la rama de la
    ausencia»*. La verdad acá no es ausencia ni unicidad: es que no puedo
    descartar un segundo asiento.
    """
    proc = tmp_path / "proc"
    _sembrar_proc(proc, 101, comm="claude",
                  argv=["claude", "--name", "NEXUS"], seal_agent="NEXUS")
    _sembrar_proc(proc, 777, comm="claude",
                  argv=["claude", "--name", "NEXUS"], seal_agent="NEXUS")
    (proc / "777" / "comm").chmod(0o000)
    try:
        scan = _autoridad_scan(proc)
        assert list(scan.pids) == [101] and list(scan.unreadable_candidates) == [777], (
            f"fixture inválida: {list(scan.pids)} / {list(scan.unreadable_candidates)}"
        )
        assert scan.determined is False, "la autoridad debía quedar INDETERMINADA"

        hb, err = _correr(tmp_path, [])
        assert hb["process_pid"] == "0", (
            f"ancló el pid 101 ignorando un candidato que no pudo descartar: {hb}"
        )
        assert "INDETERMINADO" in err, f"la incertidumbre quedó muda: {err!r}"
    finally:
        (proc / "777" / "comm").chmod(0o644)


def test_dos_asientos_y_cero_asientos_NO_publican_lo_mismo(tmp_path):
    """El publicador no puede tirar la distinción que el detector sí hace.

    Hallazgo de FABLE re-revisándome (29-ago 16:16). Mi detector distinguía
    AMBIGUO de AUSENTE —lo gritaba por stderr— y el JSON publicaba
    `alive=false runtime=none pid=0` en los dos casos. **El stderr va al
    journal; el JSON es lo que leen los monitores.**

    Es mi propia clase invertida: una hora antes le reporté a JARVIS que su
    librería decía AMBIGUO y su writer publicaba `present_unique`. Acá el que
    sabe es el detector y el que tira la información es el publicador.
    """
    dos = _correr(tmp_path / "dos", [
        {"pid": 101, "argv": ["claude", "--name", "NEXUS"]},
        {"pid": 102, "argv": ["claude", "--name", "NEXUS"]},
    ])[0]
    cero = _correr(tmp_path / "cero", [])[0]

    assert dos != cero, (
        f"dos asientos y cero asientos publican el MISMO JSON: {dos}"
    )
    assert dos.get("runtime_detection_status") == "present_ambiguous"
    assert cero.get("runtime_detection_status") == "absent"


def test_present_ambiguous_proyecta_alive_TRUE(tmp_path):
    """`alive` es proyección de compatibilidad del contrato de ADA (29-ago 12:17).

        present_unique     alive=true  pid>0
        present_ambiguous  alive=true  pid=0   <- prueba EXISTENCIA, no identidad
        absent             alive=false pid=0
        indeterminate      alive=false pid=0

    Yo arreglé el `status` y dejé la proyección vieja: publicaba
    `present_ambiguous` con `alive=false`, o sea **ausencia sobre dos asientos
    míos VIVOS**. Lo encontró FABLE re-atacando (16:19); no es criterio suyo
    —lo dice la tabla de ADA y lo implementa igual `alice_heartbeat_update.sh`.

    Es LATENTE: con un solo asiento hoy no dispara. Salta cuando de verdad hay
    dos, que es justo cuando el dato importa.
    """
    dos, _ = _correr(tmp_path, [
        {"pid": 101, "argv": ["claude", "--name", "NEXUS"]},
        {"pid": 102, "argv": ["claude", "--name", "NEXUS"]},
    ])
    assert dos["runtime_detection_status"] == "present_ambiguous"
    assert dos["alive"] is True, (
        "dos asientos VIVOS se publicaron como alive=false: la proyección "
        "contradice el contrato de ADA"
    )
    assert dos["process_pid"] == "0", "no debe anclar un pid sobre ambigüedad"
