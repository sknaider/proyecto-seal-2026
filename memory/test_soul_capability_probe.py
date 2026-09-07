"""Pruebas de la capa de absorción.

Lo que hay que probar acá NO es que la tabla se imprima linda: es que el estado
**cambie** con el cerebro. Un runner que devolviera ACTIVE siempre pasaría un
test ingenuo y sería exactamente igual de inútil que no tenerlo — es el patrón
que venimos cazando todo el día (una herramienta que reporta lo mismo haya hecho
algo o nada).

Por eso cada test de estado es DIFERENCIAL: se afirma el estado y también que el
estado contrario aparece al variar la única entrada que debe decidirlo.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import soul_capability_probe as cap  # noqa: E402


def _capability(**measurements) -> dict:
    return {"id": "demo", "soul_impl": "memory/demo.py", "native_in": dict(measurements)}


# --- El corazón: el estado depende del cerebro -------------------------------


def test_native_pauses_and_absent_activates_same_capability() -> None:
    """La MISMA capacidad, dos cerebros, dos estados. Si esto no varía, el
    registro es decorativo."""
    capability = _capability(
        **{
            "modelo-que-la-trae": {"verdict": "NATIVE", "measured": "2026-07-24"},
            "modelo-que-no": {"verdict": "ABSENT", "measured": "2026-07-24"},
        }
    )
    assert cap.resolve_state(capability, "modelo-que-la-trae")[0] == cap.PAUSED
    assert cap.resolve_state(capability, "modelo-que-no")[0] == cap.ACTIVE


def test_unmeasured_model_activates_never_pauses() -> None:
    """El caso que decide si esto sirve: cambiamos a un cerebro nuevo y nadie lo
    midió todavía. Encender de más cuesta plata; apagar de más nos deja sin la
    capa justo el día del cambio, que es cuando nadie está mirando."""
    capability = _capability(**{"claude-opus-5": {"verdict": "NATIVE", "measured": "2026-07-24"}})
    state, reason = cap.resolve_state(capability, "kimi-k2-instruct")
    assert state == cap.ACTIVE
    assert "sin medicion" in reason
    # Y la contraprueba: contra el cerebro que SÍ se midió, pausa.
    assert cap.resolve_state(capability, "claude-opus-5")[0] == cap.PAUSED


def test_partial_does_not_pause() -> None:
    """'El modelo colabora' no es 'el modelo lo garantiza'. Un guard externo que
    el modelo no puede saltear no se apaga porque el modelo sea cooperativo."""
    # El fixture decía `"m"`. No es un nombre pobre: es un **no-identidad**, y
    # desde que un cerebro sin nombrar cae en UNVERIFIED esta celda medía otra
    # cosa que la que dice medir. Un modelo de mentira tiene que tener FORMA de
    # modelo o el test no ejerce la rama que cree ejercer.
    capability = _capability(**{"modelo-que-colabora": {"verdict": "PARTIAL", "measured": "2026-07-24"}})
    assert cap.resolve_state(capability, "modelo-que-colabora")[0] == cap.ACTIVE


# --- Identidad del cerebro ---------------------------------------------------


def test_alias_is_treated_as_unknown_but_full_id_is_accepted(monkeypatch) -> None:
    """`opus` apuntó a 4.8 y después a 5 sin cambiar una letra del launcher.
    Un alias nombra un puntero, no un cerebro: no puede autorizar una pausa.

    Diferencial a propósito: si `current_model` devolviera 'unknown' siempre,
    la primera mitad pasaría sola y no probaría nada.
    """
    for var in ("ANTHROPIC_MODEL", "CLAUDE_MODEL"):
        monkeypatch.delenv(var, raising=False)

    monkeypatch.setenv("SEAL_MODEL", "opus")
    assert cap.current_model() == "unknown"

    monkeypatch.setenv("SEAL_MODEL", "claude-opus-5")
    assert cap.current_model() == "claude-opus-5"


def test_unknown_model_enciende_la_capa_pero_NO_la_declara_medida(monkeypatch) -> None:
    """Esta celda **codificaba el defecto** que encontró ADA, con un docstring que
    lo llamaba "fail-closed": afirmaba que un alias tiene que terminar en `ACTIVE`,
    o sea en el mismo estado que un cerebro medido. Un test puede clavar un bug
    tan bien como clava un contrato, y este lo defendió hasta que un humano lo miró.

    Lo que sí se conserva —y es la mitad correcta del original— es que la capa
    **queda encendida**. Lo que cambia es que ya no se afirma que esté medida.
    """
    monkeypatch.setenv("SEAL_MODEL", "opus")
    capability = _capability(**{"claude-opus-5": {"verdict": "NATIVE", "measured": "2026-07-24"}})
    estado, motivo = cap.resolve_state(capability, cap.current_model())
    assert estado == cap.UNVERIFIED
    assert estado != cap.PAUSED, "un cerebro sin nombrar jamas puede apagar una capa"
    assert "no identificado" in motivo


def test_census_engine_is_invoked_not_reimplemented(monkeypatch) -> None:
    """El motor de censo de FABLE decide la identidad del cerebro; acá sólo se lo
    invoca. Si algún día alguien lo reimplementa en este archivo, este test sigue
    pasando pero el de abajo —el que exige que un runtime ajeno dé 'unknown'—
    cazaría la divergencia."""
    for var in ("SEAL_MODEL", "ANTHROPIC_MODEL", "CLAUDE_MODEL"):
        monkeypatch.delenv(var, raising=False)
    resolved = cap.model_from_census("ALICE")
    # Puede ser None si el asiento no está vivo; lo que NO puede es inventar.
    assert resolved is None or resolved == "unknown" or cap._FULL_MODEL_ID.match(resolved)


def test_foreign_runtime_never_names_a_brain(monkeypatch) -> None:
    """ADA corre en codex: la pregunta '¿qué modelo?' no aplica. Un OTRO_RUNTIME
    que devolviera un nombre autorizaría una pausa sobre un cerebro que nadie
    midió."""
    for var in ("SEAL_MODEL", "ANTHROPIC_MODEL", "CLAUDE_MODEL"):
        monkeypatch.delenv(var, raising=False)
    resolved = cap.model_from_census("ADA")
    # La invariante es una PROPIEDAD —"no nombra un cerebro"—, no una lista de
    # strings permitidos. Escrita como lista, esta celda daba rojo cuando la sonda
    # empezó a devolver `otro_runtime:codex`, que respeta la invariante y ADEMAS
    # dice cual es el runtime. Un test que enumera valores en vez de afirmar la
    # propiedad bloquea la mejora y parece que caza una regresion.
    assert resolved is None or not cap._FULL_MODEL_ID.match(resolved), (
        f"un runtime ajeno devolvio identidad de cerebro: {resolved}")


# --- Los dos controles que pidió ADA -----------------------------------------


def test_control_POSITIVO_el_runtime_ajeno_se_nombra_no_se_borra(monkeypatch) -> None:
    """ADA corre `codex --profile ada` y el censo lo SABE (lo imprime como
    OTRO_RUNTIME). La sonda tiraba ese dato y decía 'unknown' — que es el falso
    verde: manda a averiguar qué corre cuando ya se sabe qué corre.

    Se salta si el asiento de ADA no está vivo: sin sujeto no hay medición, y un
    verde por ausencia sería justo el modo de fallo que este archivo persigue.
    """
    for var in ("SEAL_MODEL", "ANTHROPIC_MODEL", "CLAUDE_MODEL"):
        monkeypatch.delenv(var, raising=False)
    resolved = cap.model_from_census("ADA")
    if resolved is None:
        pytest.skip("asiento ADA no vivo: nada que medir")
    assert resolved.startswith("otro_runtime:"), resolved
    assert "codex" in resolved, f"el runtime real no sobrevivio a la clasificacion: {resolved}"


def test_control_NEGATIVO_cerebro_desconocido_no_puede_salir_verde() -> None:
    """El otro lado: un cerebro que de verdad no se sabe cuál es NO puede
    producir rc=0. Es el fail-closed puesto donde corresponde —la afirmación—,
    mientras la capa sigue corriendo.
    """
    capability = _capability(**{"claude-opus-5": {"verdict": "NATIVE", "measured": "2026-07-24"}})
    for no_identidad in ("unknown", "opus", "otro_runtime:codex", ""):
        estado, _ = cap.resolve_state(capability, no_identidad)
        assert estado == cap.UNVERIFIED, f"'{no_identidad}' produjo {estado}"
    # Y contra un id completo de verdad, la sonda vuelve a afirmar con normalidad.
    assert cap.resolve_state(capability, "claude-opus-5")[0] == cap.PAUSED


# --- La otra pregunta: ¿nuestra capa funciona? -------------------------------


def test_missing_impl_and_missing_test_are_both_unhealthy() -> None:
    """Un archivo que no existe y un archivo sin test son la misma cosa a efectos
    de evidencia: no hay forma de afirmar que la capa funciona."""
    ok, detail = cap.run_layer_probe({"id": "x", "soul_impl": "memory/no_existe_jamas.py"})
    assert ok is False and "no existe" in detail

    ok, detail = cap.run_layer_probe({"id": "x", "soul_impl": "memory/soul_capability_registry.json"})
    assert ok is False and "sin test" in detail


def test_declared_impls_actually_exist_and_pass() -> None:
    """Contra el registro REAL: cada capacidad declarada tiene implementación en
    disco, test propio, y ese test pasa. Es lo que evita que el registro se
    convierta en una lista de promesas."""
    registry = cap.load_registry()
    assert registry["capabilities"], "el registro no puede estar vacio"
    for capability in registry["capabilities"]:
        ok, detail = cap.run_layer_probe(capability)
        assert ok, f"{capability['id']}: capa declarada pero no operativa — {detail}"


def test_every_measurement_records_model_and_date() -> None:
    """Un 'nativa' sin decir contra qué cerebro y cuándo se midió miente el día
    que cambiemos de modelo — que es justo el día que se va a leer."""
    for capability in cap.load_registry()["capabilities"]:
        for model, entry in (capability.get("native_in") or {}).items():
            assert model and model != "unknown", f"{capability['id']}: medicion sin modelo"
            assert entry.get("measured"), f"{capability['id']}/{model}: medicion sin fecha"
            assert entry.get("evidence"), f"{capability['id']}/{model}: veredicto sin evidencia"
            assert entry["verdict"] in {cap.NATIVE, cap.ABSENT, cap.PARTIAL}


@pytest.mark.parametrize("state", [cap.ACTIVE, cap.PAUSED, cap.UNVERIFIED])
def test_states_are_distinct_constants(state: str) -> None:
    """Los tres estados tienen que ser distinguibles entre sí. Si dos colapsaran
    al mismo string, la salida y el exit code volverían a mezclar 'medido' con
    'no medido', que es el defecto entero de este cambio.
    """
    todos = (cap.ACTIVE, cap.PAUSED, cap.UNVERIFIED)
    assert len(set(todos)) == len(todos), f"estados que colapsan: {todos}"
    assert state in todos and state.strip()


def test_wiring_surface_is_runtime_specific_and_flags_unconfirmed(tmp_path) -> None:
    """Tres celdas, no una: el cableado tiene que depender del RUNTIME y una
    superficie inferida tiene que confesarlo en el propio texto.

    Origen: el 24-jul publiqué que Codex "no tiene hooks" leyendo un `config.toml`
    sin esa seccion. Es falso —el binario trae PreToolUse/PostToolUse y config por
    `hooks.json`—, asi que la sonda ahora conoce esa superficie. Pero conocerla NO
    es haber visto a Codex leerla: ese matiz vive en el string, y un string sin
    test se borra en el primer refactor que lo encuentre incomodo.

    ACTUALIZADO 24-jul, misma tarde: este test afirmaba tambien que "hoy ninguna
    capa esta cableada en codex". Era estado del mundo, no contrato, y el mundo
    cambio 40 minutos despues —ADA instalo y EJECUTO `REPO_ROOT/.codex/hooks.json`—
    dejando al test defendiendo un rojo que ya era falso. **Un test que fija el
    estado de hoy caduca; uno que fija el MECANISMO no.** Por eso la reserva se
    prueba ahora con un runtime ficticio: sigue cubierta aunque codex salga (o
    vuelva a entrar) de `_WIRING_UNCONFIRMED`.
    """
    hooks = tmp_path / "hooks.json"
    hooks.write_text('{"command": "nerves_injection_probe.py"}', encoding="utf-8")
    original = dict(cap._WIRING_SURFACE)
    original_unconf = set(cap._WIRING_UNCONFIRMED)
    cap._WIRING_SURFACE["inferido"] = [hooks]
    cap._WIRING_UNCONFIRMED.add("inferido")
    try:
        ok_inf, detail_inf = cap._wired_in_settings(
            "memory/nerves_injection_probe.py", "otro_runtime:inferido")
        ok_claude, detail_claude = cap._wired_in_settings(
            "memory/nerves_injection_probe.py", "claude-opus-5")
    finally:
        cap._WIRING_SURFACE.clear()
        cap._WIRING_SURFACE.update(original)
        cap._WIRING_UNCONFIRMED.clear()
        cap._WIRING_UNCONFIRMED.update(original_unconf)

    assert ok_inf, f"superficie existente y con la capa nombrada: {detail_inf}"
    assert "sin corrida" in detail_inf, (
        f"un verde inferido debe declarar su reserva, no venderse como medido: {detail_inf}")
    assert ok_claude and "sin corrida" not in detail_claude, (
        f"claude esta confirmado por corrida: la reserva no debe contagiarse — {detail_claude}")

    # La superficie de codex tiene que incluir la ruta PROJECT-LOCAL. Omitirla
    # fue lo que hizo publicar `UNWIRED` sobre el asiento ya cableado de ADA.
    rutas_codex = [str(p) for p in cap._WIRING_SURFACE["codex"]]
    assert any(r.endswith("/.codex/hooks.json") and not r.startswith(str(Path.home()) + "/.codex")
               for r in rutas_codex), (
        f"falta REPO_ROOT/.codex/hooks.json: la sonda acusaria un asiento sano — {rutas_codex}")

    # Y el rojo, cuando toque, tiene que NOMBRAR donde falta o no es accionable.
    faltan = cap._wired_in_settings("memory/no_existe_esta_capa.py", "otro_runtime:codex")[1]
    assert "hooks.json" in faltan, f"un rojo sin ruta no es accionable: {faltan}"


def test_fleet_fails_on_unwired_even_after_the_seat_is_measured(monkeypatch) -> None:
    """El gate no puede apagarse el dia que alguien MIDE: medir no es instalar.

    Origen: el 24-jul `_fleet_report` sólo miraba `UNVERIFIED`. ADA daba rc=2 por
    estar sin medir contra Codex; el dia que se midiera, el estado dejaba de ser
    UNVERIFIED, `wired=False` seguia intacto y la funcion salia **0**, firmando en
    verde un asiento con las dos capas apagadas.

    Tres celdas, porque un gate que sólo prueba el ataque bloquea el caso legitimo:
      medida + no cableada  -> DEBE fallar   (el defecto)
      medida + cableada     -> DEBE pasar    (no romper el verde real)
      PAUSED + no cableada  -> DEBE pasar    (apagada a proposito, es coherente)
    """
    registro = cap.load_registry()
    ids = [c["id"] for c in registro["capabilities"]]

    def fleet_de_una(_agent="X"):
        return [{"agent": "X", "model": "otro_runtime:codex"}]

    def scan_con(state, wired):
        def _scan(_reg, _model, only=None):
            return [{"id": i, "state": state, "wired": wired, "soul_layer_ok": True,
                     "reason": "", "soul_layer_detail": "", "wired_detail": ""} for i in ids]
        return _scan

    monkeypatch.setattr(cap, "fleet", fleet_de_una)

    monkeypatch.setattr(cap, "scan", scan_con(cap.ACTIVE, False))
    assert cap._fleet_report(json_out=False) != 0, \
        "asiento MEDIDO con capas encendidas y sin cablear certifico en VERDE"

    monkeypatch.setattr(cap, "scan", scan_con(cap.ACTIVE, True))
    assert cap._fleet_report(json_out=False) == 0, \
        "un asiento sano y cableado tiene que seguir dando verde"

    monkeypatch.setattr(cap, "scan", scan_con(cap.PAUSED, False))
    assert cap._fleet_report(json_out=False) == 0, \
        "una capa PAUSED no cableada es coherente, no puede bloquear la flota"


def test_atestacion_externa_cuenta_pero_declara_su_procedencia() -> None:
    """La herramienta tiene que tener CASILLA para la evidencia que ya existe.

    Origen: 24-jul. ADA midió sus hooks en Codex por ejecución real (canarios
    allow/deny, 75 tests) y el probe siguió gritando `SIN MEDIR`, rc=2 — porque
    `otro_runtime:codex` no matchea el patron de model-id y la rama devolvia
    UNVERIFIED **antes de mirar el registro**. FABLE y JARVIS lo nombraron el
    mismo minuto: la evidencia existia y el esquema no tenia donde ponerla.

    Cuatro celdas, porque el riesgo del arreglo es el opuesto al del defecto:
      atestacion completa   -> cuenta como medida, Y dice que es externa
      sin attested_by       -> NO cuenta (seria "alguien dijo que midio")
      evidence de una linea -> NO cuenta (no es auditable por un tercero)
      sin entrada           -> UNVERIFIED, como antes
    """
    completa = {
        "verdict": "ABSENT",
        "measured": "2026-07-24",
        "attested_by": "ADA",
        "evidence": ("Ejecucion real en el TUI Codex pid 991181: hook/started + "
                     "hook/completed, canarios allow/deny y PostToolUse reales, 75 tests."),
    }
    cap_base = {"id": "x", "state": "ACTIVE", "soul_impl": "memory/x.py"}

    estado, razon = cap.resolve_state(dict(cap_base, native_in={"otro_runtime:codex": completa}),
                                 "otro_runtime:codex")
    assert estado == cap.ACTIVE, f"una atestacion valida no puede quedar SIN MEDIR: {razon}"
    assert "atestado por ADA" in razon, f"la procedencia tiene que viajar en el texto: {razon}"
    assert "no corrida propia" in razon, (
        f"no puede lavarse como medicion del probe: {razon}")

    sin_quien = {k: v for k, v in completa.items() if k != "attested_by"}
    estado_sq, _ = cap.resolve_state(dict(cap_base, native_in={"otro_runtime:codex": sin_quien}),
                                "otro_runtime:codex")
    assert estado_sq == cap.UNVERIFIED, "sin attested_by es una afirmacion suelta, no una medicion"

    floja = dict(completa, evidence="lo probe")
    estado_fl, _ = cap.resolve_state(dict(cap_base, native_in={"otro_runtime:codex": floja}),
                                "otro_runtime:codex")
    assert estado_fl == cap.UNVERIFIED, "una evidencia que nadie puede auditar no es evidencia"

    estado_vacio, _ = cap.resolve_state(dict(cap_base, native_in={}), "otro_runtime:codex")
    assert estado_vacio == cap.UNVERIFIED, "sin entrada el comportamiento previo se conserva"


def test_el_piso_de_evidencia_mide_resolubilidad_no_largo() -> None:
    """Las cuatro celdas que FABLE midio contra la version anterior de esta
    funcion, el 24-jul. El piso era `len(evidence) >= 40` y estaba
    ANTI-CORRELACIONADO con la propiedad buscada en los dos extremos:

        "lo midio ADA y estaba todo bien"   52 chars -> pasaba   (es el caso a bloquear)
        "aaaa..." puro relleno              46 chars -> pasaba
        "msg api_ada_1784... 19:26"         37 chars -> RECHAZABA (referencia impecable)

    Lo que hace auditable a una evidencia no es su largo: es que apunte a algo
    que otro pueda ir a mirar. Un umbral de largo es un proxy honesto de "¿hay
    contenido?" y no dice NADA de "¿es verificable?".
    """
    def _atest(evidence: str) -> dict:
        return {"verdict": "ABSENT", "measured": "2026-07-24",
                "attested_by": "ADA", "evidence": evidence}

    # Corta pero RESOLUBLE: el caso que el umbral de largo rechazaba.
    corta_resoluble = "msg api_ada_1784938872352047322 19:26"
    assert len(corta_resoluble) < 40, "la celda pierde sentido si no es corta"
    assert cap._atestacion_valida(_atest(corta_resoluble)), (
        "una referencia resoluble no puede rechazarse por corta")

    # Largas pero NO resolubles: los casos que el umbral dejaba pasar.
    for prosa in ("lo midio ADA y estaba todo bien, quedo perfecto",
                  "a" * 46,
                  "verificado exhaustivamente por el equipo entero durante la tarde"):
        assert len(prosa) >= 40, "la celda pierde sentido si no es larga"
        assert not cap._atestacion_valida(_atest(prosa)), (
            f"prosa sin referencia resoluble no puede contar como medicion: {prosa[:40]!r}")

    # Cada forma resoluble, por separado: si una deja de reconocerse, se ve.
    for forma in ("api_ada_1784938872352047322", "pid 991181", "sha 6480c203a9f24388",
                  "75 tests", "REPO_ROOT/.codex/hooks.json"):
        assert cap._atestacion_valida(_atest(f"medicion: {forma}")), f"no reconocio {forma!r}"

    # Y la atestacion REAL que quedo registrada tiene que seguir siendo valida.
    registro = cap.load_registry()
    for c in registro["capabilities"]:
        entrada = (c.get("native_in") or {}).get("otro_runtime:codex")
        if entrada:
            assert cap._atestacion_valida(entrada), (
                f"la atestacion registrada de {c['id']} dejo de pasar su propio piso")
