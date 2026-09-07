#!/usr/bin/env python3
"""Tests del guard de KILL. Cada celda sale de un fallo REAL del 24-jul-2026.

La regla que ordena este archivo: un guard que sólo prueba el ataque bloquea el
mantenimiento legítimo. Por eso cada caso peligroso viene con su gemelo sano.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import seal_kill_guard as g  # noqa: E402


@pytest.fixture
def dormido():
    """Un proceso real y descartable, para no simular /proc."""
    procs = []

    def _crear(extra_argv: list[str] | None = None):
        argv = [sys.executable, "-c", "import time; time.sleep(30)"] + (extra_argv or [])
        p = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        procs.append(p)
        time.sleep(0.05)
        return p

    yield _crear
    for p in procs:
        if p.poll() is None:
            p.kill()
        p.wait(timeout=5)


def test_pid_inexistente_no_es_reintentable(dormido) -> None:
    """`437824` en la orden de hoy ya no existía. El veredicto tiene que decir
    're-derivar', no 'reintentar el numero'."""
    p = dormido()
    pid = p.pid
    p.kill(); p.wait(timeout=5)
    time.sleep(0.05)
    codigo, informe = g.verify(pid, expect_exe="python3")
    assert codigo == g.EXIT_NOT_FOUND
    assert "caduco" in informe["reason"] or "no existe" in informe["reason"]


def test_pid_vivo_pero_no_es_el_sujeto(dormido) -> None:
    """El caso `878074`: seguía VIVO, así que `kill -0` lo dejaba pasar — y era
    un bash de hacía seis días, no un asiento. 'Existe' no es 'es el sujeto'."""
    p = dormido()
    codigo, informe = g.verify(p.pid, expect_exe="claude", expect_name="JARVIS — Team SEAL")
    assert codigo == g.EXIT_MISMATCH, "un proceso vivo que NO es el sujeto tiene que abortar"
    assert "exe" in informe["checks"]
    assert informe["observed"]["exe"] != "claude"


def test_el_caso_legitimo_pasa(dormido) -> None:
    """El gemelo sano: si todo lo declarado coincide, el guard NO puede estorbar.
    Sin esta celda, 'no mata nunca' pasaría por 'guard correcto'."""
    p = dormido()
    codigo, informe = g.verify(
        p.pid,
        expect_exe=g.exe_real(p.pid),
        expect_starttime=g.starttime(p.pid),
        expect_ppid=g.ppid(p.pid),
        require_unique_seat=False,
    )
    assert codigo == g.EXIT_OK, f"el caso legitimo fue bloqueado: {informe['reason']}"
    assert informe["verdict"] == "OK"


def test_starttime_distingue_pid_reasignado(dormido) -> None:
    """Lo único que separa 'el pid que medí' de 'el pid que el kernel reasignó'.
    Sin este campo, un PID es un puntero con gramática de identidad."""
    p = dormido()
    real = g.starttime(p.pid)
    assert real is not None and real.isdigit()
    codigo, informe = g.verify(p.pid, expect_starttime=str(int(real) + 1),
                               require_unique_seat=False)
    assert codigo == g.EXIT_MISMATCH
    assert "starttime" in informe["checks"]


def test_ejecutor_dentro_del_arbol_aborta() -> None:
    """Un proceso no puede orquestar su propia sustitución: si el paso 1 lo mata,
    el resultado depende de a quién le llegue antes la señal."""
    codigo, informe = g.verify(os.getpid(), expect_exe=g.exe_real(os.getpid()))
    assert codigo == g.EXIT_SELF_IN_TREE
    assert informe["verdict"] == "SELF_IN_TREE"

    padre = g.ppid(os.getpid())
    if padre and padre > 1:
        codigo_padre, _ = g.verify(padre, executor_pid=os.getpid())
        assert codigo_padre == g.EXIT_SELF_IN_TREE, "un ANCESTRO tambien es el arbol propio"


def test_stat_no_se_corre_por_un_comm_con_espacios(tmp_path, monkeypatch) -> None:
    """Si el comm lleva espacios o paréntesis y se parte por espacios sin cuidado,
    todos los indices se corren y `starttime` deja de ser `starttime` — un numero
    plausible y equivocado, que es peor que un error."""
    falso = tmp_path / "4242"
    falso.mkdir()
    campos_cola = " ".join(str(i) for i in range(3, 53))
    (falso / "stat").write_text(f"4242 (mi proc (raro) ) S 99 {campos_cola}\n")
    monkeypatch.setattr(g, "PROC", tmp_path)
    campos = g._stat_fields(4242)
    assert campos[0] == "4242"
    assert campos[3] == "99", f"el ppid se corrio: {campos[:6]}"


def test_seat_count_distinto_de_uno_es_ambiguo(monkeypatch, dormido) -> None:
    """Con 0 el sujeto ya murió; con 2+ matar el equivocado y matar el correcto
    se ven idénticos desde afuera. Ninguno de los dos autoriza el efecto."""
    p = dormido()
    gemelo = {"pid": p.pid, "exe": "claude", "name": "X — Team SEAL",
              "starttime": g.starttime(p.pid), "ppid": g.ppid(p.pid)}
    monkeypatch.setattr(g, "live_seats", lambda exe="claude": [gemelo, dict(gemelo, pid=p.pid + 1)])
    monkeypatch.setattr(g, "exe_real", lambda pid: "claude")
    monkeypatch.setattr(g, "seat_name", lambda pid: "X — Team SEAL")
    codigo, informe = g.verify(p.pid, expect_exe="claude", expect_name="X — Team SEAL")
    assert codigo == g.EXIT_AMBIGUOUS
    assert informe["seat_count"] == 2

    monkeypatch.setattr(g, "live_seats", lambda exe="claude": [gemelo])
    codigo_unico, _ = g.verify(p.pid, expect_exe="claude", expect_name="X — Team SEAL")
    assert codigo_unico == g.EXIT_OK, "con exactamente 1 asiento el guard debe permitir"


def test_seat_name_lee_el_argumento_no_una_subcadena(dormido) -> None:
    """`--name == "JARVIS"` dio 0 asientos porque el real era `JARVIS — Team SEAL`,
    y un filtro `*claude*` matcheo el bash de la tool call. Se lee el argumento
    SIGUIENTE a --name, nunca una subcadena del cmdline."""
    p = dormido(["--name", "TEST — Team SEAL"])
    assert g.seat_name(p.pid) == "TEST — Team SEAL"


def test_no_mata_cuando_hay_mismatch(dormido) -> None:
    """La propiedad que de verdad importa: un veredicto != OK no envia señal."""
    p = dormido()
    codigo, informe = g.verify_and_kill(p.pid, signal.SIGTERM, expect_exe="claude")
    assert codigo == g.EXIT_MISMATCH
    assert informe["killed"] is False
    assert p.poll() is None, "el proceso fue matado pese al veredicto MISMATCH"


def test_mata_cuando_todo_coincide(dormido) -> None:
    """Y el gemelo: cuando todo coincide, el efecto SI ocurre. Un guard que nunca
    mata no es seguro, es inutil — y se ve igual de verde."""
    p = dormido()
    codigo, informe = g.verify_and_kill(
        p.pid, signal.SIGKILL,
        expect_exe=g.exe_real(p.pid),
        expect_starttime=g.starttime(p.pid),
        require_unique_seat=False,
    )
    assert codigo == g.EXIT_OK, informe["reason"]
    assert informe["killed"] is True
    p.wait(timeout=5)
    assert p.poll() is not None, "el veredicto fue OK pero el proceso sigue vivo"


def test_binario_versionado_se_reconoce_por_su_nombre_de_comando() -> None:
    """La celda que mis 10 primeros tests NO cubrian, y que el incidente real si.

    Un asiento Claude Code vive en `.../share/claude/versions/2.1.219`, asi que
    el basename del realpath es la VERSION. `--expect-exe claude` daba MISMATCH
    sobre el asiento sano. Los tests usaban `python3` —cuyo realpath termina en
    el nombre del comando— o sea que **el binario de prueba era mas amable que el
    binario real** y el verde no significaba nada.
    """
    ident = g.exe_identities(os.getpid())
    assert ident["path"] and ident["comm"], "las identidades no pueden venir vacias"

    class _Fake:
        pass

    # Reproducimos la forma exacta del caso real sin depender de que haya un
    # asiento claude vivo: ruta versionada + comm con el nombre del comando.
    identidades_falsas = {
        "path": "/home/dadito/.local/share/claude/versions/2.1.219",
        "basename": "2.1.219",
        "comm": "claude",
        "argv0": "claude",
    }
    original = g.exe_identities
    g.exe_identities = lambda pid: identidades_falsas
    try:
        assert g.exe_matches(1, "claude"), "un binario versionado debe reconocerse por comm/argv0"
        assert g.exe_matches(1, "2.1.219"), "el basename real tambien es una identidad valida"
        assert not g.exe_matches(1, "bash"), "y no puede reconocer a CUALQUIER cosa"
    finally:
        g.exe_identities = original


def test_censo_encuentra_asientos_con_binario_versionado(monkeypatch) -> None:
    """Si `live_seats` filtra por basename, con binario versionado devuelve CERO
    y el chequeo de unicidad degenera en `seat_count == 0` -> AMBIGUOUS siempre:
    el guard bloquearia TODO kill legitimo, que es un fallo silencioso."""
    monkeypatch.setattr(g, "exe_identities", lambda pid: {
        "path": "/home/x/.local/share/claude/versions/2.1.219",
        "basename": "2.1.219", "comm": "claude", "argv0": "claude"})
    monkeypatch.setattr(g, "seat_name", lambda pid: "X — Team SEAL")
    monkeypatch.setattr(g, "starttime", lambda pid: "111")
    monkeypatch.setattr(g, "ppid", lambda pid: 1)
    asientos = g.live_seats("claude")
    assert asientos, "el censo no encontro asientos con binario versionado"


def test_el_informe_no_publica_el_prompt_de_sistema_ajeno(dormido) -> None:
    """Un guard de seguridad que para operar publica lo que debe proteger no es
    un guard: es la fuga con otro nombre.

    Origen: 24-jul. FABLE volco el `cmdline` de un asiento y se llevo su prompt
    de sistema entero —contratos internos, regla de silencio, identidad—. ALICE
    reporto haber hecho lo mismo horas antes con JARVIS. Este informe tenia
    `cmdline[:200]`, y **los primeros 200 chars de un asiento real YA entran en
    el --append-system-prompt**: el truncado filtraba igual, solo que menos.

    Regla de William: el terminal y los mensajes de un agente son su intimidad.
    """
    secreto = "MANDATORY-FIRST-ACTION-contrato-interno-que-no-debe-salir"
    p = dormido(["--name", "TEST — Team SEAL", "--append-system-prompt", secreto])

    safe = g.cmdline_safe(p.pid)
    assert secreto not in safe, f"el prompt de sistema salio en cmdline_safe: {safe}"
    assert "TEST — Team SEAL" in safe, "el --name SI es publicable: sin el, el guard no sirve"
    assert "redactado" in safe, "la redaccion tiene que ser VISIBLE, no silenciosa"

    # Y el informe completo, que es lo que de verdad se publica.
    _, informe = g.verify(p.pid, expect_exe="claude")
    assert secreto not in json.dumps(informe, ensure_ascii=False), (
        "el prompt de sistema salio en el informe del guard")


def test_un_flag_desconocido_se_redacta_por_defecto(dormido) -> None:
    """Allowlist, no denylist: un flag nuevo que nadie agrego a la lista NO puede
    filtrarse por defecto. El fallo de una denylist es silencioso y solo se ve
    cuando ya se publico."""
    p = dormido(["--flag-inventado-manana", "valor-sensible-nuevo"])
    safe = g.cmdline_safe(p.pid)
    assert "valor-sensible-nuevo" not in safe, f"un flag desconocido filtro su valor: {safe}"


def test_la_señal_va_por_pidfd_ligado_a_la_instancia(dormido) -> None:
    """ADA, 24-jul: entre revalidar `starttime` y `kill(2)` queda una ventana
    residual — el proceso puede morir ahi y el kernel reciclar el numero.

    Un pidfd queda ligado a ESA instancia, no al entero: si el sujeto muere, el
    fd caduca y la señal falla en vez de ir a parar a un inquilino nuevo.

    El ORDEN es la propiedad que se prueba: el fd se abre ANTES de revalidar. Al
    reves la ventana sigue existiendo, solo que corrida un renglon.
    """
    p = dormido()
    codigo, informe = g.verify_and_kill(
        p.pid, signal.SIGKILL,
        expect_exe=g.exe_real(p.pid),
        expect_starttime=g.starttime(p.pid),
        require_unique_seat=False,
    )
    assert codigo == g.EXIT_OK, informe["reason"]
    assert informe["killed"] is True
    assert informe["delivery"] == "pidfd_send_signal", (
        f"esta plataforma soporta pidfd y la señal no fue por ahi: {informe['delivery']}")
    p.wait(timeout=5)
    assert p.poll() is not None


def test_nunca_se_emite_señal_por_numero_de_pid_sin_pidfd(dormido, monkeypatch) -> None:
    """Esta celda cambio de veredicto el mismo dia, y el cambio es la leccion.

    Version 1 (mia): sin pidfd, `os.kill` con la ventana residual DECLARADA en el
    informe. Razonamiento: una degradacion honesta es auditable.

    Version 2 (ADA, endurecida): abortar. **Declarar un riesgo no lo mitiga** —
    el informe decia la verdad y el proceso equivocado se moria igual. En una
    operacion irreversible el unico fallback aceptable es no ejecutar; correr el
    kill a mano, con el operador enterado, es estrictamente mejor que una
    herramienta que mata por numero de PID.

    El nombre viejo del test era `..._la_ventana_residual_se_DECLARA`: nombraba
    la conducta que hoy esta PROHIBIDA. Un test cuyo nombre describe el defecto
    lo defiende en la proxima lectura.
    """
    p = dormido()
    monkeypatch.delattr(g.os, "pidfd_open", raising=False)
    codigo, informe = g.verify_and_kill(
        p.pid, signal.SIGKILL,
        expect_exe=g.exe_real(p.pid),
        expect_starttime=g.starttime(p.pid),
        require_unique_seat=False,
    )
    assert codigo == g.EXIT_MISMATCH, informe.get("reason")
    assert informe["killed"] is False
    assert "sin pidfd" in informe["delivery"]
    assert p.poll() is None, "mato por numero de PID en una plataforma sin pidfd"


def test_pidfd_no_mata_si_el_starttime_cambio(dormido, monkeypatch) -> None:
    """El gemelo del anterior: tener el fd abierto NO autoriza la señal. Si la
    revalidacion falla, no se manda nada — el fd se cierra y se aborta."""
    p = dormido()
    real = g.starttime(p.pid)
    llamadas = {"n": 0}
    original = g.starttime

    def starttime_que_cambia(pid):
        llamadas["n"] += 1
        # la 1ra lectura (verify) da el real; la 2da (revalidacion) simula reciclado
        return original(pid) if llamadas["n"] <= 1 else str(int(real) + 999)

    monkeypatch.setattr(g, "starttime", starttime_que_cambia)
    codigo, informe = g.verify_and_kill(
        p.pid, signal.SIGKILL, expect_exe=g.exe_real(p.pid), require_unique_seat=False)
    assert codigo == g.EXIT_MISMATCH, informe.get("reason")
    assert informe["killed"] is False
    assert p.poll() is None, "se mato pese a que la revalidacion fallo"


def test_la_lectura_cruda_no_es_la_funcion_facil_de_llamar() -> None:
    """Minimizacion en origen (ADA, 24-jul): la funcion de nombre obvio tiene que
    ser la SEGURA.

    La fuga de hoy la cometieron cuatro agentes llamando a la herramienta obvia
    —`tr '\\0' ' '`, `ps -o args=`, mi `cmdline[:200]`—, ninguno buscando el
    secreto. Si `cmdline()` sigue siendo publica, el proximo que importe el
    modulo la llama y vuelve a filtrar sin enterarse.
    """
    assert not hasattr(g, "cmdline"), (
        "`cmdline` publica reintroduce la fuga por uso casual: debe ser `_cmdline_raw`")
    assert hasattr(g, "cmdline_safe"), "la version segura tiene que existir y ser la publica"
    assert hasattr(g, "_cmdline_raw"), "la cruda sigue siendo necesaria para extraer campos"


def test_env_field_devuelve_un_campo_y_nunca_el_bloque(dormido, monkeypatch) -> None:
    """`/proc/<pid>/environ` de un asiento SEAL trae ~71 variables, y entre ellas
    SEAL_SESSION_TOKEN — el secreto con el que ese agente prueba su identidad
    ante SOUL. El comando que circulaba el 24-jul volcaba las 71 para usar 2.
    """
    p = dormido()
    monkeypatch.setenv("IRRELEVANTE", "x")
    # Se prueba contra un environ sintetico para no depender de un asiento vivo.
    falso = "SEAL_AGENT=ADA\0SEAL_SESSION_TOKEN=secreto-que-no-debe-salir\0LANG=es\0"
    (tmp := Path(str(p.pid))),  # noqa: F841  (solo para claridad de lectura)

    class _FakePath:
        def __init__(self, raw): self._raw = raw
        def __truediv__(self, _): return self
        def read_bytes(self): return self._raw.encode()

    monkeypatch.setattr(g, "PROC", _FakePath(falso))

    assert g.env_field(p.pid, "SEAL_AGENT") == "ADA"
    assert g.env_field(p.pid, "NO_EXISTE") is None

    # env_names publica NOMBRES y ningun valor: es el oraculo-propiedad.
    nombres = g.env_names(p.pid)
    assert "SEAL_SESSION_TOKEN" in nombres, "tiene que poder AUDITARSE que el secreto viaja"
    assert not any("secreto-que-no-debe-salir" in n for n in nombres), (
        "env_names filtro un VALOR: su unico proposito es no hacerlo")


def test_si_pidfd_open_falla_en_plataforma_que_lo_soporta_ABORTA(dormido, monkeypatch) -> None:
    """ADA, 24-jul: el fallback era DESTRUCTIVO.

    Si la plataforma soporta pidfd, el unico motivo para que `pidfd_open` falle
    es que el proceso ya no exista — o sea exactamente cuando el numero pudo
    reasignarse. Caer a `os.kill(pid)` ahi manda la señal **al inquilino nuevo**.

    Un fallback que degrada a la operacion insegura convierte una condicion de
    error en el peor caso posible. No hay fallback: se aborta.
    """
    p = dormido()

    def pidfd_que_falla(_pid):
        raise ProcessLookupError("simulado: el proceso murio entre verificar y abrir")

    monkeypatch.setattr(g.os, "pidfd_open", pidfd_que_falla)
    mato = {"si": False}
    monkeypatch.setattr(g.os, "kill", lambda *a, **k: mato.update(si=True))

    codigo, informe = g.verify_and_kill(
        p.pid, signal.SIGKILL,
        expect_exe=g.exe_real(p.pid),
        expect_starttime=g.starttime(p.pid),
        require_unique_seat=False,
    )
    assert codigo == g.EXIT_MISMATCH, f"debia abortar, dio {codigo}: {informe.get('reason')}"
    assert informe["killed"] is False
    assert mato["si"] is False, "cayo a os.kill: eso es el fallback destructivo que ADA rechazo"
    assert "no se degrada" in informe["reason"]
    assert p.poll() is None, "el proceso fue matado pese al abort"


def test_flag_desconocido_con_valor_PEGADO_tambien_se_redacta(monkeypatch) -> None:
    """La fuga que ADA encontro leyendo el codigo, el 24-jul.

    Mis 21 tests probaban `--flag valor` (separado) y NINGUNO `--flag=valor`
    (pegado). La allowlist cubria una sintaxis y dejaba pasar la otra:

        _cmdline_raw = ["prog", "--api-key=SYNTHETIC_SECRET"]
        cmdline_safe(...) -> "prog --api-key=SYNTHETIC_SECRET"    <- fuga

    El test estaba escrito sobre la forma que yo ya tenia en la cabeza, que es
    justo la que no hacia falta probar.
    """
    monkeypatch.setattr(g, "_cmdline_raw", lambda pid: [
        "/usr/bin/prog",
        "--api-key=SYNTHETIC_SECRET",
        "--token=OTRO_SECRETO",
        "--name", "X — Team SEAL",
        "--model=claude-opus-5",
    ])
    safe = g.cmdline_safe(1)

    assert "SYNTHETIC_SECRET" not in safe, f"valor pegado filtrado: {safe}"
    assert "OTRO_SECRETO" not in safe, f"valor pegado filtrado: {safe}"
    # La CLAVE sigue siendo visible: saber QUE flags hay es util y no es secreto.
    assert "--api-key=[redactado]" in safe, f"la clave tiene que verse redactada: {safe}"
    # Y la allowlist con "=" sigue funcionando: no se rompe el caso legitimo.
    assert "--model=claude-opus-5" in safe, f"un flag publicable con = debe pasar: {safe}"
    assert "X — Team SEAL" in safe, f"--name separado debe seguir pasando: {safe}"
