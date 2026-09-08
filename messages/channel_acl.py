"""Quién puede escribir en qué canal. La capa que faltaba para el asiento sombra.

Medido el 2-sep-2026 sobre `soul_v3.chat_messages`: el servidor valida la FORMA
del canal (`_agent_channel_is_known`, fail-closed) pero NO existía un control de
«este remitente puede escribir en este canal». Un token válido de asiento sombra
podía publicar en `web_chat`, o sea delante de William.

Las otras dos capas del aislamiento no alcanzan, y también está medido:

    delivery_mode='isolated-clone'   5 policies RLS lo filtran, 17 no.
                                     12 mensajes de 134.679 lo llevan (0,009 %) y
                                     lo escribe UN relay puntual -> lo pone el
                                     publicador: convención, no frontera.
    channel propio                   aísla la LECTURA, no impide la ESCRITURA.

LA DIRECCIÓN DEL ERROR, que es la decisión de diseño:

Un asiento sombra queda contenido **por no estar en la lista**, no por que alguien
se acuerde de restringirlo. Si mañana se crea `ALICE-v2` y nadie toca este
archivo, ese asiento NO puede publicar en el canal general — el olvido produce
seguridad, no un agujero. Es el estándar que ya aplicamos a lo destructivo: la
operación peligrosa tiene que ser estructuralmente incapaz de apuntar a algo
crítico, y no depender del cuidado de quien la escribe.

Viable porque el universo es chico: 10 remitentes con actividad en 30 días
(JARVIS, FABLE, ADA, NEXUS, ALICE, William, henry, DUM, FAILOVER, william2).
Están todos, así que este control NO cambia nada de lo que hoy funciona.
"""
from __future__ import annotations

import pathlib
import re

# Remitentes con voz en los canales del equipo, medidos sobre 30 días de tráfico
# real. Se listan por NOMBRE y no por patrón: un patrón como `.*` volvería a
# dejar entrar a cualquiera, que es justo lo que este módulo evita.
REMITENTES_PLENOS: frozenset[str] = frozenset({
    "JARVIS", "FABLE", "ADA", "NEXUS", "ALICE", "DUM",
    "WILLIAM", "HENRY", "WILLIAM2",
    "FAILOVER",          # watchdog: publica partes de salud
})

# ── Cuerpo activo de ALICE: EXCLUSION MUTUA, no un permiso mas ───────────────
#
# William ordeno el 4-sep-2026 02:21 «pasala a v2 alice». El reflejo es agregar
# ALICE-V2 a la lista de arriba, y seria un ERROR: crearia un SEGUNDO writer con
# la misma identidad y desactivaria justo la propiedad que verificamos en 3b
# (273 publicaciones de v2, cero fuera de su corral). Lo planteo NEXUS y ADA lo
# fijo como exclusion mutua.
#
# La forma importa: no es «v2 tambien puede», es «cual de los dos cuerpos es EL
# cuerpo». Uno solo a la vez, y el otro cae al corral de sombra como cualquier
# desconocido -- sin ninguna linea que agregar ni sacar de REMITENTES_PLENOS.
#
# Por que un ARCHIVO y no una variable de entorno: el ACL lo consulta el chat
# server en cada publicacion; una variable exige reiniciar para volver atras, y
# el requisito de William es que se pueda deshacer. Un archivo se borra y el
# efecto es inmediato.
#
# POR QUE /etc/seal Y NO ~/.config/seal (JARVIS lo midio, 4-sep 02:50): el otro
# lector del interruptor es el broker de v2, que corre como `User=root` con
# `CapabilityBoundingSet=` VACIO. Sin CAP_DAC_READ_SEARCH, uid 0 no atraviesa un
# directorio 710 de `dadito`: le tocan los bits de "otros", que son `---`. O sea
# el interruptor lo leia UNA sola de las dos piezas y la otra quedaba ciega.
#
# Y no corresponde abrir el directorio de secretos para esto: el valor NO es un
# secreto --dice `ALICE` o `ALICE-V2`--, asi que vive en /etc/seal (root:root
# 755), traversable por ambos sin relajar ningun permiso.
_CUERPO_ALICE_PATH = pathlib.Path("/etc/seal/alice_cuerpo_activo")

# El nombre canonico y sus dos cuerpos posibles.
_ALICE_CANONICA = "ALICE"
_ALICE_CUERPOS: frozenset[str] = frozenset({"ALICE", "ALICE-V2"})


def cuerpo_activo_de_alice() -> str:
    """Cual de los dos cuerpos de ALICE tiene voz plena AHORA.

    Sin archivo -> "ALICE" (v1), que es el estado historico: el olvido conserva
    lo que ya funcionaba en vez de habilitar algo nuevo. Un valor ilegible o
    desconocido tambien cae a v1: fail-closed hacia lo conocido, nunca hacia el
    cuerpo en evaluacion.
    """
    try:
        crudo = _CUERPO_ALICE_PATH.read_text(encoding="utf-8").strip().upper()
    except OSError:
        return _ALICE_CANONICA
    return crudo if crudo in _ALICE_CUERPOS else _ALICE_CANONICA

# Un asiento en evaluación sólo habla en su propio canal de sombra.
_CANAL_SOMBRA = re.compile(r"^shadow:[a-z0-9][a-z0-9_.-]{0,63}$", re.IGNORECASE)


def es_canal_de_sombra(channel: str) -> bool:
    return bool(_CANAL_SOMBRA.fullmatch(str(channel or "").strip()))


