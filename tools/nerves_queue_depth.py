#!/usr/bin/env python3
"""Contrapresion NERVES, incremento 1: hacer RUIDOSA la cola de handoffs.

Tarea #1325. Origen: 24-jul-2026, JARVIS reporto dos handoffs autenticados
(84abe048, d0beb492) acumulandose en su inbox **sin error, sin rechazo y sin
aviso**. El tablero no mostraba nada raro. Trabajo valido parado, en silencio.

POR QUE ESTE ARCHIVO NO LE PONE TTL AL CLAIM
--------------------------------------------
Primera lectura mia, sobre `nerves_mission_handoff.py` (la ruta de JARVIS):
`claim_handoff` (:922-937) no tiene TTL ni ruta de liberacion, y `hold_handoff`
(:986-1005) expone `lease_released` pero exige comando HOLD autenticado de
William y **terminaliza**. Conclui "no existe valvula". FABLE lo corrigio y tenia
razon: **existe, en el otro modulo.**

`nerves_agent_mission_core.py` (v2) SI escribe `lease_expires_at` en cada claim
(:1732, `claim_lease_seconds=180`) y expone `stale_claim_mission_ids` (:2200),
vivo en `nerves_local_sidecar.py:191,238`. Y su docstring dice textual:
*"Return expired claimed missions **without mutating or reassigning them**"* — o
sea que el v2 tampoco auto-libera: **reporta**. Fail-loud ya es la casa.

Pero la correccion abre algo mas filoso que lo que corrige. Los ROUTE declarados
en el core son cuatro: ADA, ALICE, NEXUS, FABLE. **JARVIS no tiene** — su propio
docstring lo dice: *"JARVIS keeps its closed Claude receipt v1 adapter. This
module is the v2 core."* Es un corte deliberado, no podredumbre. Pero el
resultado es que el lease cubre 4 de 5 colas, y **la que quedo afuera es
exactamente donde se trabo la cola.**

Y aun asi el lease no habria salvado a JARVIS: sus dos handoffs estan en
`live_notified` con `claim: False`. Nunca fueron reclamados, asi que
`stale_claim_mission_ids` —que filtra por `status == "claimed"`— no los ve. El
hueco real no es "claim sin vencimiento": es **entregado y jamas reclamado**,
invisible para las dos rutas. Eso es lo que mide este archivo.

Y por eso tampoco propongo auto-liberar: un lease que vence cambia "congelado
para siempre" por "dos workers en la misma mision" cuando el primero esta lento y
no muerto. **Se libera con muerte PROBADA, no con tiempo cumplido** — la senal de
liveness es el incremento 2 y todavia no existe.

SALIDA
------
Sale 1 si algún handoff lleva más de --max-espera-min en estado no terminal,
con o sin claim, o si el worker tiene muerte probada. Sale 2 si una superficie
esperada falta o no puede leerse, y 3 si se pidió publicar un cambio pero la
alerta no fue entregada. Escribe latido siempre, para que el auditor de
vigilantes pueda medir SU silencio: un vigilante que solo escribe cuando
encuentra algo es indistinguible de uno muerto.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# La raiz, no ROOT/"memory": el modulo se importa a si mismo como `memory.*`, asi
# que colgarse del subdirectorio rompe con ModuleNotFoundError.
sys.path.insert(0, str(ROOT))

from memory.nerves_mission_handoff import (  # noqa: E402
    DEFAULT_STATE,
    TERMINAL_STATES,
    claim_worker_liveness,
)
from memory.nerves_agent_mission_core import ROUTES  # noqa: E402

LATIDO = ROOT / "var" / "nerves_queue_depth.log"
# En `var/` del repo, NO en /tmp: bajo systemd con PrivateTmp el estado se
# destruye al terminar la unidad y la deteccion-por-cambio degrada a "alertar en
# cada tick", que es el flood que se quiere evitar.
ESTADO = ROOT / "var" / "nerves_queue_depth_state.json"

# El inbox por defecto de nerves_mission_handoff esta clavado a JARVIS. Se
# enumera el DIRECTORIO, no una lista escrita a mano: una lista propia no puede
# desmentirse a si misma, que es exactamente como reporte "1 de 10 vigilantes"
# el mismo dia con un denominador que era mi inventario.
INBOX_ROOT = ROOT / "research/flywire_results/nerves_orchestrator_inbox"


class StateReadError(RuntimeError):
    """El instrumento no pudo leer una superficie; nunca equivale a cola vacía."""


def colas() -> dict[str, Path]:
    """{agente: ruta del state.json}, enumerando el sustrato en disco."""
    return {p.name[: -len(".state.json")]: p for p in sorted(INBOX_ROOT.glob("*.state.json"))}


def expected_colas() -> dict[str, Path]:
    """Superficies declaradas por las configuraciones canónicas, no por el glob."""
    paths = {DEFAULT_STATE, *(route.state_path for route in ROUTES.values())}
    return {
        path.name[: -len(".state.json")]: path
        for path in sorted(paths)
    }


def _edad_min(marca: str | None, ahora: datetime) -> float | None:
    if not marca:
        return None
    try:
        return (ahora - datetime.fromisoformat(marca)).total_seconds() / 60.0
    except ValueError:
        return None


def atascados(state_path: Path, *, max_espera_min: float, ahora: datetime | None = None) -> list[dict]:
    """Handoffs en estado NO TERMINAL que llevan esperando de mas.

    Cubre las dos mitades, y la segunda por correccion propia. Mi primera version
    salteaba todo lo que tuviera claim, con el argumento de que un claim significa
    que alguien lo esta ejecutando. Falso: `d0beb492` estuvo en `running` con
    claim puesto mientras la sesion que lo reclamo no avanzaba. Filtrar por claim
    hacia INVISIBLE exactamente el caso congelado —el que motivo la tarea— y me
    dejaba viendo solo la mitad facil.

      * `sin_claim`: entregado y nunca reclamado. Ni el v1 ni el v2 lo ven; el
        `stale_claim_mission_ids` del v2 filtra por `status == "claimed"`.
      * `con_claim`: reclamado y sin terminar. Aca SI hay lease en el v2, pero no
        en la ruta v1 de JARVIS, y en ningun caso se libera solo.

    Ninguna de las dos muta nada. Reportar, no reasignar.
    """
    ahora = ahora or datetime.now(timezone.utc)
    try:
        datos = json.loads(state_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise StateReadError(
            f"{state_path}: lectura fallida ({type(exc).__name__})"
        ) from exc
    except json.JSONDecodeError as exc:
        raise StateReadError(
            f"{state_path}: JSON inválido (línea {exc.lineno}, columna {exc.colno})"
        ) from exc
    if not isinstance(datos, dict):
        raise StateReadError(f"{state_path}: raíz JSON no es un objeto")
    fuera = []
    for mid, rec in (datos.get("deliveries") or {}).items():
        if not isinstance(rec, dict):
            continue
        if rec.get("status") in TERMINAL_STATES:
            continue
        # `delivered_at` esta en None en los registros reales; la marca util es
        # la de notificacion en vivo. Se prueban las dos y se toma la primera
        # que exista, en vez de asumir cual poblo el despachador.
        claim = rec.get("claim")
        claim = claim if isinstance(claim, dict) else None
        # El reloj arranca en el ultimo evento REAL de la mision. Para una
        # reclamada eso es el claim, no la notificacion: si no, una mision
        # reclamada hace un minuto arrastra la antiguedad de su entrega y sale
        # atascada apenas la toman.
        campos = (("claimed_at",) if claim else ()) + (
            "live_notified_at", "delivered_at", "created_at")
        origen = claim if claim else rec
        edad = None
        for campo in campos:
            edad = _edad_min((origen if campo == "claimed_at" else rec).get(campo), ahora)
            if edad is not None:
                break
        # Incremento 2: liveness VERIFICADA del worker, no inferida del reloj.
        # Un worker con muerte probada no necesita cumplir la espera: ya no va a
        # avanzar nunca, y esperarle una hora sólo retrasa el aviso. Lo inverso
        # tambien vale y es lo que protege: `unmeasurable` no acelera nada.
        vida = claim_worker_liveness(claim) if claim else None
        muerto = bool(vida and vida["state"] == "dead")
        if edad is None or edad > max_espera_min or muerto:
            fuera.append({"mission_id": mid, "status": rec.get("status"),
                          "categoria": "con_claim" if claim else "sin_claim",
                          "worker_id": (claim or {}).get("worker_id"),
                          "espera_min": edad, "sin_marca": edad is None,
                          "liveness": vida["state"] if vida else None,
                          "liveness_detalle": vida["detail"] if vida else None})
    return fuera


def _alertar(nuevos: list[str], resueltos: list[str]) -> bool:
    """Avisa al equipo. Solo se llama con cambios: alertar por ESTADO en vez de
    por CAMBIO entrena al operador a filtrar el aviso, y entonces el vigilante
    sigue vivo pero ya no vigila a nadie."""
    lineas = []
    if nuevos:
        lineas.append("**Cola NERVES — handoff(s) atascado(s):**")
        lineas += [f"- `{n}`" for n in nuevos]
        lineas.append("")
        lineas.append("Entregado y sin avanzar. No se reasigna nada: solo se reporta.")
    if resueltos:
        lineas.append("**Destrabado(s):** " + ", ".join(f"`{r}`" for r in resueltos))
    cmd = [sys.executable, str(ROOT / "scripts" / "seal_send.py"),
           "NEXUS", "equipo", "\n".join(lineas), "--channel", "web_chat", "--type", "status"]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
    # Sin este chequeo, un envio fallido y uno exitoso emiten la misma senal
    # (ninguna), que es el modo de fallo por defecto de nuestras herramientas.
    if r.returncode != 0 or '"ok":true' not in (r.stdout or ""):
        print(f"ALERTA NO ENTREGADA (rc={r.returncode}): {(r.stdout or r.stderr)[:200]}",
              file=sys.stderr)
        return False
    return True


def _cambios(ids: set[str]) -> tuple[list[str], list[str]]:
    try:
        antes = set(json.loads(ESTADO.read_text(encoding="utf-8")))
    except FileNotFoundError:
        antes = set()
    except OSError as exc:
        raise StateReadError(
            f"{ESTADO}: lectura de estado de alertas falló ({type(exc).__name__})"
        ) from exc
    except json.JSONDecodeError as exc:
        raise StateReadError(
            f"{ESTADO}: estado de alertas JSON inválido "
            f"(línea {exc.lineno}, columna {exc.colno})"
        ) from exc
    ESTADO.parent.mkdir(parents=True, exist_ok=True)
    try:
        ESTADO.write_text(json.dumps(sorted(ids)), encoding="utf-8")
    except OSError as exc:
        raise StateReadError(
            f"{ESTADO}: escritura de estado de alertas falló ({type(exc).__name__})"
        ) from exc
    return sorted(ids - antes), sorted(antes - ids)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-espera-min", type=float, default=60.0)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--alert", action="store_true",
                    help="avisa al equipo SOLO cuando la lista de atascados cambia")
    args = ap.parse_args()

    ahora = datetime.now(timezone.utc)
    superficies = colas()
    esperadas = expected_colas()
    errores_instrumento: dict[str, str] = {}
    if not superficies:
        errores_instrumento["_discovery"] = (
            f"{INBOX_ROOT}: no se descubrió ningún *.state.json"
        )
    faltantes = sorted(set(esperadas) - set(superficies))
    if faltantes:
        errores_instrumento["_missing_expected"] = (
            "faltan superficies canónicas: " + ", ".join(faltantes)
        )
    informe = {}
    for agente, path in superficies.items():
        try:
            informe[agente] = atascados(
                path, max_espera_min=args.max_espera_min, ahora=ahora
            )
        except StateReadError as exc:
            informe[agente] = []
            errores_instrumento[agente] = str(exc)
    total = sum(len(v) for v in informe.values())

    if args.json:
        print(json.dumps({
            "schema": "seal.nerves.queue-depth.v2",
            "superficies_esperadas": sorted(esperadas),
            "superficies": sorted(informe),
            "superficies_total": len(informe),
            "errores_instrumento": errores_instrumento,
            "atascados": informe,
            "total": total,
        }, indent=2, default=str))
    else:
        for agente, filas in informe.items():
            marca = f"{len(filas)} ATASCADOS" if filas else "0"
            print(f"{agente:<10} {marca}")
            for f in filas:
                espera = "sin marca de tiempo" if f["sin_marca"] else f"{f['espera_min']/60:.1f} h"
                quien = f" worker={f['worker_id']}" if f["worker_id"] else ""
                vida = (f"  [worker {f['liveness']}: {f['liveness_detalle']}]"
                        if f.get("liveness") else "")
                print(f"    {f['mission_id'][:8]}  {f['status']:<14} {f['categoria']:<10} "
                      f"esperando {espera}{quien}{vida}")
        for agente, error in errores_instrumento.items():
            print(f"{agente:<10} ERROR INSTRUMENTO: {error}", file=sys.stderr)

    alerta_fallida = False
    if args.alert and not errores_instrumento:
        try:
            nuevos, resueltos = _cambios(
                {f["mission_id"] for filas in informe.values() for f in filas})
        except StateReadError as exc:
            errores_instrumento["_alert_state"] = str(exc)
        else:
            if nuevos or resueltos:
                alerta_fallida = not _alertar(nuevos, resueltos)

    # El latido se escribe SIEMPRE, tambien cuando no hay nada que reportar. Si
    # solo escribiera al encontrar algo, su silencio significaria dos cosas
    # opuestas —todo bien / estoy muerto— y no habria forma de distinguirlas.
    try:
        LATIDO.parent.mkdir(parents=True, exist_ok=True)
        LATIDO.write_text(
            f"{ahora.isoformat()} colas={len(informe)} atascados={total}\n", encoding="utf-8")
    except OSError as exc:
        errores_instrumento["_heartbeat"] = (
            f"{LATIDO}: latido no escrito ({type(exc).__name__})"
        )

    if errores_instrumento:
        print(
            f"\nINSTRUMENTO MUDO en {len(errores_instrumento)} superficie(s); "
            "el total observado no autoriza declarar la cola limpia",
            file=sys.stderr,
        )
        return 2
    if alerta_fallida:
        return 3
    if total:
        print(f"\n{total} handoff(s) sin avanzar "
              f"(sin_claim o con_claim) más de {args.max_espera_min:.0f} min",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
