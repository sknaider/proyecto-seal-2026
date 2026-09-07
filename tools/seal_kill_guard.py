#!/usr/bin/env python3
"""Verificar y ejecutar un kill de asiento SEAL, fail-closed.

Por qué existe
--------------
El protocolo KILL del equipo vivía **sólo en prosa** (`sandbox-agent/CLAUDE.md`,
`agents/NEXUS/MIS_DIRECTRICES_NEXUS.md`): un checklist que cada agente aplicaba a
mano. El 24-jul-2026 fallaron **tres** listas de PIDs en una sola tarde:

  * ALICE publicó `matar 878074, despues 484520` — correcta al medirla, y media
    hora después habría destruido el JARVIS sano.
  * La orden nombraba `437824`, que para entonces **ya no existía**.
  * `878074` seguía vivo, así que un chequeo de "¿existe el PID?" lo dejaba
    pasar — pero era un `bash` de hacía seis días, **no un asiento**.

Ese último caso es el que obliga a que esto sea código: *"existe"* no es *"es el
sujeto"*, y las dos lecturas se ven idénticas desde `kill -0`.

Contrato (propuesto por ADA, 24-jul): validar **inmediatamente antes del efecto**
PID + starttime + exe real + argv `--name` + ancestría + unicidad de asiento. Si
cualquiera difiere, **abortar sin matar**.

Por qué el guard también MATA en vez de sólo verificar
------------------------------------------------------
Si este script sólo devolviera un veredicto y otro proceso ejecutara el `kill`,
quedaría una ventana entre la verificación y el efecto — la misma clase de
carrera que hizo caducar las listas de hoy, sólo que más corta. Verificar y matar
en el mismo proceso, con `starttime` releído justo antes de la señal, la cierra.

Y el ejecutor no puede estar dentro del árbol que mata: si lo está, se apaga a sí
mismo a mitad de la operación y el resultado depende de a quién le llegue primero
la señal. Eso se chequea explícito y sale `EXIT_SELF_IN_TREE`.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
import signal
import sys
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_MISMATCH = 1        # alguna expectativa no se cumple -> NO se mató nada
EXIT_NOT_FOUND = 2       # el PID no existe
EXIT_SELF_IN_TREE = 3    # el ejecutor vive dentro del árbol objetivo
EXIT_AMBIGUOUS = 4       # seat_count != 1

PROC = Path("/proc")


def _stat_fields(pid: int) -> list[str] | None:
    """Campos de /proc/<pid>/stat, con el comm entre paréntesis neutralizado.

    El comm puede contener espacios y paréntesis, así que partir por espacios sin
    cuidado corre todos los índices y `starttime` deja de ser `starttime`.
    """
    try:
        raw = (PROC / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None
    cierre = raw.rfind(")")
    if cierre == -1:
        return None
    cabeza = raw[:cierre].split(" ", 1)
    resto = raw[cierre + 2:].split()
    return [cabeza[0], "comm"] + resto


def starttime(pid: int) -> str | None:
    """Jiffies desde el boot en que arrancó el proceso — campo 22 de stat.

    Es lo único que distingue "el PID 878074 que medí" de "el PID 878074 que el
    kernel reasignó después". Un PID sin starttime es un puntero, no una
    identidad.
    """
    campos = _stat_fields(pid)
    if campos is None or len(campos) < 22:
        return None
    return campos[21]


def ppid(pid: int) -> int | None:
    campos = _stat_fields(pid)
    if campos is None or len(campos) < 4:
        return None
    try:
        return int(campos[3])
    except ValueError:
        return None


def exe_real(pid: int) -> str:
    try:
        return os.path.basename(os.path.realpath(PROC / str(pid) / "exe"))
    except OSError:
        return ""


def exe_path(pid: int) -> str:
    try:
        return os.path.realpath(PROC / str(pid) / "exe")
    except OSError:
        return ""


def comm(pid: int) -> str:
    try:
        return (PROC / str(pid) / "comm").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


# Flags cuyo VALOR puede mostrarse. Todo lo demás se cuenta y se redacta.
#
# Allowlist y no denylist a propósito: con una denylist, un flag nuevo que nadie
# agregó a la lista se filtra por defecto, y el fallo es silencioso. Acá lo que
# no se conoce no se muestra.
_ARGS_PUBLICABLES = frozenset({"--name", "--model", "--effort", "--profile"})


def cmdline_safe(pid: int) -> str:
    """El cmdline SIN el prompt de sistema del agente.

    El `cmdline` de un asiento de la familia contiene su `--append-system-prompt`
    entero: contratos internos, reglas de silencio, identidad. En JARVIS son
    5.481 caracteres, y **los primeros 200 ya entran en el prompt** — o sea que
    el truncado ingenuo que tenía este informe (`[:200]`) filtraba igual, sólo
    que menos.

    Lo nombró FABLE el 24-jul tras volcarlo sin querer. Cae de lleno en la regla
    de William: el terminal y los mensajes de un agente son su intimidad. Un
    guard de seguridad que para operar publica lo que debe proteger no es un
    guard: es la fuga con otro nombre.
    """
    argv = _cmdline_raw(pid)
    if not argv:
        return ""
    partes = [os.path.basename(argv[0])]
    redactados = 0
    i = 1
    while i < len(argv):
        arg = argv[i]
        clave = arg.split("=", 1)[0]
        if clave in _ARGS_PUBLICABLES:
            if "=" in arg:
                partes.append(arg)
                i += 1
            elif i + 1 < len(argv):
                partes.extend([arg, argv[i + 1]])
                i += 2
            else:
                partes.append(arg)
                i += 1
            continue
        if arg.startswith("-"):
            # Un flag NO publicable puede traer su valor PEGADO (`--api-key=SECRETO`).
            # Hasta el 24-jul esta rama hacía `partes.append(arg)` entero, así que
            # la allowlist filtraba el valor separado y dejaba pasar el pegado: la
            # misma variable, dos sintaxis, una sola cubierta. Lo encontró ADA
            # leyendo el código — mis 21 tests probaban `--flag valor` y ninguno
            # `--flag=valor`, o sea que el test estaba escrito sobre la forma que
            # yo ya tenía en la cabeza.
            if "=" in arg:
                partes.append(arg.split("=", 1)[0] + "=[redactado]")
            else:
                partes.append(arg)
        else:
            redactados += 1
        i += 1
    if redactados:
        partes.append(f"[{redactados} valor(es) redactado(s)]")
    return " ".join(partes)


def env_field(pid: int, nombre: str) -> str | None:
    """UN campo del environ ajeno. Nunca el bloque.

    `/proc/<pid>/environ` de un asiento SEAL trae ~71 variables, y entre ellas
    **`SEAL_SESSION_TOKEN`** — el secreto por-sesión con el que ese agente prueba
    su identidad ante SOUL. También `XAUTHORITY` y `SSH_AUTH_SOCK`. Volcarlo
    entero para leer una variable entrega la credencial de un compañero a
    cualquiera que lea el mensaje, el log o el transcript.

    Medido el 24-jul: el comando que circulaba por el equipo volcaba 71
    variables para usar 2. Esta función lee, filtra en memoria y devuelve sólo
    lo pedido; el bloque fuente no sale de acá.

    No es un control de acceso —cualquier proceso del mismo usuario puede leer
    ese archivo— sino de **minimización**: reduce a cero las veces que el secreto
    pasa por un canal donde puede quedar escrito.
    """
    try:
        raw = (PROC / str(pid) / "environ").read_bytes().decode("utf-8", "replace")
    except OSError:
        return None
    prefijo = f"{nombre}="
    for var in raw.split("\0"):
        if var.startswith(prefijo):
            return var[len(prefijo):]
    return None


def env_names(pid: int) -> list[str]:
    """Los NOMBRES de las variables, sin ningún valor.

    Para auditar qué secretos viajan en un asiento sin tocar ninguno: es el
    oráculo-propiedad aplicado al environ. Publicar esta salida es seguro;
    publicar la de `env_field` casi nunca lo es.
    """
    try:
        raw = (PROC / str(pid) / "environ").read_bytes().decode("utf-8", "replace")
    except OSError:
        return []
    return [v.split("=", 1)[0] for v in raw.split("\0") if "=" in v]


def exe_identities(pid: int) -> dict[str, str]:
    """Las tres señales que nombran a un ejecutable, porque NINGUNA sola alcanza.

    Cazado el 24-jul corriendo este guard contra su propio incidente: para un
    asiento Claude Code real,

        realpath(/proc/PID/exe) -> /home/dadito/.local/share/claude/versions/2.1.219
        basename de eso         -> "2.1.219"      <- la VERSION, no el comando
        argv[0]                 -> ".../bin/claude"
        comm                    -> "claude"

    Comparar `--expect-exe claude` contra el basename del realpath daba MISMATCH
    sobre el asiento sano. Los tests no lo vieron porque usaban `python3`, cuyo
    realpath sí termina en el nombre del comando: **el binario de prueba era más
    amable que el binario real.**

    Fuerza de cada señal, que hay que tener presente al usarlas:
      * `path`/`basename` — del kernel, NO falsificable por el proceso.
      * `comm`            — del kernel, pero el proceso puede cambiarlo (PR_SET_NAME).
      * `argv0`           — enteramente bajo control del proceso.
    Para un guard de seguridad el `path` es el ancla; las otras dos existen
    porque el nombre humano del comando vive ahí y no en el path.
    """
    argv = _cmdline_raw(pid)
    return {
        "path": exe_path(pid),
        "basename": exe_real(pid),
        "comm": comm(pid),
        "argv0": os.path.basename(argv[0]) if argv else "",
    }


def exe_matches(pid: int, esperado: str) -> bool:
    """¿`esperado` nombra al ejecutable de `pid` por cualquiera de sus señales?

    Se acepta también que aparezca como COMPONENTE de la ruta real
    (`.../share/claude/versions/2.1.219`), que es el caso de un binario
    versionado. Esto ensancha a propósito: el daño de un falso MISMATCH acá es
    bloquear una operación legítima y hacer que alguien la ejecute a mano sin
    guard — peor que el chequeo laxo. La identidad fina la dan `--expect-name` y
    `--expect-starttime`, que sí son estrictos.
    """
    if not esperado:
        return True
    ident = exe_identities(pid)
    if esperado in (ident["basename"], ident["comm"], ident["argv0"]):
        return True
    return esperado in Path(ident["path"]).parts


def _cmdline_raw(pid: int) -> list[str]:
    """Lectura CRUDA del argv. Privada a propósito (minimización en origen, ADA).

    Devuelve el prompt de sistema completo del asiento. El nombre lleva guion
    bajo para que el uso casual —`import seal_kill_guard; sk._cmdline_raw(pid)`—
    caiga en `cmdline_safe()` y no acá. Que la función segura sea la de nombre
    fácil no es cosmética: la fuga de hoy la cometieron cuatro agentes llamando
    a la herramienta obvia, no buscando el secreto.

    Sólo debe usarse para EXTRAER UN CAMPO y comparar en memoria, nunca para
    imprimir, loguear ni persistir el bloque entero.
    """
    try:
        raw = (PROC / str(pid) / "cmdline").read_bytes()
    except OSError:
        return []
    return [p for p in raw.decode("utf-8", "replace").split("\0") if p]


def seat_name(pid: int) -> str:
    """El valor de `--name` en el argv, o "" si el proceso no lo declara.

    Se lee el argumento SIGUIENTE a `--name`, no una subcadena del cmdline: el
    24-jul un `--name == "JARVIS"` dio 0 asientos porque el real era
    `JARVIS — Team SEAL`, y un filtro `*claude*` matcheó el bash de la tool call
    porque su cmdline incluye `.claude/shell-snapshots/`.
    """
    argv = _cmdline_raw(pid)
    for i, arg in enumerate(argv):
        if arg == "--name" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--name="):
            return arg.split("=", 1)[1]
    return ""


def ancestry(pid: int, tope: int = 64) -> list[int]:
    cadena: list[int] = []
    actual = pid
    for _ in range(tope):
        padre = ppid(actual)
        if padre is None or padre <= 1 or padre in cadena:
            break
        cadena.append(padre)
        actual = padre
    return cadena


def live_seats(exe: str = "claude") -> list[dict[str, Any]]:
    """Todo proceso vivo cuyo ejecutable real es `exe`, con su identidad completa."""
    asientos = []
    for entrada in PROC.iterdir():
        if not entrada.name.isdigit():
            continue
        pid = int(entrada.name)
        # Por `exe_matches` y no por basename: el censo tiene que encontrar los
        # asientos aunque el binario sea versionado (`.../versions/2.1.219`).
        # Con el filtro viejo `live_seats("claude")` devolvia CERO y el chequeo
        # de unicidad se convertia en `seat_count == 0` -> AMBIGUOUS siempre.
        if not exe_matches(pid, exe):
            continue
        # `measured_at` y `source` van en CADA fila, no en un encabezado.
        #
        # El 24-jul tres tablas de estado de procesos se contradijeron entre sí
        # —ventanas de contexto, listas de kill, mi propio `git status`— y en los
        # tres casos las filas eran compatibles: describían momentos distintos.
        # Lo único que faltaba para verlo era la hora al lado del dato. Una fila
        # que viaja sin su hora se cita como permanente, porque nada en ella dice
        # que caduca.
        asientos.append({
            "pid": pid,
            "exe": exe,
            "agent": (seat_name(pid).split("—")[0].strip() or None),
            "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": "seal_kill_guard.live_seats",
            "name": seat_name(pid),
            "starttime": starttime(pid),
            "ppid": ppid(pid),
        })
    return sorted(asientos, key=lambda a: a["pid"])


def verify(
    pid: int,
    *,
    expect_exe: str | None = None,
    expect_name: str | None = None,
    expect_starttime: str | None = None,
    expect_ppid: int | None = None,
    require_unique_seat: bool = True,
    executor_pid: int | None = None,
) -> tuple[int, dict[str, Any]]:
    """Devuelve (codigo_de_salida, informe). No mata ni cambia nada."""
    yo = os.getpid() if executor_pid is None else executor_pid
    informe: dict[str, Any] = {"pid": pid, "checks": {}, "observed": {}}

    if not (PROC / str(pid)).exists():
        informe["verdict"] = "NOT_FOUND"
        informe["reason"] = (
            f"el pid {pid} no existe. Una orden que lo nombra fue medida antes y "
            "ya caduco: re-derivar el sujeto, no reintentar el numero."
        )
        return EXIT_NOT_FOUND, informe

    observado = {
        "exe": exe_real(pid),
        "name": seat_name(pid),
        "starttime": starttime(pid),
        "ppid": ppid(pid),
        "cmdline": cmdline_safe(pid),
    }
    informe["observed"] = observado

    # El ejecutor no puede estar dentro del arbol que va a matar: se apagaria a
    # si mismo a mitad de la operacion.
    if yo == pid or pid in ancestry(yo):
        informe["verdict"] = "SELF_IN_TREE"
        informe["reason"] = (
            f"el ejecutor (pid {yo}) vive dentro del arbol objetivo {pid}: no puede "
            "orquestar su propia sustitucion. Ejecutar desde fuera del arbol."
        )
        return EXIT_SELF_IN_TREE, informe

    esperado = {
        "exe": expect_exe,
        "name": expect_name,
        "starttime": expect_starttime,
        "ppid": expect_ppid,
    }
    informe["exe_identities"] = exe_identities(pid)
    difieren = []
    for campo, valor in esperado.items():
        if valor is None:
            informe["checks"][campo] = "no declarado"
            continue
        if campo == "exe":
            # Un binario versionado no se nombra por el basename de su ruta real.
            coincide = exe_matches(pid, str(valor))
            detalle = f"esperado={valor} identidades={informe['exe_identities']}"
        else:
            coincide = str(observado.get(campo)) == str(valor)
            detalle = f"esperado={valor} observado={observado.get(campo)}"
        informe["checks"][campo] = "OK" if coincide else detalle
        if not coincide:
            difieren.append(campo)

    if difieren:
        informe["verdict"] = "MISMATCH"
        informe["reason"] = (
            "no coinciden: " + ", ".join(difieren) +
            ". El pid existe pero NO es el sujeto que se midio."
        )
        return EXIT_MISMATCH, informe

    if require_unique_seat and expect_name:
        gemelos = [a for a in live_seats(observado["exe"]) if a["name"] == expect_name]
        informe["seat_count"] = len(gemelos)
        informe["seats"] = gemelos
        if len(gemelos) != 1:
            informe["verdict"] = "AMBIGUOUS"
            informe["reason"] = (
                f"hay {len(gemelos)} asientos vivos con name={expect_name!r}; el "
                "contrato exige exactamente 1. Con 0 el sujeto ya murio; con 2+ "
                "matar el equivocado es indistinguible de matar el correcto."
            )
            return EXIT_AMBIGUOUS, informe

    informe["verdict"] = "OK"
    informe["reason"] = "todas las expectativas declaradas coinciden"
    return EXIT_OK, informe


def verify_and_kill(pid: int, sig: int = signal.SIGTERM, **expectativas: Any) -> tuple[int, dict[str, Any]]:
    """Verifica y, sólo si todo coincide, mata — releyendo `starttime` justo antes.

    La relectura no es paranoia decorativa: entre la verificación y la señal el
    proceso puede morir y el kernel reasignar el número. Es una ventana chica y
    es exactamente la clase de ventana que nos falló hoy en escala de minutos.
    """
    codigo, informe = verify(pid, **expectativas)
    if codigo != EXIT_OK:
        informe["killed"] = False
        return codigo, informe

    # ORDEN DELIBERADO: se abre el pidfd ANTES de revalidar.
    #
    # ADA señaló que releer `starttime` y despues llamar `kill(2)` deja una
    # ventana residual: entre las dos cosas el proceso puede morir y el kernel
    # reciclar el numero. Un pidfd queda ligado a **esa instancia** del proceso,
    # no al entero; si el sujeto muere, el fd caduca y la señal falla en vez de
    # ir a parar a un inquilino nuevo.
    #
    # Abrirlo primero y revalidar despues es lo que cierra el hueco: si el
    # starttime todavia coincide DESPUES de tener el fd, el fd es del sujeto que
    # se verifico. Al reves —revalidar y luego abrir— la ventana sigue ahi, solo
    # que corrida un renglon.
    fd = None
    soporta_pidfd = hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal")
    try:
        if soporta_pidfd:
            try:
                fd = os.pidfd_open(pid)
            except (OSError, ProcessLookupError) as exc:
                # FAIL-CLOSED (corregido 24-jul por ADA). Antes esto caía a
                # `os.kill(pid)`, y ahí está el daño: el fallback mandaba la
                # señal al PID desnudo, es decir **al inquilino nuevo** si el
                # número ya había sido reasignado.
                #
                # CORREGIDA la justificación, misma tarde y también por ADA: yo
                # había escrito que "el único motivo para que pidfd_open falle es
                # que el proceso no exista". **Falso** — también falla con EPERM,
                # EMFILE/ENFILE (sin descriptores) o ENOSYS en un kernel parcial.
                # El comportamiento correcto no cambia, pero la razón sí: no se
                # aborta porque sepamos que murió, se aborta **porque no sabemos
                # por qué falló** y la operación siguiente es irreversible.
                # Un fail-closed sostenido por una causa única se afloja el día
                # que aparece la segunda causa.
                #
                # Un fallback que degrada a la operación insegura convierte una
                # condición de error en el peor caso posible. No hay fallback:
                # se aborta.
                informe["verdict"] = "MISMATCH"
                informe["reason"] = (
                    f"pidfd_open({pid}) fallo en una plataforma que lo soporta "
                    f"({type(exc).__name__}): el proceso ya no existe y el numero pudo "
                    "reasignarse. Abortado sin matar — no se degrada a os.kill."
                )
                informe["killed"] = False
                informe["delivery"] = "abortado: pidfd_open fallo"
                return EXIT_MISMATCH, informe

        antes = informe["observed"]["starttime"]
        ahora = starttime(pid)
        if ahora != antes:
            informe["verdict"] = "MISMATCH"
            informe["reason"] = (
                f"starttime cambio entre la verificacion ({antes}) y la señal ({ahora}): "
                "el numero fue reasignado. Abortado sin matar."
            )
            informe["killed"] = False
            return EXIT_MISMATCH, informe

        if fd is not None:
            signal.pidfd_send_signal(fd, sig)
            informe["delivery"] = "pidfd_send_signal"
        else:
            # ENDURECIDO 24-jul (ADA): `pidfd` OBLIGATORIO para emitir señal.
            #
            # Yo había dejado `os.kill` con la ventana residual DECLARADA para el
            # caso de un kernel sin pidfd. ADA cerró también esa puerta, y tiene
            # razón: **declarar un riesgo no lo mitiga**. El informe decía la
            # verdad y el proceso equivocado se moría igual.
            #
            # Que la degradación sea honesta la vuelve auditable, no segura. En
            # una operación irreversible el único fallback aceptable es no
            # ejecutar: correr el kill a mano, con el operador enterado, es
            # estrictamente mejor que una herramienta que mata por número.
            informe["verdict"] = "MISMATCH"
            informe["reason"] = (
                "esta plataforma no expone pidfd_open/pidfd_send_signal: no hay forma "
                "de ligar la señal a la INSTANCIA, solo al numero de PID. Abortado sin "
                "matar — pidfd es obligatorio para emitir señal."
            )
            informe["killed"] = False
            informe["delivery"] = "abortado: plataforma sin pidfd"
            return EXIT_MISMATCH, informe
    finally:
        if fd is not None:
            os.close(fd)

    informe["killed"] = True
    informe["signal"] = sig
    return EXIT_OK, informe


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pid", type=int, nargs="?", help="pid objetivo")
    parser.add_argument("--expect-exe", help="basename del ejecutable real, ej. claude")
    parser.add_argument("--expect-name", help="valor exacto de --name, ej. 'JARVIS — Team SEAL'")
    parser.add_argument("--expect-starttime", help="campo 22 de /proc/<pid>/stat")
    parser.add_argument("--expect-ppid", type=int)
    parser.add_argument("--allow-duplicate-seat", action="store_true",
                        help="no exigir seat_count==1 (por defecto SE exige)")
    parser.add_argument("--kill", action="store_true",
                        help="matar si y solo si todo coincide (por defecto solo verifica)")
    parser.add_argument("--signal", type=int, default=signal.SIGTERM)
    parser.add_argument("--census", action="store_true",
                        help="listar asientos vivos con su identidad completa y salir")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.census:
        asientos = live_seats(args.expect_exe or "claude")
        if args.json:
            print(json.dumps({"seats": asientos}, ensure_ascii=False, indent=2))
        else:
            print(f"{'PID':>8}  {'STARTTIME':>12}  {'PPID':>7}  NAME")
            for a in asientos:
                print(f"{a['pid']:>8}  {str(a['starttime']):>12}  {str(a['ppid']):>7}  {a['name'] or '(sin --name)'}")
        return EXIT_OK

    if args.pid is None:
        parser.error("hace falta un pid (o --census)")

    expectativas = {
        "expect_exe": args.expect_exe,
        "expect_name": args.expect_name,
        "expect_starttime": args.expect_starttime,
        "expect_ppid": args.expect_ppid,
        "require_unique_seat": not args.allow_duplicate_seat,
    }
    if args.kill:
        codigo, informe = verify_and_kill(args.pid, args.signal, **expectativas)
    else:
        codigo, informe = verify(args.pid, **expectativas)

    if args.json:
        print(json.dumps(informe, ensure_ascii=False, indent=2))
    else:
        print(f"[{informe['verdict']}] pid={informe['pid']}")
        print(f"  {informe['reason']}")
        for campo, estado in informe.get("checks", {}).items():
            print(f"    {campo:<12} {estado}")
        if informe.get("killed"):
            print(f"  SEÑAL ENVIADA: {informe['signal']}")
        elif args.kill:
            print("  NO se envio ninguna señal.")
    return codigo


if __name__ == "__main__":
    sys.exit(main())