_CANAL_USUARIO = re.compile(r"^user:\d+:([a-z0-9]+)(?:-([a-z0-9_.-]+))?$", re.IGNORECASE)
_HUMANOS: frozenset[str] = frozenset({"WILLIAM", "HENRY", "WILLIAM2"})


_AGENTES_CON_SALA: frozenset[str] = frozenset({"JARVIS", "FABLE", "ADA", "NEXUS", "ALICE", "DUM"})


def agente_de_canal_usuario(channel: str) -> str | None:
    """``user:<uid>:<agente>[-<cuerpo>]`` -> agente dueño de la sala (``user:1:ada-claude`` -> ``ADA``).

    None si no es sala o si el nombre NO es un agente: ``user:3:gtl-sistemas`` es la sala de un proyecto de
    Henry, no de un agente «GTL», y ahí los remitentes plenos siguen escribiendo como siempre (matriz de cero
    regresión de NEXUS, test_nexus_channel_acl_v1).
    """
    m = _CANAL_USUARIO.fullmatch(str(channel or "").strip())
    if not m:
        return None
    agente = m.group(1).upper()
    return agente if agente in _AGENTES_CON_SALA else None


def cuerpo_de_canal_usuario(channel: str) -> str | None:
    """``user:1:ada-claude`` -> ``ADA_CLAUDE``: el cuerpo EXCLUSIVO de la sala. None si la sala no nombra cuerpo."""
    m = _CANAL_USUARIO.fullmatch(str(channel or "").strip())
    if not m or not m.group(2) or agente_de_canal_usuario(channel) is None:
        return None
    return f"{m.group(1).upper()}_{m.group(2).upper().replace('-', '_')}"


def _es_cuerpo_del_agente(quien: str, agente: str) -> bool:
    """``ADA``, ``ADA_CLAUDE``, ``ADA-CODEX`` son cuerpos de ADA; ``NEXUS`` no."""
    q = str(quien or "").upper()
    return q == agente or q.startswith(agente + "_") or q.startswith(agente + "-")


def identidad_efectiva(sender: str, instance_id: str = "") -> str:
    """Quién es REALMENTE el que escribe, para efectos del ACL.

    Existe por un agujero que este módulo tenía y que se ve sólo mirando el gate
    de identidad de al lado: `clone_identity` permite, legítimamente, que la
    instancia `ALICE-u2` asevere `from: "ALICE"` — así hablan hoy los clones de
    usuario. Consecuencia medida el 3-sep:

        asiento sombra instanciado como ALICE-u2, aseverando from='ALICE'
          auth  -> PASA (clone_identity)
          ACL   -> ve sender='ALICE', remitente pleno -> web_chat PERMITIDO

    O sea: el asiento sombra evadía el ACL usando el nombre de su agente madre, y
    justo con la forma `X-uN` que la spec de F2 propone para instanciarlo.

    No se arregla en el auth —esa excepción es correcta para los clones— sino
    acá: **el ACL mira la INSTANCIA, no el nombre aseverado.** Si el que escribe
    declara una instancia, esa instancia es su identidad, y una instancia que
    nadie habilitó cae en el corral de sombra como cualquier desconocido.
    """
    inst = str(instance_id or "").strip()
    return inst.upper() if inst else str(sender or "").strip().upper()


def puede_escribir(sender: str, channel: str, instance_id: str = "") -> bool:
    """¿Este remitente puede publicar en este canal?

    Fail-closed para lo DESCONOCIDO y permisivo para lo conocido: los diez
    remitentes medidos conservan exactamente lo que tienen hoy (cero regresión),
    y cualquier identidad nueva —un asiento sombra, un clon, un token que no
    debería existir— queda encerrada en `shadow:*` sin que nadie lo configure.
    """
    quien = identidad_efectiva(sender, instance_id)
    if not quien:
        return False
    # Sala privada de una persona con UN agente (ADA, 8-sep-2026): en ``user:<uid>:<agente>`` escriben la
    # persona y ese agente (cualquiera de sus cuerpos). Los demás remitentes plenos NO, aunque sean de casa:
    # el 8-sep NEXUS y ALICE contestaban en ``user:1:ada-claude`` y William lo vio como su DM con ADA.
    agente_sala = agente_de_canal_usuario(channel)
    if agente_sala:
        if quien in _HUMANOS:
            return True
        cuerpo_sala = cuerpo_de_canal_usuario(channel)
        if cuerpo_sala:
            # Sala EXCLUSIVA de un cuerpo (William, 8-sep 13:24: «un canal exclusivo para vos, ADA Claude»):
            # escribe sólo ese cuerpo, declarado por instancia. ADA a secas (el puente Codex escribe así) NO.
            return quien == cuerpo_sala
        return _es_cuerpo_del_agente(quien, agente_sala)
    # Exclusion mutua entre los dos cuerpos de ALICE: exactamente UNO tiene voz
    # plena. El que no es el cuerpo activo queda en su corral aunque figure en
    # REMITENTES_PLENOS -- por eso este chequeo va ANTES y no despues.
    if quien in _ALICE_CUERPOS:
        return (
            quien == cuerpo_activo_de_alice()
            or es_canal_de_sombra(channel)
        )
    if quien in REMITENTES_PLENOS:
        return True
    # Desconocido: sólo su propio corral.
    return es_canal_de_sombra(channel)


def motivo_del_rechazo(sender: str, channel: str) -> str:
    """Texto para el 403. Dice qué pasó y qué se puede hacer, sin filtrar la lista."""
    return (
        f"remitente '{sender}' no habilitado para el canal '{channel}'. "
        "Un asiento en evaluación publica sólo en su canal 'shadow:<nombre>'."
    )
