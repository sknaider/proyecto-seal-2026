"""Enrutamiento canal -> instancia. Contrato del §9 de SPEC_CLON_POR_USUARIO_V1.

JARVIS, 31-jul-2026. Carril asignado por FABLE: canal y enrutamiento.

QUE PROBLEMA RESUELVE, en una linea: hoy todos los usuarios caen en la MISMA
cabeza. El puente temporal que puse esta mañana entrega a katy, a William y a
Henry al mismo agente, mezclados. Esto decide, de forma determinista y sin
modelo de por medio, QUE INSTANCIA consume cada mensaje.

LA REGLA QUE GOBIERNA ESTE ARCHIVO (§9), y el orden importa:

    superuser / admin                -> agente CANONICO
    basic CON asignacion explicita   -> instancia AGENTE-u<user_id>
    basic sin asignacion             -> RECHAZO explicito
    lista de agentes vacia           -> RECHAZO explicito
    instancia no sana / gate vencido -> RECHAZO explicito

    *** NUNCA cae al canonico. ***

El fallback al canonico seria el peor resultado posible: un usuario `basic` sin
instancia sana terminaria hablando con el agente que tiene el corpus de William.
Un 503 se ve y se arregla; un fallback silencioso se descubre en la auditoria de
dentro de seis meses. Por eso todo camino que no sea un permiso POSITIVO Y
EXPLICITO termina en rechazo.

AUTORIDAD DE CADA DATO -- lo que este modulo NO acepta del payload:

    user_id   de la SESION / DB          nunca del mensaje
    role      de chat_users              nunca del modelo ni del payload
    agent     de la asignacion/launcher  nunca del texto

Este modulo recibe esos valores YA RESUELTOS por quien tiene la autoridad. No
los descubre ni los adivina: si le pasan basura, rechaza. Es a proposito -- un
resolvedor que acepta `role` desde el mensaje es un escalador de privilegios con
forma de funcion de ruteo.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

# `basic` es el unico rol que va a instancia propia. Los otros dos usan el
# asiento canonico -- decision de William: "los usuarios son contados y
# designados por mi de confianza como henry y etc".
ROLES_CANONICOS = frozenset({"superuser", "admin"})
ROLES_CLONADOS = frozenset({"basic"})
ROLES_VALIDOS = ROLES_CANONICOS | ROLES_CLONADOS

_AGENTE_OK = re.compile(r"^[A-Z][A-Z0-9_]{1,15}$")


class DestinoInvalido(ValueError):
    """Entrada que no se puede rutear con seguridad. Nunca se degrada a canonico."""


@dataclass(frozen=True)
class Destino:
    """A donde va el mensaje. `rechazo` distinto de None significa: NO se entrega."""

    tipo: str                      # "canonico" | "instancia" | "rechazo"
    agente: str | None = None
    instance_key: str | None = None
    rechazo: str | None = None     # motivo legible, para el 503 y para el log
    motivo: str = ""               # por que se decidio asi -- auditable

    @property
    def entregable(self) -> bool:
        return self.tipo in ("canonico", "instancia")


def instance_key(agente: str, user_id: int) -> str:
    """`ADA-u103`. Usa user_id y NO username: renombrar a alguien no debe mover
    su memoria ni su credencial (§4)."""
    if not _AGENTE_OK.match(str(agente or "")):
        raise DestinoInvalido(f"agente invalido: {agente!r}")
    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
        raise DestinoInvalido(f"user_id invalido: {user_id!r}")
    return f"{agente}-u{user_id}"


def resolver_destino(
    *,
    agente: str,
    user_id: int,
    role: str,
    agentes_asignados: Sequence[str],
    instancias_sanas: Iterable[str] = (),
) -> Destino:
    """Decide el destino de un mensaje de `user_id` dirigido a `agente`.

    Todo parametro es obligatorio y por nombre: una llamada posicional floja es
    justo como se cuela un `role` en el lugar equivocado.
    """
    rol = str(role or "").strip().lower()
    asignados = [str(a).strip().upper() for a in (agentes_asignados or [])]
    sanas = {str(s).strip() for s in (instancias_sanas or ())}

    try:
        ag = str(agente or "").strip().upper()
        if not _AGENTE_OK.match(ag):
            raise DestinoInvalido(f"agente invalido: {agente!r}")
    except DestinoInvalido as exc:
        return Destino("rechazo", rechazo="agente_invalido", motivo=str(exc))

    if rol not in ROLES_VALIDOS:
        # Un rol desconocido NO es "probablemente basic" ni "probablemente admin".
        # Es un dato que no entendemos y por eso no se entrega nada.
        return Destino("rechazo", rechazo="rol_desconocido",
                       motivo=f"role={rol!r} no esta en {sorted(ROLES_VALIDOS)}")

    if rol in ROLES_CANONICOS:
        return Destino("canonico", agente=ag,
                       motivo=f"role={rol} usa el asiento canonico (§9)")

    # --- a partir de aca, rol == basic ---
    if not asignados:
        return Destino("rechazo", rechazo="sin_agentes_asignados",
                       motivo="lista de agentes vacia -> 503, nunca canonico")

    if ag not in asignados:
        return Destino("rechazo", rechazo="agente_no_asignado",
                       motivo=f"{ag} no esta entre los asignados {asignados}")

    try:
        clave = instance_key(ag, user_id)
    except DestinoInvalido as exc:
        return Destino("rechazo", rechazo="identidad_invalida", motivo=str(exc))

    if clave not in sanas:
        # Incluye gate vencido, instancia caida y instancia que nunca arranco.
        # Los tres dan lo mismo A PROPOSITO: ninguno habilita hablar.
        return Destino("rechazo", rechazo="instancia_no_sana",
                       motivo=f"{clave} no figura entre las instancias sanas")

    return Destino("instancia", agente=ag, instance_key=clave,
                   motivo=f"role=basic con asignacion explicita -> {clave} (§9)")


def canal_dm(a: str, b: str) -> str:
    """Canal DM canonico, ordenado alfabeticamente: `dm:<a>:<b>`.

    Existe porque el bug de esta mañana fue exactamente este: un filtro miraba
    `dm:<agente>:%` y perdia dos tercios de los mensajes, los que tienen el
    nombre del agente del lado DERECHO del par.
    """
    x, y = sorted((str(a).strip().lower(), str(b).strip().lower()))
    if not x or not y or x == y:
        raise DestinoInvalido(f"par de canal invalido: {a!r}, {b!r}")
    return f"dm:{x}:{y}"


def participa_en_canal(canal: str, quien: str) -> bool:
    """True si `quien` es uno de los dos participantes del canal DM.

    §11.8: el clon solo ve DMs donde SU usuario participa. Se compara contra las
    dos posiciones del par, no contra un prefijo.
    """
    partes = str(canal or "").strip().lower().split(":")
    if len(partes) != 3 or partes[0] != "dm":
        return False
    return str(quien or "").strip().lower() in (partes[1], partes[2])


def canal_permitido_para_instancia(
    *,
    canal: str,
    username: str,
    agente: str,
    agentes_asignados: Sequence[str],
) -> tuple[bool, str]:
    """§11 brazo 8: ¿puede la instancia `agente`-de-`username` consumir `canal`?

    Devuelve `(permitido, motivo)`. El motivo se registra siempre, tambien cuando
    permite: un gate que solo explica sus NO deja sin auditar sus SI.

    TRES condiciones, y las tres son necesarias:

        1. es un canal DM            (`web_chat`, `user:3:...` NO son del clon)
        2. el USUARIO participa      no alcanza con que participe el agente
        3. el AGENTE participa Y esta asignado a ese usuario

    La 2 sola no alcanza y la 3 sola tampoco. `dm:ada:william` cumple 1 y 3 para
    una instancia de ADA, y es justo el canal que un clon de katy NO puede ver.
    """
    c = str(canal or "").strip().lower()
    partes = c.split(":")
    if len(partes) != 3 or partes[0] != "dm":
        return False, f"no es un canal DM: {canal!r}"

    quien = str(username or "").strip().lower()
    ag = str(agente or "").strip()
    if not quien or not _AGENTE_OK.match(ag.upper()):
        return False, f"identidad invalida: username={username!r} agente={agente!r}"

    participantes = (partes[1], partes[2])
    if quien not in participantes:
        return False, f"el usuario {quien!r} no participa en {c}"
    if ag.lower() not in participantes:
        return False, f"el agente {ag} no participa en {c}"

    asignados = [str(a).strip().upper() for a in (agentes_asignados or [])]
    if ag.upper() not in asignados:
        return False, f"{ag.upper()} no esta asignado a {quien!r} ({asignados})"

    return True, f"{ag.upper()} asignado y ambos participan en {c}"


# ── RESIDUOS DECLARADOS ────────────────────────────────────────────────────
#
# 1. Este modulo decide, no ejecuta. Quien lo llame tiene que HONRAR el rechazo:
#    un `Destino(tipo="rechazo")` ignorado deja el sistema igual que antes.
#    Ver test_routing_instancia.py, que prueba la decision -- NO que alguien la
#    obedezca. Ese segundo test se escribe cuando exista el consumidor.
#
# 2. `instancias_sanas` llega como parametro: este modulo NO consulta salud.
#    Si el que la calcula devuelve una lista optimista, esto rutea a una
#    instancia muerta. La salud es del gate de NEXUS (§11), no de aca.
#
# 3. NO cubre `user:<id>:<canal>` (los canales de trabajo tipo
#    `user:3:gtl-sistemas`). Hoy solo hay DMs en el alcance de la canaria.
RESIDUOS: tuple[str, ...] = (
    "decide pero no ejecuta: el que llama tiene que honrar el rechazo",
    "no calcula salud de instancia; la recibe ya resuelta",
    "no cubre canales user:<id>:<nombre>, solo DMs",
)
