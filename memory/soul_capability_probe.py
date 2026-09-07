#!/usr/bin/env python3
"""Capa de absorción de SOUL: qué capacidad se enciende con el cerebro puesto.

Visión de William, 24-jul-2026:

    "absorber todo lo mejor de los modelos principales... si cambiamos modelo,
     ejemplo kimi, geminis, tenemos toda la arquitectura absorbida en soul, y
     cuando volvamos a claude opus solo se quede pausa ya que el modelo lo tiene
     como nativo, pero cuando se cambia de modelo, soul detectará o escaneará qué
     no hace el modelo nuevo, el cerebro nuevo, y usar la capa de soul"

DOS PREGUNTAS QUE NO SE MEZCLAN
------------------------------
1. ¿**nuestra** implementación funciona ahora?  -> se mide con un comando, acá.
2. ¿el **cerebro** la trae nativa?              -> se mide evaluando al MODELO.

Colapsarlas en un solo estado es exactamente el error que pagamos hoy: un mismo
resultado cubriendo dos causas con remediación opuesta.  Si la capa de SOUL está
rota, se arregla código.  Si el modelo no la trae, se ENCIENDE la capa.  No es lo
mismo y no puede salir por el mismo canal.

LA PAUSA ES EL RIESGO, NO EL AHORRO
-----------------------------------
JARVIS lo señaló y tiene razón: una capacidad "en pausa" sin disparador de
reactivación es un borrado silencioso con otro nombre.  Por eso el estado NO se
guarda suelto: se guarda **por modelo**.  Si el cerebro que corre hoy no tiene
medición propia en el registro, la capacidad sale **ACTIVE**, no PAUSED.

Es fail-closed a propósito, y en la dirección correcta: el costo de encender de
más es gasto; el de apagar de más es quedarse sin la capa el día que cambiamos de
modelo — que es justo el día en que nadie va a estar mirando este archivo.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REGISTRY_PATH = Path(__file__).resolve().parent / "soul_capability_registry.json"
REPO_ROOT = Path(__file__).resolve().parent.parent
# El mismo intérprete con el que los hooks corren estas capas.
PYTEST_PYTHON = os.environ.get("SEAL_PYTHON", "/home/dadito/IA/seal-spark/.venv/bin/python3")

# Estados posibles de una capa de SOUL frente al cerebro en uso.
ACTIVE = "ACTIVE"  # SOUL la ejecuta: el cerebro está nombrado y no la trae
PAUSED = "PAUSED"  # el modelo la trae nativa y medida: no hace falta gastarla
# Capa ENCENDIDA sobre un cerebro que no se pudo nombrar. No es un tercer nivel
# de protección: la capa corre igual. Es un tercer nivel de AFIRMACIÓN.
#
# ADA pidió "fallar cerrado si el cerebro es desconocido" y tiene razón en el
# hallazgo, pero "cerrado" acá tenía dos lecturas opuestas y sólo una es segura.
# Estas capas son GUARDS (`input_injection_probe`, `output_action_guard`):
# apagarlas ante la duda deja el runtime no medido sin defensa, que es el peor
# lado del error. Lo que tiene que fallar cerrado no es el guard — es la CLAIM.
# Antes esto imprimía `[ACTIVE]` con rc=0, byte a byte igual que una capa medida
# contra un cerebro conocido. Ahora la capa sigue encendida y el proceso sale
# distinto de 0, así que la duda es visible para quien lea la salida o el exit.
UNVERIFIED = "ACTIVE-SIN-MEDIR"

# Veredictos de la medición contra un modelo concreto.
NATIVE = "NATIVE"
ABSENT = "ABSENT"
PARTIAL = "PARTIAL"


# Un id completo identifica un cerebro; un alias NO.  `opus` apuntó a 4.8 y luego
# a 5 sin que cambiara una sola letra en el launcher — NEXUS corrió horas creyendo
# una cosa y ejecutando otra.  Sólo se acepta como identidad lo que no puede
# reapuntar debajo nuestro.
_FULL_MODEL_ID = re.compile(r"^[a-z]+-[a-z0-9]+-[a-z0-9.\-]+$")

# Campos que una atestación externa DEBE traer para contar como medición.
# Sin esto, "medido por otra vía" degenera en "alguien dijo que lo midió" — que
# es exactamente el verde firmado que este archivo existe para evitar.
_CAMPOS_ATESTACION = ("verdict", "measured", "attested_by", "evidence")


# Formas de referencia que un tercero puede IR A MIRAR. La lista es corta a
# propósito: cada entrada es algo que existe fuera del texto de la atestación.
_REFERENCIA_RESOLUBLE = re.compile(
    r"""(
        api_[a-z]+_\d{6,}          # id de mensaje del chat
      | \bpid\s*\d{3,}             # proceso concreto
      # sha / hash / commit. El lookahead que exige un digito NO es adorno:
      # sin el, `"aaaaaaaa..."` de puro relleno matchea —`a` es un digito
      # hexadecimal valido— y el piso vuelve a aceptar exactamente el caso que
      # existe para bloquear. Cazado por el propio test de FABLE, 24-jul.
      | \b(?=[0-9a-f]*\d)[0-9a-f]{8,}\b
      | \b\d+\s+tests?\b           # corrida con conteo
      | (?:[\w.\-]+/){1,}[\w.\-]+\.[a-z]{2,4}   # ruta de archivo
    )""",
    re.VERBOSE | re.IGNORECASE,
)


def _atestacion_valida(entry: Any) -> bool:
    """¿La entrada es una medición externa con procedencia, o una afirmación suelta?

    Exige los cuatro campos y que `evidence` contenga **al menos una referencia
    resoluble**: un id de mensaje, un PID, un sha, un conteo de tests o una ruta.

    CORREGIDO 24-jul por FABLE, que lo midió contra esta misma función. Antes el
    piso era `len(evidence) >= 40`, y estaba **anti-correlacionado con lo que
    quería en los dos extremos**:

        "lo midio ADA y estaba todo bien"   52 chars -> pasaba
        "aaaaaaaa..." puro relleno          46 chars -> pasaba
        "msg api_ada_1784... 19:26"         37 chars -> RECHAZABA

    El primero es literalmente el *"alguien dijo que lo midió"* que este piso
    existe para bloquear; el tercero es una referencia impecable descartada por
    corta. **Lo que hace auditable a una evidencia no es su largo: es que apunte
    a algo que otro pueda ir a mirar.** El largo es un proxy honesto de "¿hay
    contenido?" y no dice nada de "¿es verificable?", que es la pregunta de acá.
    """
    if not isinstance(entry, dict):
        return False
    if any(not str(entry.get(campo, "")).strip() for campo in _CAMPOS_ATESTACION):
        return False
    return bool(_REFERENCIA_RESOLUBLE.search(str(entry.get("evidence", ""))))


def model_from_census(agent: str) -> str | None:
    """Pregunta a la sonda de FABLE con qué cerebro corre un asiento vivo.

    **Se invoca el motor, no se copia su regla.** Su `censar()` recorre `/proc`
    por runtime, deduplica por ancestría y rechaza alias; replicar eso acá
    crearía una segunda implementación que se desincroniza en silencio — que es
    justo el defecto que los tres encontramos en el skill: describir en vez de
    llamar. Si su módulo no está, se devuelve None y el llamador cae al camino
    del entorno; nunca se inventa un modelo.
    """
    censo = _cargar_censo()
    if censo is None:
        return None
    try:
        for seat in censo.censar():
            if seat.get("agente", "").upper() != agent.upper():
                continue
            return _identidad(seat, censo)
    except Exception:
        return None
    return None


def _cargar_censo():
    """El motor de FABLE, o None si no está. Nunca se inventa un sustituto."""
    sys.path.insert(0, str(REPO_ROOT / "fable" / "tools"))
    try:
        import model_adoption_check as census  # type: ignore
    except Exception:
        return None
    return census


def _identidad(seat: dict, censo) -> str:
    """Clasifica UN asiento. Vive suelta a propósito: `--fleet` la reusa.

    Si el modo flota reimplementara esta regla, la sonda tendría dos criterios de
    identidad que se desincronizan en silencio — exactamente el defecto contra el
    que advierte el docstring de arriba, cometido un nivel más abajo.
    """
    verdict, detail = censo.clasificar(seat)
    # Sólo CONOCIDO nombra un cerebro; DESCONOCIDO y OTRO_RUNTIME no son
    # identidades, y tratarlos como tales autorizaría una pausa.
    if verdict == "CONOCIDO":
        return detail
    # Pero tampoco se colapsan entre sí. Esta línea devolvía `"unknown"`
    # para los dos y ADA lo cazó como falso verde: el censo **sí sabe**
    # que ADA corre en `codex` —lo imprime como OTRO_RUNTIME— y la sonda
    # tiraba ese dato para después decir "cerebro en uso: unknown".
    # Son dos causas con remediación OPUESTA: `otro_runtime:codex` se
    # arregla midiendo ese cerebro, `unknown` se arregla averiguando qué
    # corre. Un solo código para las dos manda a arreglar lo que no es.
    if verdict == "OTRO_RUNTIME":
        runtime = str(seat.get("runtime") or "").strip() or "sin-nombre"
        return f"otro_runtime:{runtime}"
    return "unknown"


def fleet() -> list[dict[str, Any]]:
    """Un asiento vivo por fila: quién es y con qué cerebro corre AHORA.

    Es el dato que le falta al panel de William. La pregunta del panel no es
    "¿puedo cambiar el cerebro?" —el cerebro ya es un parámetro del launcher—
    sino **qué queda sin medir cuando lo cambia**. Eso sólo se puede responder
    cruzando los asientos vivos contra el registro de capacidades.

    Enumera el SUSTRATO (los procesos que el censo encuentra en `/proc`), nunca
    una lista de agentes escrita a mano: una lista propia no puede desmentirse a
    sí misma, y así fue como publiqué "1 de 10 vigilantes" con el denominador
    equivocado.
    """
    censo = _cargar_censo()
    if censo is None:
        return []
    filas = []
    for seat in censo.censar():
        agente = str(seat.get("agente") or "?").upper()
        filas.append({"agent": agente, "model": _identidad(seat, censo),
                      "runtime": seat.get("runtime"), "pid": seat.get("pid")})
    return sorted(filas, key=lambda f: f["agent"])


def current_model(agent: str | None = None) -> str:
    """El cerebro que corre AHORA, o 'unknown' si no se puede afirmar.

    Se prefiere el entorno del PROCESO VIVO sobre cualquier archivo de config: un
    launcher dice lo que debería correr, no lo que corre.  Y un **alias**
    (`opus`, `sonnet`) se trata como desconocido a propósito: nombra un puntero,
    no un modelo.  Devolver 'unknown' no es una falla — encamina a fail-closed,
    que es la dirección correcta acá.
    """
    for var in ("SEAL_MODEL", "ANTHROPIC_MODEL", "CLAUDE_MODEL"):
        value = (os.environ.get(var) or "").strip()
        if value:
            return value if _FULL_MODEL_ID.match(value) else "unknown"

    from_census = model_from_census(agent or os.environ.get("SEAL_AGENT") or "")
    if from_census:
        return from_census

    try:
        cmdline = Path(f"/proc/{os.getppid()}/cmdline").read_bytes().decode("utf-8", "replace")
    except OSError:
        return "unknown"
    parts = [p for p in cmdline.split("\0") if p]
    for index, part in enumerate(parts):
        if part == "--model" and index + 1 < len(parts):
            candidate = parts[index + 1].strip()
            return candidate if _FULL_MODEL_ID.match(candidate) else "unknown"
        if part.startswith("--model="):
            candidate = part.split("=", 1)[1].strip()
            return candidate if _FULL_MODEL_ID.match(candidate) else "unknown"
    return "unknown"


def load_registry(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or REGISTRY_PATH).read_text(encoding="utf-8"))


def resolve_state(capability: dict[str, Any], model: str) -> tuple[str, str]:
    """Devuelve (estado, motivo) de una capacidad para el cerebro dado.

    Sólo un veredicto NATIVE **medido contra este modelo exacto** pausa la capa.
    Todo lo demás —sin medir, medido contra otro cerebro, PARTIAL, ABSENT— la
    deja encendida.

    Y hay dos "no medido" que NO son el mismo estado, aunque hasta hoy salían
    idénticos por pantalla:

      * cerebro NOMBRADO sin medición de esta capacidad -> `ACTIVE`. Es un
        default defendible: sé contra qué corre y asumo que no la trae.
      * cerebro que no se pudo NOMBRAR -> `UNVERIFIED`. No hay default posible
        porque no hay sujeto; afirmar `ACTIVE` ahí es afirmar sobre algo que
        nadie midió.
    """
    measurements = capability.get("native_in") or {}
    if not _FULL_MODEL_ID.match(model):
        # Un identificador que no parece model-id (`otro_runtime:codex`) NO es
        # automáticamente "sin medir": puede haber sido medido POR OTRA VÍA y
        # atestado. Hasta el 24-jul esta rama devolvía UNVERIFIED antes de mirar
        # el registro, así que la evidencia de ADA —ejecución real en el TUI,
        # canarios allow/deny, 75 tests— no tenía dónde entrar y el rc=2 seguía
        # gritando "SIN MEDIR" sobre algo ya medido. Lo señalaron FABLE y JARVIS
        # el mismo minuto: **la herramienta no tenía casilla para la evidencia
        # que ya existía.**
        atestacion = measurements.get(model)
        if not _atestacion_valida(atestacion):
            return UNVERIFIED, (
                f"cerebro no identificado ('{model}') y sin atestación registrada: "
                "la capa corre igual, pero su estado NO está medido contra este runtime"
            )
        quien = atestacion.get("attested_by")
        cuando = atestacion.get("measured", "sin fecha")
        verdict = str(atestacion.get("verdict", "")).upper()
        # La reserva viaja en el texto: esto NO es una corrida del probe.
        reserva = f" [atestado por {quien}, {cuando} — evidencia externa, no corrida propia]"
        if verdict == NATIVE:
            return PAUSED, f"nativa en {model}{reserva}"
        return ACTIVE, f"{verdict.lower() or 'presente'} en {model}{reserva}"
    entry = measurements.get(model)
    if entry is None:
        known = ", ".join(sorted(measurements)) or "ninguno"
        return ACTIVE, f"sin medicion contra '{model}' (medido en: {known}) -> se enciende por defecto"
    verdict = str(entry.get("verdict", "")).upper()
    when = entry.get("measured", "sin fecha")
    if verdict == NATIVE:
        return PAUSED, f"nativa en {model}, medido {when}"
    if verdict == PARTIAL:
        return ACTIVE, f"parcial en {model} ({when}): colaborar no es garantizar"
    return ACTIVE, f"ausente en {model}, medido {when}"


def run_layer_probe(capability: dict[str, Any]) -> tuple[bool, str]:
    """¿La implementación de SOUL funciona? Pregunta independiente del modelo."""
    impl = capability.get("soul_impl")
    if not impl:
        return False, "la capacidad no declara soul_impl"
    target = REPO_ROOT / impl
    if not target.exists():
        return False, f"soul_impl no existe en disco: {impl}"
    tests = target.parent / f"test_{target.stem}.py"
    if not tests.exists():
        return False, f"sin test asociado ({tests.name}): no hay forma de saber si funciona"
    # Dos detalles que NO son cosméticos: el intérprete tiene que ser el del venv
    # (el `python3` del sistema no ve `seal_secrets`) y el cwd tiene que ser el
    # directorio del sujeto (desde la raíz, pytest colecta 0 tests y "0 tests" NO
    # es lo mismo que "todo pasa" — es la firma de una sonda que no midió nada).
    result = subprocess.run(
        [PYTEST_PYTHON, "-m", "pytest", tests.name, "-q"],
        capture_output=True,
        text=True,
        cwd=str(target.parent),
        timeout=300,
    )
    if "no tests ran" in (result.stdout + result.stderr):
        return False, "pytest no colecto NINGUN test: verde vacio, no evidencia"
    tail = (result.stdout or result.stderr).strip().splitlines()
    summary = tail[-1] if tail else "sin salida"
    return result.returncode == 0, summary


def _wired_in_settings(impl: str, model: str = "") -> tuple[bool, str]:
    """¿Está la capa ENGANCHADA al runtime que de verdad corre este asiento?

    Un archivo sano pero NO cableado es una capa apagada que parece encendida.
    Y hay una vuelta más, que encontré midiendo el asiento de ADA: esta función
    leía **siempre** `.claude/settings.local.json` —el archivo de hooks de Claude
    Code— sin mirar qué runtime corre el asiento. ADA corre en **Codex**, donde
    ese archivo no gobierna absolutamente nada. La sonda igual decía `cableada`.

    Es el mismo defecto que ya corregí un nivel más arriba: **preguntarle a la
    superficie equivocada devuelve una respuesta válida sobre otro sujeto.** Un
    verde así es peor que un rojo, porque afirma cobertura donde no hay ninguna.
    """
    runtime = model.split(":", 1)[1] if model.startswith("otro_runtime:") else "claude"
    superficies = _WIRING_SURFACE.get(runtime)
    if not superficies:
        return False, f"runtime '{runtime}' sin superficie de cableado conocida"
    # Marca en PALABRAS, no un asterisco: una marca críptica se lee como ruido y
    # el lector se queda con el verde. La reserva tiene que costar lo mismo que
    # la afirmación o no se transmite.
    reserva = (" [superficie inferida del binario, sin corrida que la confirme]"
               if runtime in _WIRING_UNCONFIRMED else "")
    faltantes = []
    for superficie in superficies:
        if not superficie.exists():
            faltantes.append(f"{superficie.name} no existe")
            continue
        if Path(impl).name in superficie.read_text(encoding="utf-8"):
            return True, f"enganchada en {superficie.name}{reserva}"
        faltantes.append(f"no aparece en {superficie.name}")
    return False, "; ".join(faltantes)


# Dónde se engancha una capa, POR RUNTIME.
#
# Acá había un comentario mío afirmando que Codex "no tiene ningún mecanismo de
# hooks". Es FALSO y lo retracté el 24-jul. Lo deduje de leer un `config.toml`
# sin sección `hooks`; el binario (`codex-cli 0.144.1`) trae motor completo:
# `PreToolUse PermissionRequest PostToolUse PreCompact PostCompact SessionStart
# SubagentStart`, config por `hooks.json` y el mismo wire `hookSpecificOutput`
# que usa Claude Code. **La ausencia de CONFIGURACIÓN no es la ausencia de
# MECANISMO**: para negar una capacidad hay que interrogar al ejecutable.
#
# Varias rutas candidatas por runtime, en orden de precedencia. Que la lista
# tenga entradas NO promete que el runtime lea esa ruta: eso sólo lo prueba una
# corrida real, y hasta tenerla el detalle lo dice en voz alta (ver abajo).
#
# 24-jul, segunda corrección de esta tabla en un día: faltaba
# `REPO_ROOT/.codex/hooks.json` — la ruta PROJECT-LOCAL, que es exactamente
# donde ADA instaló y ejecutó. Con la lista incompleta la sonda publicó
# `UNWIRED` sobre un asiento que YA estaba cableado: un rojo falso contra una
# compañera, emitido con toda la apariencia de una medición. El daño de omitir
# una ruta candidata no es simétrico — de menos acusás, de más sólo mirás un
# archivo que no existe.
_WIRING_SURFACE: dict[str, list[Path]] = {
    "claude": [REPO_ROOT / ".claude" / "settings.local.json"],
    "codex": [REPO_ROOT / ".codex" / "hooks.json",
              Path.home() / ".codex" / "hooks.json",
              REPO_ROOT / ".codex" / "config.toml"],
}

# Runtimes cuya superficie está inferida del binario pero NO confirmada por una
# corrida observada. Un `True` acá vale menos que un `True` en `claude`, y el
# texto tiene que decirlo: si no, la sonda lava la incertidumbre y el que lea la
# fila hereda una confianza que nadie ganó.
#
# `codex` SALE de este conjunto el 24-jul: ADA instaló en la ruta project-local,
# ejecutó y capturó payload real de `PreToolUse`/`PostToolUse`, y ALICE lo
# verificó desde afuera (sha coincidente con el publicado). Eso es la corrida
# observada que faltaba. **Evidencia de EJECUCIÓN gana sobre superficie
# INFERIDA** — que es justo el orden que yo invertí al publicar el rojo.
_WIRING_UNCONFIRMED: set[str] = set()


def scan(registry: dict[str, Any], model: str, only: str | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for capability in registry.get("capabilities", []):
        if only and capability.get("id") != only:
            continue
        state, reason = resolve_state(capability, model)
        wired, wired_detail = _wired_in_settings(capability.get("soul_impl", ""), model)
        healthy, summary = run_layer_probe(capability)
        rows.append(
            {
                "id": capability.get("id", "?"),
                "state": state,
                "reason": reason,
                "soul_layer_ok": healthy,
                "soul_layer_detail": summary,
                "wired": wired,
                "wired_detail": wired_detail,
                "impl": capability.get("soul_impl", ""),
            }
        )
    return rows


def _fleet_report(*, json_out: bool) -> int:
    """La matriz que necesita el panel: asiento x capacidad SOUL.

    Responde la pregunta que el panel de cambio de cerebro NO puede contestar
    solo: cambiar el modelo es un parámetro del launcher, pero **cada capa de
    SOUL fue medida contra UN cerebro**, y al mover el parámetro esas mediciones
    dejan de aplicar sin que nada avise. Esta salida hace visible ese costo.

    Sale != 0 por DOS causas independientes, y la distinción es el punto:

      rc=2  capas SIN MEDIR contra el cerebro de ese asiento  -> falta medir
      rc=1  capas encendidas y NO CABLEADAS al runtime        -> falta instalar

    El rc=1 existe porque hasta el 24-jul esta función sólo miraba `UNVERIFIED`.
    Con ADA en Codex daba rc=2 por no estar medida — y el día que se midiera, el
    estado dejaba de ser UNVERIFIED, `wired=False` seguía igual y **salía 0**.
    Es decir: el gate se apagaba justo cuando alguien hacía el trabajo de medir,
    certificando en verde un asiento con las dos capas apagadas. Un verde que
    llega por medir y no por arreglar es peor que no tener gate, porque se firma.
    """
    filas = fleet()
    if not filas:
        print("censo no disponible o sin asientos vivos", file=sys.stderr)
        return 2
    registro = load_registry()
    sin_medir: list[str] = []
    sin_cablear: list[str] = []
    salida = []
    encendidas = (ACTIVE, UNVERIFIED)
    for f in filas:
        rows = scan(registro, f["model"])
        f["capabilities"] = rows
        salida.append(f)
        if any(r["state"] == UNVERIFIED for r in rows):
            sin_medir.append(f["agent"])
        # Una capa PAUSED no cableada es coherente; una ENCENDIDA no cableada es
        # una promesa que el runtime no puede cumplir.
        apagadas = [r["id"] for r in rows if r["state"] in encendidas and not r["wired"]]
        if apagadas:
            sin_cablear.append(f"{f['agent']} ({', '.join(apagadas)})")

    if json_out:
        print(json.dumps({"fleet": salida}, ensure_ascii=False, indent=2))
    else:
        print(f"{'ASIENTO':<10} {'CEREBRO':<26} CAPAS SOUL")
        for f in salida:
            estados = ", ".join(
                f"{r['id']}={'UNWIRED' if r['state'] in encendidas and not r['wired'] else r['state']}"
                for r in f["capabilities"]
            )
            print(f"{f['agent']:<10} {f['model']:<26} {estados}")
    if sin_cablear:
        print(f"\n{len(sin_cablear)} asiento(s) con capas ENCENDIDAS pero NO CABLEADAS "
              "al runtime que corre: " + "; ".join(sin_cablear), file=sys.stderr)
    if sin_medir:
        print(f"\n{len(sin_medir)} asiento(s) con capas SIN MEDIR contra su cerebro: "
              + ", ".join(sin_medir), file=sys.stderr)
        return 2
    return 1 if sin_cablear else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", metavar="ID", help="medir una sola capacidad")
    parser.add_argument("--model", help="forzar el cerebro a evaluar (por defecto, el del entorno)")
    parser.add_argument("--agent", help="preguntarle al censo de FABLE con que cerebro corre ese asiento")
    parser.add_argument("--json", action="store_true", help="salida cruda")
    parser.add_argument("--fleet", action="store_true",
                        help="matriz asiento x capacidad: que queda sin medir si cambias un cerebro")
    args = parser.parse_args()

    if args.fleet:
        return _fleet_report(json_out=args.json)

    model = args.model or current_model(args.agent)
    rows = scan(load_registry(), model, only=args.check)

    if args.json:
        print(json.dumps({"model": model, "capabilities": rows}, ensure_ascii=False, indent=2))
    else:
        print(f"cerebro en uso: {model}\n")
        for row in rows:
            mark = "OK " if row["soul_layer_ok"] else "ROTA"
            wired = row.get("wired_detail", "cableada" if row["wired"] else "NO CABLEADA")
            print(f"[{row['state']:<16}] {row['id']}")
            print(f"           {row['reason']}")
            print(f"           capa SOUL: {mark} ({row['soul_layer_detail']}) — {wired}")

    # Sale != 0 si alguna capa que DEBE estar encendida no lo está de verdad.
    # Una capa PAUSED rota no es urgente; una ACTIVE rota es un agujero abierto.
    # `UNVERIFIED` cuenta como encendida a este efecto: la capa corre, así que si
    # además está rota el agujero es igual de real que en `ACTIVE`.
    encendidas = (ACTIVE, UNVERIFIED)
    broken = [r for r in rows if r["state"] in encendidas and not (r["soul_layer_ok"] and r["wired"])]
    if broken:
        print("\nACTIVE pero no operativa: " + ", ".join(r["id"] for r in broken), file=sys.stderr)
        return 1
    # Y sale != 0 también cuando el cerebro no se pudo nombrar, aunque todas las
    # capas estén sanas y cableadas. Es el fail-closed que pidió ADA, puesto en la
    # AFIRMACIÓN y no en el guard: un rc=0 acá significaría "medido y en orden",
    # que es exactamente lo que no pasó.
    sin_medir = [r for r in rows if r["state"] == UNVERIFIED]
    if sin_medir:
        print(f"\ncerebro '{model}' no identificado: {len(sin_medir)} capa(s) corriendo sin "
              "medicion contra este runtime. Capas ENCENDIDAS; estado NO afirmado.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
