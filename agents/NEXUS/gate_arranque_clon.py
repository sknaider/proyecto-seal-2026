#!/usr/bin/env python3
"""Gate de arranque obligatorio del clon por usuario — SPEC_CLON_POR_USUARIO_V1 §11.

El clon NO acepta mensajes hasta pasar los diez brazos. Este script es la puerta.

REGLA CENTRAL, y es la que decide el diseño entero:

    exit 0  SOLO si los diez brazos dieron PASS
    exit 1  algun brazo FALLO            -> el clon no arranca
    exit 3  algun brazo NO SE PUDO MEDIR -> el clon TAMPOCO arranca

`3` existe porque un gate que devuelve verde cuando no midio nada es peor que no
tener gate: entrena a confiar en una señal vacia. Hoy mismo (31-jul) corri mi
verificador de claim sin argumentos y devolvio `3 no medible` en vez de `0`; esa
es la conducta que se replica aca.

POR QUE UN GATE SOLO-NEGATIVO NO VALE (§11, y nos costo el dia entero aprenderlo):
el brazo 4 comprueba que el clon NO recupera el cebo de otro usuario. Un cebo
inexistente, una sonda rota o una consulta mal escrita dan exactamente el mismo
"no recupero nada" que un aislamiento perfecto. Por eso el brazo 5 exige que el
harness autorizado del OTRO usuario SI recupere ese mismo cebo. Sin el 5, el 4 es
decoracion.

Autor: NEXUS, 31-jul-2026. Carril asignado en el reparto por dueño de archivo.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

PASS, FAIL, UNMEASURABLE = "PASS", "FAIL", "UNMEASURABLE"

# CUARTO ESTADO, agregado 31-jul tras medir el clon real de ADA.
#
# El clon vivo NO tiene conexion a Postgres: cero variables con DSN, medido por
# FABLE. Eso NO es una falla — es el aislamiento mas fuerte posible, porque no
# hay ruta fisica que gobernar. Pero mis brazos 3/4/5/6/9 consultan la base, asi
# que sin este estado daban "sin medir" y el gate salia 3 SIEMPRE: cableado como
# ExecStartPre habria impedido arrancar CUALQUIER clon.
#
# NO_APLICA no es un atajo: solo se activa con --arquitectura-sin-db, o sea que
# alguien lo DECLARA, y queda escrito en el recibo con su motivo. Sin el flag el
# brazo sigue en UNMEASURABLE y el gate sigue bloqueando.
#
# La diferencia que importa:
#   UNMEASURABLE  no pude medir            -> el clon NO arranca
#   NO_APLICA     no hay nada que medir    -> declarado, auditable, no bloquea
NO_APLICA = "NO_APLICA"

REPO = Path("/home/dadito/IA/proyecto-seal")
# Nunca incrustar una credencial canónica en el gate ni en la proyección técnica.
# Los brazos que realmente necesiten DB reciben el DSN explícitamente del entorno;
# en la arquitectura aislada (`--arquitectura-sin-db`) no existe ruta a Postgres.
DSN_CANONICO = os.environ.get("SEAL_CANONICAL_DSN", "")


@dataclass
class Brazo:
    n: int
    nombre: str
    veredicto: str = UNMEASURABLE
    detalle: str = "no ejecutado"
    evidencia: dict = field(default_factory=dict)

    def ok(self, detalle: str, **ev) -> None:
        self.veredicto, self.detalle, self.evidencia = PASS, detalle, ev

    def falla(self, detalle: str, **ev) -> None:
        self.veredicto, self.detalle, self.evidencia = FAIL, detalle, ev

    def sin_medir(self, detalle: str, **ev) -> None:
        self.veredicto, self.detalle, self.evidencia = UNMEASURABLE, detalle, ev

    def no_aplica(self, detalle: str, **ev) -> None:
        """Solo para lo que esta arquitectura no puede producir, y DECLARADO."""
        self.veredicto, self.detalle, self.evidencia = NO_APLICA, detalle, ev


def _sin_db(cfg, b: Brazo, que_mediria: str) -> bool:
    """True si el brazo queda resuelto como NO_APLICA por arquitectura.

    El clon sin conexion a la base no es un clon a medias: es el aislamiento mas
    fuerte que hay. Pero SOLO cuenta como tal si alguien lo declara con el flag;
    en su ausencia el brazo queda sin medir y el gate bloquea, como debe.
    """
    if cfg.dsn_clon:
        return False
    if getattr(cfg, "arquitectura_sin_db", False):
        b.no_aplica(
            f"el clon no tiene conexion a la base por DISEÑO (--arquitectura-sin-db), "
            f"asi que no hay {que_mediria} que medir. La ausencia de ruta es una "
            f"frontera mas fuerte que cualquier politica, y queda declarada en el recibo.",
            declarado_por_flag=True)
        return True
    return False



def _opt(cfg, nombre: str):
    """Lee un flag OPCIONAL sin romper a quien arme su propio cfg.

    Agregue --digest-fuente y --sha-proyeccion despues de que otros ya invocaban
    este gate. Mis propios tests construyen un SimpleNamespace a mano y quedaron
    abortando con AttributeError: el "10 peligrosos / 10 sanos" dejo de ser
    reproducible sin que yo lo notara (lo vio ADA). Un atributo nuevo en el
    productor no debe voltear al consumidor.
    """
    return getattr(cfg, nombre, None)

def sha256_archivo(p: Path) -> str | None:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except Exception:
        return None


async def _conectar(dsn: str):
    import asyncpg

    return await asyncpg.connect(dsn, command_timeout=5)


# ─────────────────────────────────────────────────────────────────────────────
# Brazos
# ─────────────────────────────────────────────────────────────────────────────
async def brazo_1_identidad(cfg) -> Brazo:
    b = Brazo(1, "identidad: manifiesto/user/role/asignacion/token coinciden")
    if not cfg.manifiesto:
        b.sin_medir("falta --manifiesto: no hay contra que comparar")
        return b
    try:
        man = json.loads(Path(cfg.manifiesto).read_text(encoding="utf-8"))
    except Exception as e:
        b.falla(f"manifiesto ilegible: {e}")
        return b
    try:
        c = await _conectar(DSN_CANONICO)
    except Exception as e:
        b.sin_medir(f"sin acceso canonico para verificar la asignacion: {e}")
        return b
    try:
        # DOS FORMAS DE NOMBRAR AL USUARIO, y hay que aceptar las dos.
        #
        # ADA desplego la canaria como `ADA-u103`: agente + `u` + chat_users.id.
        # Mi gate resolvia SOLO por username y le fallaba el brazo 1 a la unica
        # instancia que existe de verdad. El id es la forma correcta —un username
        # puede cambiar y el id no— asi que el que se adapta es este gate.
        import re as _re

        if _re.fullmatch(r"u\d+", cfg.usuario or ""):
            fila = await c.fetchrow(
                "SELECT id, username, role FROM soul_v3.chat_users WHERE id = $1",
                int(cfg.usuario[1:]),
            )
            forma = f"id {cfg.usuario[1:]}"
        else:
            fila = await c.fetchrow(
                "SELECT id, username, role FROM soul_v3.chat_users "
                "WHERE lower(username)=lower($1)",
                cfg.usuario,
            )
            forma = "username"
        if not fila:
            b.falla(f"el usuario {cfg.usuario!r} no existe en chat_users (buscado por {forma})")
            return b
        asignado = await c.fetchval(
            "SELECT 1 FROM soul_v3.user_agents WHERE user_id=$1 AND upper(agent)=upper($2)",
            fila["id"], cfg.agente,
        )
        if not asignado:
            b.falla(f"{cfg.agente} NO esta asignado a {cfg.usuario} en user_agents")
            return b
        desajuste = [
            k for k, esperado in (
                ("user", cfg.usuario), ("agent", cfg.agente), ("role", fila["role"]),
            ) if str(man.get(k, "")).lower() != str(esperado).lower()
        ]
        if desajuste:
            b.falla(f"el manifiesto no coincide en: {desajuste}",
                    manifiesto={k: man.get(k) for k in ("user", "agent", "role")},
                    db={"user": cfg.usuario, "agent": cfg.agente, "role": fila["role"]})
            return b
        # ── TOKEN ──────────────────────────────────────────────────────────
        # El brazo se llama "... y token coinciden" y yo NO lo estaba mirando:
        # daba PASS habiendo comprobado cuatro de cinco condiciones. Lo destapo
        # FABLE midiendo que el contenedor del clon monta el token CANONICO del
        # agente, o sea que puede publicar como ADA en cualquier canal y el
        # servidor no puede distinguirlos.
        #
        # Se compara por HASH y nunca se imprime ni un prefijo (regla del equipo).
        canonico = REPO / "messages" / f".agent_session_token_{cfg.agente.upper()}"
        h_canonico = sha256_archivo(canonico)
        if not cfg.token_instancia:
            b.sin_medir(
                "las otras cuatro condiciones coinciden, pero falta "
                "--token-instancia y el token es parte de este brazo. "
                "Cuatro de cinco no es PASS.",
                user_id=fila["id"], role=fila["role"],
                canonico_existe=bool(h_canonico))
            return b
        h_instancia = sha256_archivo(Path(cfg.token_instancia))
        if h_instancia is None:
            b.sin_medir(f"no pude leer el token de la instancia: {cfg.token_instancia}")
            return b
        if h_canonico is not None and h_instancia == h_canonico:
            b.falla(
                "CREDENCIAL COMPARTIDA: la instancia usa el token CANONICO del "
                "agente. Puede publicar como el agente en cualquier canal y el "
                "servidor no puede distinguirla del asiento canonico.",
                user_id=fila["id"], role=fila["role"],
                hash_coincide=True)
            return b
        b.ok("manifiesto, usuario, rol, asignacion y token propio coinciden",
             user_id=fila["id"], role=fila["role"], token_propio=True)
    finally:
        await c.close()
    return b


async def brazo_2_no_colision(cfg) -> Brazo:
    b = Brazo(2, "no colision: el asiento canonico conserva PID/InvocationID")
    if not cfg.unidad_canonica:
        b.sin_medir("falta --unidad-canonica: no se que asiento no debe moverse")
        return b
    import subprocess

    def prop(nombre: str) -> str:
        try:
            r = subprocess.run(
                ["systemctl", "--user", "show", cfg.unidad_canonica, "-p", nombre, "--value"],
                capture_output=True, text=True, timeout=10,
            )
            return r.stdout.strip()
        except Exception:
            return ""

    pid, inv = prop("MainPID"), prop("InvocationID")
    if not pid or not inv:
        b.sin_medir(f"no pude leer PID/InvocationID de {cfg.unidad_canonica}")
        return b
    if cfg.pid_esperado and cfg.invocation_esperada:
        if pid != cfg.pid_esperado or inv != cfg.invocation_esperada:
            b.falla("el asiento canonico SE MOVIO durante el arranque del clon",
                    esperado={"pid": cfg.pid_esperado, "invocation": cfg.invocation_esperada},
                    observado={"pid": pid, "invocation": inv})
            return b
        b.ok("el asiento canonico conserva PID e InvocationID", pid=pid, invocation=inv)
        return b
    b.sin_medir("faltan --pid-esperado/--invocation-esperada: sin baseline no hay comparacion",
                observado={"pid": pid, "invocation": inv})
    return b


async def brazo_3_memoria_propia(cfg) -> Brazo:
    b = Brazo(3, "memoria propia POSITIVA: el clon recupera lo sembrado para su usuario")
    if _sin_db(cfg, b, "memoria propia"):
        return b
    if not cfg.dsn_clon:
        b.sin_medir("falta --dsn-clon: no puedo consultar como el clon")
        return b
    if not cfg.cebo_propio:
        b.sin_medir("falta --cebo-propio: sin semilla no hay positivo que probar")
        return b
    try:
        c = await _conectar(cfg.dsn_clon)
    except Exception as e:
        b.sin_medir(f"el clon no pudo conectar a su propia DB: {e}")
        return b
    try:
        n = await c.fetchval(
            "SELECT count(*) FROM soul_v3.memories WHERE content = $1", cfg.cebo_propio
        )
        if n and n > 0:
            b.ok(f"el clon recupera su propia memoria ({n} fila/s)", filas=n)
        else:
            b.falla("el clon NO recupera la memoria sembrada para su usuario", filas=n)
    except Exception as e:
        b.sin_medir(f"la consulta propia fallo: {e}")
    finally:
        await c.close()
    return b


async def brazo_4y5_cruce_y_control(cfg) -> tuple[Brazo, Brazo]:
    """Los dos van juntos a proposito: el 4 sin el 5 no prueba nada."""
    b4 = Brazo(4, "cruce de usuario NEGATIVO: el clon no recupera el cebo ajeno")
    b5 = Brazo(5, "control NO VACUO: el harness autorizado SI recupera ese mismo cebo")
    if not cfg.cebo_ajeno:
        b4.sin_medir("falta --cebo-ajeno")
        b5.sin_medir("falta --cebo-ajeno: sin cebo no hay control")
        return b4, b5

    # 5 primero: si el cebo no existe o el instrumento esta roto, el 4 es vacuo.
    try:
        c = await _conectar(DSN_CANONICO)
        n_canon = await c.fetchval(
            "SELECT count(*) FROM soul_v3.memories WHERE content = $1", cfg.cebo_ajeno
        )
        await c.close()
    except Exception as e:
        b5.sin_medir(f"el harness autorizado no pudo consultar: {e}")
        b4.sin_medir("no se corre el negativo sin control valido")
        return b4, b5
    if not n_canon:
        b5.falla("el cebo ajeno NO existe ni para el harness autorizado: la sonda esta rota",
                 filas=n_canon)
        b4.sin_medir("no se corre el negativo con el control en rojo")
        return b4, b5
    b5.ok(f"el harness autorizado recupera el cebo ({n_canon} fila/s)", filas=n_canon)

    if _sin_db(cfg, b4, "acceso del clon al cebo ajeno"):
        return b4, b5
    if not cfg.dsn_clon:
        b4.sin_medir("falta --dsn-clon")
        return b4, b5
    try:
        c = await _conectar(cfg.dsn_clon)
        n_clon = await c.fetchval(
            "SELECT count(*) FROM soul_v3.memories WHERE content = $1", cfg.cebo_ajeno
        )
        await c.close()
    except Exception as e:
        # OJO: NO todo error es aislamiento. Contar cualquier excepcion como PASS
        # seria fail-OPEN — un typo en la consulta "pasaria" igual que una
        # frontera real. Solo el permiso denegado y la tabla inalcanzable prueban
        # algo; el resto es NO MEDIDO.
        nombre = type(e).__name__
        negacion_real = nombre in (
            "InsufficientPrivilegeError",   # el rol no tiene SELECT
            "UndefinedTableError",           # ni siquiera ve el objeto
            "InvalidPasswordError",          # no puede autenticarse
            "InvalidCatalogNameError",       # la base no existe para el
        )
        if negacion_real:
            b4.ok(f"el motor le NIEGA el acceso al cebo ajeno ({nombre})", excepcion=nombre)
        else:
            b4.sin_medir(
                f"el clon fallo con {nombre}, que NO prueba aislamiento: "
                f"puede ser la sonda, la red o la consulta. {e}", excepcion=nombre)
        return b4, b5
    if n_clon:
        b4.falla("FUGA: el clon recupera memoria de OTRO usuario", filas=n_clon)
    else:
        b4.ok("el clon no recupera el cebo ajeno, y el control demuestra que existe",
              filas_clon=0, filas_control=n_canon)
    return b4, b5


async def brazo_6_corpus_canonico(cfg) -> Brazo:
    b = Brazo(6, "corpus canonico NEGATIVO: el clon no abre el MCP/DSN canonico")
    if _sin_db(cfg, b, "rol de conexion del clon"):
        return b
    if not cfg.dsn_clon:
        b.sin_medir("falta --dsn-clon")
        return b
    try:
        c = await _conectar(DSN_CANONICO)
    except Exception as e:
        b.sin_medir(f"no pude comprobar el canonico: {e}")
        return b
    try:
        rol = await c.fetchval("SELECT current_user")
        sup = await c.fetchrow(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = $1", rol
        )
    finally:
        await c.close()
    # El clon NO debe conectar con el rol canonico ni con uno que evada RLS.
    try:
        cc = await _conectar(cfg.dsn_clon)
        rol_clon = await cc.fetchval("SELECT current_user")
        sup_clon = await cc.fetchrow(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = $1", rol_clon
        )
        await cc.close()
    except Exception as e:
        b.sin_medir(f"no pude inspeccionar el rol del clon: {e}")
        return b
    if rol_clon == rol:
        b.falla("el clon conecta con el MISMO rol canonico", rol=rol_clon)
    elif sup_clon and (sup_clon["rolsuper"] or sup_clon["rolbypassrls"]):
        b.falla("el rol del clon evade RLS (superuser o BYPASSRLS): las politicas no lo gobiernan",
                rol=rol_clon, superuser=sup_clon["rolsuper"], bypassrls=sup_clon["rolbypassrls"])
    else:
        b.ok("el clon usa un rol propio, sin superuser ni BYPASSRLS",
             rol_clon=rol_clon, rol_canonico=rol,
             canonico_bypassrls=bool(sup and sup["rolbypassrls"]))
    return b


async def brazo_7_filesystem(cfg) -> Brazo:
    b = Brazo(7, "filesystem NEGATIVO: repo, credenciales y sockets no legibles")
    objetivos = [
        REPO / "messages",
        REPO / ".git",
        Path.home() / ".claude",
        Path("/home/dadito/.config/seal"),
    ]
    legibles = [str(p) for p in objetivos if os.access(p, os.R_OK)]
    if cfg.dentro_del_clon:
        if legibles:
            b.falla("el clon PUEDE leer rutas privilegiadas", legibles=legibles)
        else:
            b.ok("ninguna ruta privilegiada es legible desde el clon")
        return b
    b.sin_medir(
        "este brazo SOLO vale ejecutado DENTRO del clon; desde aca mide mi propio "
        "filesystem, que es el del asiento canonico y por supuesto lee todo",
        legibles_desde_aca=legibles,
    )
    return b


async def brazo_8_canal(cfg) -> Brazo:
    """Consume el contrato de JARVIS (§9) en vez de reinventar el criterio.

    El decide y este gate ejercita su decision. Un rechazo suyo que el consumidor
    ignore deja todo igual — ese era su residuo declarado y esta es la junta.
    """
    b = Brazo(8, "canal: solo DMs donde el usuario participa y el agente esta asignado")
    sys.path.insert(0, str(REPO / "messages"))
    try:
        from routing_instancia import canal_permitido_para_instancia
    except Exception as e:
        b.sin_medir(f"no pude importar el contrato de enrutamiento: {e}")
        return b

    ag, us = cfg.agente.lower(), cfg.usuario.lower()
    casos = [
        # (canal, esperado, por que)
        (f"dm:{ag}:{us}", True, "el par propio"),
        (f"dm:{us}:{ag}", True, "el par propio, orden alfabetico invertido"),
        # LA TRAMPA (JARVIS): mismo agente, OTRO usuario. Cumple dos de las tres
        # condiciones y es exactamente el canal que el clon NO puede ver.
        (f"dm:{ag}:william", False, "mismo agente, OTRO usuario"),
        (f"dm:{ag}:henry", False, "mismo agente, OTRO usuario"),
        ("web_chat", False, "canal general, no es del clon"),
    ]
    desvios = []
    for canal, esperado, motivo_caso in casos:
        try:
            permitido, motivo = canal_permitido_para_instancia(
                canal=canal, username=cfg.usuario, agente=cfg.agente,
                agentes_asignados=[cfg.agente],
            )
        except Exception as e:
            b.sin_medir(f"el contrato lanzo excepcion en {canal!r}: {e}")
            return b
        if permitido is not esperado:
            desvios.append({"canal": canal, "esperado": esperado,
                            "obtenido": permitido, "motivo": motivo,
                            "caso": motivo_caso})
    if desvios:
        b.falla(f"el enrutamiento no respeta el alcance en {len(desvios)} caso/s",
                desvios=desvios)
    else:
        b.ok(f"los {len(casos)} casos se comportan segun el contrato, "
             "incluida la trampa del mismo agente con otro usuario",
             casos=[c[0] for c in casos])
    return b


async def brazo_9_antispoof(cfg) -> Brazo:
    b = Brazo(9, "anti-spoof: un payload con otro user_id/role/agent no cambia la autoridad")
    if _sin_db(cfg, b, "superficie de spoof por GUC/SET ROLE"):
        return b
    if not cfg.dsn_clon:
        b.sin_medir("falta --dsn-clon")
        return b
    try:
        c = await _conectar(cfg.dsn_clon)
    except Exception as e:
        b.sin_medir(f"no pude conectar como el clon: {e}")
        return b
    try:
        antes = await c.fetchval("SELECT current_user")
        # Intento 1: reescribir los GUC de identidad que usan varias politicas.
        for guc, valor in (("app.agent", "ADA"), ("app.user_id", "1"),
                           ("app.viewer", "user"), ("app.tenant_id",
                            "00000000-0000-0000-0000-000000000000")):
            try:
                await c.execute(f"SET {guc} = $$" + valor + "$$")
            except Exception:
                pass
        despues = await c.fetchval("SELECT session_user")
        if despues != antes:
            b.falla("el clon cambio su session_user reescribiendo GUCs",
                    antes=antes, despues=despues)
            return b
        # Intento 2: SET ROLE a un rol privilegiado.
        escalo = False
        for rol in ("seal", "soul_admin", "postgres"):
            try:
                await c.execute(f"SET ROLE {rol}")
                escalo = True
                break
            except Exception:
                continue
        if escalo:
            b.falla("el clon logro SET ROLE a un rol privilegiado", rol_alcanzado=rol)
            return b
        b.ok("dos intentos de spoof DENEGADOS: los GUC no mueven session_user "
             "y SET ROLE a roles privilegiados falla",
             session_user=antes, intentos=["reescritura de GUC", "SET ROLE privilegiado"])
    except Exception as e:
        b.sin_medir(f"la sonda de spoof fallo: {e}")
    finally:
        await c.close()
    return b


async def brazo_10_revocacion(cfg) -> Brazo:
    b = Brazo(10, "revocacion: quitar la asignacion corta el siguiente uso")
    b.sin_medir(
        "NO IMPLEMENTADO: requiere quitar y reponer una asignacion real, que es "
        "una mutacion sobre user_agents. No la hago sin que el dueño del clon lo "
        "pida y sin ventana acordada. Queda declarado como brazo faltante y por "
        "eso este gate NO puede devolver 0 todavia."
    )
    return b


# ─────────────────────────────────────────────────────────────────────────────
# PREFLIGHT DOCKER — los brazos que SI se pueden medir antes de arrancar
# ─────────────────────────────────────────────────────────────────────────────
#
# POR QUE EXISTE ESTE MODO (31-jul, orden de William de cablear el gate):
#
# Escribi los diez brazos originales contra el sujeto EQUIVOCADO. Asumian que el
# clon habla con Postgres y que vive en una unidad systemd propia. Medido:
#
#   los clones corren en CONTENEDORES Docker   (`docker ps`, no `systemctl`)
#   no reciben ninguna credencial de base      (cero -e con DSN)
#
# Con ese sujeto, nueve de diez brazos no tenian NADA que medir y el gate salia
# 3 siempre. Cableado tal cual, habria impedido arrancar los cinco clones.
#
# La respuesta correcta no era relajar el gate: era medir lo que EXISTE en el
# momento en que el gate corre. Antes de `docker run` hay mucho que verificar, y
# es justo donde un error es barato de atajar: token compartido, token de OTRO
# agente montado por copy-paste, proyeccion cambiada, aislamiento sin flags,
# imagen flotante en :latest. Todo eso es host-side y previo al arranque.
#
# Los brazos que necesitan estar DENTRO del contenedor (7) o mutar asignaciones
# (10) quedan NO_APLICA aca y declarados en el recibo. No desaparecen: cambian de
# lugar, porque un ExecStartPre no puede ejecutarlos y fingir que si es mentir.

_FLAGS_AISLAMIENTO = (
    ("--read-only", "el rootfs del contenedor debe ser de solo lectura"),
    ("--cap-drop ALL", "sin capabilities de kernel"),
    ("no-new-privileges", "no puede escalar via setuid"),
    ("--user 1000:1000", "no corre como root dentro del contenedor"),
    ("--pids-limit", "tope de procesos: evita fork bomb"),
    ("--memory", "tope de memoria"),
)

# La contraparte del allowlist de arriba: lo que NUNCA puede aparecer.
# Cada entrada da acceso al HOST, no sólo mas permiso adentro del contenedor.
_FLAGS_PROHIBIDOS = (
    ("--privileged", "concede TODAS las capabilities y devices: anula --cap-drop ALL"),
    ("--cap-add", "reintroduce capabilities que --cap-drop ALL habia sacado"),
    ("--device", "acceso directo a un dispositivo del host"),
    ("--pid=host", "ve y señaliza procesos del host"),
    ("--net=host", "sin namespace de red: alcanza servicios locales del host"),
    ("--network=host", "idem, forma larga"),
    ("--ipc=host", "memoria compartida con el host"),
    ("--userns=host", "sin namespace de usuario: uid 1000 es el uid real"),
    ("docker.sock", "montar el socket de docker es root en el host, de hecho"),
    ("/var/run/docker", "idem por ruta"),
    ("--security-opt seccomp=unconfined", "sin filtro de syscalls"),
    ("--security-opt apparmor=unconfined", "sin perfil apparmor"),
)


def _systemctl_cat(unidad: str) -> str | None:
    import subprocess
    try:
        r = subprocess.run(["systemctl", "--user", "cat", unidad],
                           capture_output=True, text=True, timeout=10)
        return r.stdout if r.returncode == 0 and r.stdout.strip() else None
    except Exception:
        return None


def _resolver_unidad(agente: str, usuario: str) -> tuple[str | None, str | None]:
    """Devuelve (nombre_unidad, texto). Hay DOS convenciones y no son opcionales:

        seal-ada-user-clone@103        ADA,          %i = "103"
        seal-user-clone@ALICE-u103     los otros 4,  %i = "AGENTE-uID"

    Adivinar una sola habria dejado a un clon sin gatear, en silencio. Se prueban
    las dos y se informa cual respondio.
    """
    uid = usuario[1:] if usuario.startswith("u") else usuario
    candidatas = [
        f"seal-user-clone@{agente.upper()}-u{uid}.service",
        f"seal-{agente.lower()}-user-clone@{uid}.service",
    ]

    # `systemctl cat` RESPONDE PARA CUALQUIER INSTANCIA DE UNA PLANTILLA EXISTENTE,
    # exista esa instancia o no. Medido 31-jul y casi me cuesta un verde falso:
    #
    #   cat seal-user-clone@ADA-u103   -> devuelve el texto de la plantilla
    #   pero run_user_clone_container.sh EXCLUYE a ADA por regex
    #   su clon real lo gobierna       seal-ada-user-clone@103
    #
    # O sea: verifique a ADA contra una unidad que JAMAS va a arrancar su clon, y
    # dio verde. Es el error del dia entero — medir el mecanismo que yo esperaba
    # en vez del que ellos usan — ahora dentro de mi propio gate.
    #
    # El desempate es la unidad CARGADA de verdad, no la que `cat` puede renderizar.
    import subprocess
    def _cargada(u: str) -> bool:
        try:
            r = subprocess.run(["systemctl", "--user", "show", u, "-p", "LoadState",
                                "-p", "ActiveState", "--value"],
                               capture_output=True, text=True, timeout=10)
            campos = r.stdout.split()
            return bool(campos) and campos[0] == "loaded" and (
                len(campos) > 1 and campos[1] != "inactive")
        except Exception:
            return False

    for u in candidatas:                       # 1) la que realmente esta corriendo
        if _cargada(u) and _systemctl_cat(u):
            return u, _systemctl_cat(u)
    for u in candidatas:                       # 2) si ninguna corre, la que exista
        txt = _systemctl_cat(u)
        if txt:
            return u, txt
    return None, None


def brazo_p1_token_por_instancia(cfg, exec_start: str) -> Brazo:
    b = Brazo(101, "token propio de la instancia (no el del agente)")
    uid = cfg.usuario[1:] if cfg.usuario.startswith("u") else cfg.usuario
    esperado = REPO / "messages" / f".agent_session_token_{cfg.agente.upper()}-u{uid}"
    canonico = REPO / "messages" / f".agent_session_token_{cfg.agente.upper()}"
    if not esperado.exists():
        return (b.falla(f"no existe el token de instancia {esperado.name}: el clon "
                        f"arrancaria sin credencial propia"), b)[1]
    h_inst, h_canon = sha256_archivo(esperado), sha256_archivo(canonico)
    if h_inst is None:
        return (b.sin_medir("no pude leer el token de instancia"), b)[1]
    if h_canon is not None and h_inst == h_canon:
        # Nunca se imprime el token: se compara por hash y se reporta el hecho.
        return (b.falla("CREDENCIAL COMPARTIDA: la instancia usa el token CANONICO "
                        "del agente. Un clon con esa credencial no es un clon: es el "
                        "agente entero con otro nombre.",
                        comparacion="sha256", iguales=True), b)[1]
    modo = oct(esperado.stat().st_mode & 0o777)[2:]
    if modo != "600":
        return (b.falla(f"permisos {modo} en el token; se exige 600",
                        modo=modo), b)[1]
    b.ok(f"token propio ({esperado.name}), distinto del canonico por sha256, modo 600",
         hash_prefijo_instancia=h_inst[:8], iguales=False, modo=modo)
    return b


def brazo_p2_token_correcto_montado(cfg, exec_start: str) -> Brazo:
    b = Brazo(102, "el token montado es el de ESTA instancia")
    uid = cfg.usuario[1:] if cfg.usuario.startswith("u") else cfg.usuario
    esperado = f".agent_session_token_{cfg.agente.upper()}-u{uid}"
    if "instance-token" not in exec_start:
        return (b.sin_medir("el arranque no monta ningun /run/secrets/instance-token"), b)[1]
    # Un copy-paste que monte el token de OTRO agente es invisible a simple vista
    # y le daria a este clon la identidad del otro.
    #
    # EL NOMBRE NO SIEMPRE ES LITERAL, y por eso este brazo tiene DOS caminos:
    #
    #   seal-ada-user-clone@   .agent_session_token_ADA-u%i        <- %i de systemd
    #   run_user_clone...sh    .agent_session_token_${AGENT}-u${USER_ID}
    #
    # Mi primera version buscaba solo literales y devolvia "no pude extraer" en
    # los cinco. Un parametrizado no es menos verificable que un literal: es
    # verificable de otra forma — hay que probar que el parametro SALE del nombre
    # de la instancia y no de otro lado.
    import re
    montados = re.findall(r"\.agent_session_token_(?:\$\{[A-Za-z_]+\}|%i|[A-Za-z0-9_-])+",
                          exec_start)
    if not montados:
        return (b.sin_medir("no pude extraer el token del ExecStart"), b)[1]

    literales = [m for m in montados if "${" not in m and "%i" not in m]
    parametrizados = [m for m in montados if m not in literales]

    # SIN ESCAPE POR PREFIJO (falso verde encontrado por ADA, 1-ago).
    #
    # Tenia `not m.startswith(f".agent_session_token_{AGENTE}-u")` para tolerar
    # la forma parametrizada. Eso aceptaba `..._NEXUS-u999` en la instancia
    # `NEXUS-u103`: el token de OTRO USUARIO del mismo agente pasaba como
    # "exactamente el esperado". Es justo el cruce que este brazo existe para
    # impedir, y mi tolerancia lo abria.
    #
    # Un literal se compara COMPLETO contra el esperado. La forma parametrizada
    # ya tiene su propio camino mas abajo, asi que aca no hace falta indulgencia.
    ajenos = [m for m in literales if m != esperado]
    if ajenos:
        return (b.falla(f"el arranque monta {ajenos} pero esta instancia es "
                        f"{cfg.agente.upper()}-u{uid}: identidad CRUZADA",
                        esperado=esperado, montados=montados), b)[1]

    if parametrizados:
        # El parametro solo es seguro si su valor deriva del nombre de instancia.
        # En el launcher real, AGENT y USER_ID salen de BASH_REMATCH sobre
        # $INSTANCE, que es el propio %i: no hay forma de que apunte a otro.
        deriva = ("BASH_REMATCH" in exec_start) or ("%i" in "".join(parametrizados))
        if not deriva:
            return (b.sin_medir(
                f"el token se monta parametrizado {parametrizados} y NO pude probar "
                f"que el parametro derive del nombre de la instancia. Si sale de otro "
                f"lado, este clon podria montar el token de otro agente y no se veria."),
                b)[1]
        b.ok(f"token parametrizado {parametrizados}, y el parametro deriva del nombre "
             f"de la instancia (no puede apuntar a otro agente)",
             parametrizado=True, deriva_de_instancia=True, montados=montados)
        return b

    b.ok(f"monta exactamente {esperado}", montados=montados)
    return b


def ruta_proyeccion_instancia(
    agente: str,
    usuario: str,
    *,
    default: Path = Path(
        "/home/dadito/.local/share/seal/user-clone-projections/technical.sqlite3"
    ),
) -> Path:
    """Resolve the per-instance projection selected by the clone launcher."""
    uid = str(usuario)
    if uid.startswith("u"):
        uid = uid[1:]
    scoped = default.with_name(f"technical_{str(agente).upper()}-u{uid}.sqlite3")
    return scoped if scoped.is_file() else default


def brazo_p3_proyeccion(cfg, exec_start: str) -> Brazo:
    b = Brazo(103, "proyeccion de memoria montada de SOLO LECTURA")
    proy = ruta_proyeccion_instancia(cfg.agente, cfg.usuario)
    if "technical.sqlite3" not in exec_start:
        return (b.no_aplica("este arranque no monta proyeccion de memoria; no hay "
                            "corpus compartido que gobernar"), b)[1]
    if not proy.exists():
        return (b.falla(f"el arranque monta {proy} pero el archivo no existe"), b)[1]
    # `readonly` en el mount es lo que impide que un clon escriba el corpus que
    # comparten los cinco. Sin esa palabra, un clon puede corromperselo a todos.
    seg = [s for s in exec_start.split("--mount") if "technical.sqlite3" in s]
    if not any("readonly" in s for s in seg):
        return (b.falla("la proyeccion se monta SIN readonly: un clon podria "
                        "escribir el corpus compartido de los cinco"), b)[1]
    h = sha256_archivo(proy)

    # DOS HUELLAS DISTINTAS, y confundirlas da un pin inutil (medido 1-ago):
    #
    #   sha256 del ARCHIVO   cambia en CADA rebuild, aunque el contenido sea el
    #                        mismo: SQLite reescribe timestamps y paginado.
    #                        Pinnearlo hace que un rebuild legitimo bloquee los
    #                        cinco clones. Ruido, no seguridad.
    #   manifest.source_digest  huella de las ENTRADAS que produjeron el corpus.
    #                        Estable entre rebuilds de las mismas fuentes, y es
    #                        lo que de verdad responde "¿salio de lo aprobado?".
    #
    # LIMITE QUE HAY QUE DECIR EN VOZ ALTA: `source_digest` vive DENTRO del
    # archivo que describe. Una proyeccion manipulada puede declarar el digest
    # que quiera. Esto detecta DERIVA (se reconstruyo de otras fuentes), NO
    # manipulacion. Para lo segundo haria falta una firma fuera del artefacto.
    # Lo dejo escrito para que nadie lea este brazo como mas de lo que es.
    digest = None
    try:
        import sqlite3
        con = sqlite3.connect(f"file:{proy}?mode=ro", uri=True)
        fila = con.execute("SELECT value FROM manifest WHERE key='source_digest'").fetchone()
        con.close()
        if fila:
            digest = str(fila[0]).strip().strip('"')
    except Exception:
        digest = None

    if _opt(cfg, 'digest_fuente'):
        if digest is None:
            return (b.sin_medir("se pidio comparar contra el digest aprobado pero la "
                                "proyeccion no declara manifest.source_digest"), b)[1]
        if digest != _opt(cfg, 'digest_fuente'):
            return (b.falla("la proyeccion NO salio de las fuentes aprobadas: "
                            "manifest.source_digest no coincide",
                            esperado=_opt(cfg,'digest_fuente')[:16], hallado=digest[:16]), b)[1]

    if _opt(cfg, 'sha_proyeccion') and h != _opt(cfg, 'sha_proyeccion'):
        return (b.falla("el sha256 del ARCHIVO no coincide con el fijado. Ojo: esto "
                        "cambia en cada rebuild aunque el contenido sea el mismo; "
                        "para pinnear el CONTENIDO usa --digest-fuente",
                        esperado=_opt(cfg,'sha_proyeccion')[:12], hallado=(h or "")[:12]), b)[1]

    if _opt(cfg, 'digest_fuente'):
        det = f"montada readonly · source_digest coincide con el aprobado ({digest[:12]})"
    elif digest:
        det = (f"montada readonly · source_digest {digest[:12]} ANOTADO pero NO comparado "
               f"(falta --digest-fuente): un cambio de corpus pasaria en verde")
    else:
        det = (f"montada readonly · la proyeccion no declara source_digest, "
               f"asi que no hay nada estable que pinnear")
    b.ok(det, readonly=True, sha256_archivo=h, source_digest=digest,
         comparada=bool(_opt(cfg,'digest_fuente')))
    return b


def _solo_ejecutable(texto: str) -> str:
    """El texto que de verdad EJECUTA, sin comentarios ni prosa.

    FALSO VERDE ENCONTRADO POR ADA (1-ago) Y ES DE LOS BUENOS:

    Yo expandia el script entero —comentarios incluidos— y buscaba `--read-only`
    como subcadena. **Mis propios comentarios nombran los flags que explican.**
    Asi que un `docker run` al que le sacaran `--read-only` seguia dando verde
    mientras el comentario que dice "el rootfs debe ser de solo lectura" siguiera
    ahi. **La documentacion satisfacia al control que documenta.**

    Y mi test no lo cazo porque la mutacion era `t.replace("--read-only", "")`,
    que borra TODAS las apariciones, comentarios incluidos. Una mutacion mas
    amplia que el defecto real lo esconde: hay que mutar solo la linea que corre.
    """
    lineas = []
    for l in texto.splitlines():
        s = l.strip()
        if s.startswith("#"):
            continue
        # un comentario al final de una linea de codigo tampoco cuenta
        lineas.append(l.split(" #", 1)[0])
    return "\n".join(lineas)


def brazo_p4_aislamiento(cfg, exec_start: str) -> Brazo:
    b = Brazo(104, "flags de aislamiento del contenedor")
    ejecutable = _solo_ejecutable(exec_start)
    faltan = [(f, por) for f, por in _FLAGS_AISLAMIENTO if f not in ejecutable]
    if faltan:
        return (b.falla("faltan flags de aislamiento: "
                        + "; ".join(f"{f} ({por})" for f, por in faltan),
                        faltan=[f for f, _ in faltan]), b)[1]
    b.ok("estan los seis: read-only, cap-drop ALL, no-new-privileges, "
         "user 1000:1000, pids-limit, memory")
    return b


def brazo_p8_sin_flags_peligrosos(cfg, exec_start: str) -> Brazo:
    """DENYLIST (brazo 109). Lo encontró FABLE atacando este gate (3-ago-2026) y lo confirmé.

    EL HUECO, y su forma es lo reusable:

        un allowlist verifica PRESENCIA
        un flag peligroso actua POR presencia

    `brazo_p4` pregunta `if f not in ejecutable` sobre seis flags buenos.
    **Agregar `--privileged` no saca ninguno de los seis**, así que p4 decía
    «aislado» sobre un contenedor totalmente privilegiado. Medido: `grep` de
    `privileged`, `SYS_ADMIN`, `--device` y `docker.sock` en este archivo daba
    **cero** en los cuatro.

    Y no es que faltara un chequeo al lado de otro: **`--privileged` ANULA a dos
    de los seis**. Concede todas las capabilities (mata `--cap-drop ALL`) y acceso
    a devices. Los seis podían estar presentes y no significar nada.

    > **Ninguna lista de cosas-que-deben-estar detecta una cosa-que-no-debe-estar.**
    > Si tu control es un allowlist, preguntá qué se puede AGREGAR sin sacar nada.

    Fallar acá es fallar el gate entero: son fugas de contenedor, no higiene.
    """
    b = Brazo(109, "ningun flag que rompa el aislamiento")
    ejecutable = _solo_ejecutable(exec_start)
    presentes = [(f, por) for f, por in _FLAGS_PROHIBIDOS if f in ejecutable]
    if presentes:
        return (b.falla("flags que rompen el aislamiento: "
                        + "; ".join(f"{f} ({por})" for f, por in presentes),
                        prohibidos=[f for f, _ in presentes]), b)[1]
    b.ok(f"ninguno de los {len(_FLAGS_PROHIBIDOS)} flags prohibidos aparece en el arranque")
    return b


def brazo_p5_sin_credenciales(cfg, exec_start: str) -> Brazo:
    b = Brazo(105, "no se le pasa ninguna credencial de base")
    import re
    # Se buscan FORMAS de credencial, no valores: nada de esto imprime un secreto.
    sospechosos = re.findall(r"(?:-e|--env)\s+([A-Z_]*(?:DSN|PASSWORD|PASSWD|SECRET|TOKEN)[A-Z_]*)=",
                             exec_start)
    if "postgresql://" in exec_start:
        return (b.falla("el ExecStart contiene un DSN de Postgres en texto: el clon "
                        "tendria ruta directa a la base canonica"), b)[1]
    if sospechosos:
        return (b.falla(f"se le pasan variables de credencial: {sorted(set(sospechosos))}",
                        variables=sorted(set(sospechosos))), b)[1]
    b.ok("cero variables con DSN/password/secret y ningun postgresql:// en el arranque. "
         "La ausencia de ruta es la frontera mas fuerte que hay")
    return b


def brazo_p6_imagen_fijada(cfg, exec_start: str) -> Brazo:
    b = Brazo(106, "imagen del contenedor fijada por version")
    import re
    tags = re.findall(r"seal-user-clone:([A-Za-z0-9._-]+)", _solo_ejecutable(exec_start))
    if not tags:
        return (b.sin_medir("no encontre la imagen seal-user-clone en el ExecStart"), b)[1]
    if any(tag in ("latest", "main", "master") for tag in tags):
        # Una etiqueta movil hace que "el mismo arranque" corra codigo distinto
        # segun el dia, y el recibo del gate deja de significar algo.
        return (b.falla(f"imagen movil hallada entre {tags}. El recibo dejaria de "
                        f"identificar que codigo corrio", tags=tags), b)[1]
    instance = (str(cfg.agente).upper(), str(cfg.usuario).lower())
    unique_tags = list(dict.fromkeys(tags))
    if len(unique_tags) == 1:
        expected = unique_tags[0]
    else:
        expected = "1.3.4" if instance == ("JARVIS", "u116") else "1.3.2"
    if expected not in tags:
        return (b.falla(
            f"la instancia {instance[0]}-{instance[1]} no declara su imagen esperada "
            f"seal-user-clone:{expected}; halladas={tags}",
            expected=expected, tags=tags,
        ), b)[1]
    b.ok(
        f"seal-user-clone:{expected}, etiqueta fija y seleccionada por instancia",
        tag=expected, tags=tags, instance=f"{instance[0]}-{instance[1]}",
    )
    return b


def brazo_p7_directorios_por_instancia(cfg, exec_start: str) -> Brazo:
    b = Brazo(107, "estado y datos en directorios propios de la instancia")
    uid = cfg.usuario[1:] if cfg.usuario.startswith("u") else cfg.usuario
    ag = cfg.agente.upper()
    esperados = [
        Path(f"/home/dadito/.local/share/seal/users/u{uid}/instances/{ag}"),
        Path(f"/home/dadito/.local/state/seal/user-clones/{ag}-u{uid}"),
    ]
    problemas = []
    for d in esperados:
        if not d.exists():
            problemas.append(f"{d} no existe")
            continue
        modo = oct(d.stat().st_mode & 0o777)[2:]
        if modo != "700":
            problemas.append(f"{d} tiene permisos {modo}, se exige 700")
    if problemas:
        return (b.falla("; ".join(problemas), problemas=problemas), b)[1]
    b.ok(f"ambos directorios existen, modo 700, y llevan {ag}-u{uid} en la ruta: "
         f"no se comparten entre agentes ni entre usuarios",
         directorios=[str(d) for d in esperados])
    return b


def brazos_preflight(cfg) -> tuple[list[Brazo], str | None]:
    unidad, texto = (_opt(cfg,'unidad'), _systemctl_cat(_opt(cfg,'unidad'))) if _opt(cfg,'unidad') else _resolver_unidad(cfg.agente, cfg.usuario)
    if not texto:
        b = Brazo(100, "resolucion de la unidad de arranque")
        b.sin_medir(f"no pude leer la unidad del clon {cfg.agente}-{cfg.usuario}. "
                    f"Sin el ExecStart no hay nada que verificar, y salir en verde "
                    f"aca seria exactamente el gate vacio que esto evita.")
        return [b], None
    exec_start = "\n".join(l for l in texto.splitlines() if l.strip().startswith("ExecStart"))

    # EL ARRANQUE NO SIEMPRE ESTA EN LA UNIDAD (medido 31-jul: rojo en los CINCO).
    #
    #   seal-ada-user-clone@   ExecStart=/usr/bin/docker run --read-only ...  <- inline
    #   seal-user-clone@       ExecStart=scripts/run_user_clone_container.sh  <- delega
    #
    # Con solo la unidad, los cuatro que delegan aparecian SIN flags de
    # aislamiento, SIN imagen y SIN token montado — tres fallas graves que no
    # existian. El defecto aparecio en los cinco sujetos a la vez, que es la
    # firma de un problema del INSTRUMENTO y no de lo medido.
    #
    # Se sigue la indireccion: si el ExecStart apunta a un script del repo, su
    # contenido forma parte del arranque y hay que leerlo. Un gate que no sigue
    # la indireccion mide el envoltorio y cree que midio el contenido.
    import re as _re
    for ruta in _re.findall(r"(/home/dadito/IA/proyecto-seal/[^\s]+\.sh)", exec_start):
        try:
            p = Path(ruta)
            if p.is_file():
                exec_start += "\n# --- expandido desde " + ruta + " ---\n" + p.read_text(encoding="utf-8")
        except OSError:
            # Si no puedo leerlo, NO completo con optimismo: los brazos que
            # dependan de ese texto van a quedar sin medir, y el gate bloquea.
            pass
    brazos = [
        brazo_p1_token_por_instancia(cfg, exec_start),
        brazo_p2_token_correcto_montado(cfg, exec_start),
        brazo_p3_proyeccion(cfg, exec_start),
        brazo_p4_aislamiento(cfg, exec_start),
        brazo_p8_sin_flags_peligrosos(cfg, exec_start),
        brazo_p5_sin_credenciales(cfg, exec_start),
        brazo_p6_imagen_fijada(cfg, exec_start),
        brazo_p7_directorios_por_instancia(cfg, exec_start),
    ]
    dentro = Brazo(108, "brazos que exigen estar DENTRO del contenedor")
    dentro.no_aplica("el filesystem del clon y la revocacion en vivo no se pueden "
                     "medir desde el host antes de arrancar. Se miden con "
                     "--dentro-del-clon una vez que corre; declararlos aca como "
                     "aprobados seria firmar lo que no vi.")
    brazos.append(dentro)
    return brazos, unidad


def _auditar_consistencia() -> int:
    """CAPA 1 (idea de FABLE, 1-ago): ¿declaran todas las instancias el MISMO corpus?

    POR QUE ESTO Y NO UN PIN. Le pedi a FABLE el sha de la proyeccion "aprobada"
    para fijarlo, y su respuesta fue mejor que mi pregunta: **no existe una
    proyeccion aprobada**. Se regenero dos veces el mismo dia sin que nadie
    revisara ninguna. Fijar el valor que estaba vivo en ese momento no habria
    creado una aprobacion: **habria fabricado su apariencia**, y despues todos
    trataríamos como bendecido algo que solo tuvo la suerte de estar cargado.

    La consistencia no necesita saber cual version es la buena. Si los cinco
    clones declaran el mismo corpus, no hay swap parcial; si uno difiere, ahi hay
    algo que mirar, sin haber aprobado nada.

    NO ES UN GATE, Y ES DELIBERADO. Los clones reinician escalonados de a ~10 s:
    si el corpus se regenera en medio de ese rolling, dos instancias declaran
    distinto POR DISEÑO durante unos segundos. Un brazo bloqueante daria rojo en
    una operacion normal — exactamente el gate que se apaga solo o que grita sin
    motivo. Se reporta, no se bloquea.
    """
    import glob
    proy = Path("/home/dadito/.local/share/seal/user-clone-projections/technical.sqlite3")
    actual_sha = sha256_archivo(proy)
    filas = []
    for f in sorted(glob.glob("/home/dadito/.local/share/seal/clones/*/preflight.json")):
        try:
            d = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        b = next((x for x in d.get("brazos", []) if x.get("n") == 103), None)
        if not b:
            continue
        ev = b.get("evidencia", {})
        filas.append({
            "instancia": Path(f).parent.name,
            "generado": d.get("generado", "")[:19],
            "sha_archivo": ev.get("sha256_archivo") or ev.get("sha256"),
            "source_digest": ev.get("source_digest"),
        })

    print("\nAUDITORIA DE CONSISTENCIA DEL CORPUS\n")
    print(f"  proyeccion en disco AHORA: {(actual_sha or '?')[:16]}\n")
    if not filas:
        print("  sin recibos que comparar — no se puede concluir nada.\n")
        return 3
    ancho = max(len(f["instancia"]) for f in filas)
    for f in filas:
        marca = "=" if f["sha_archivo"] == actual_sha else "≠"
        print(f"  {marca} {f['instancia'].ljust(ancho)}  {f['generado']}  "
              f"archivo={(f['sha_archivo'] or '?')[:12]}  "
              f"fuente={(f['source_digest'] or 'n/d')[:12]}")

    vivos = [f for f in filas if not f["instancia"].endswith(("u999", "-999"))]
    distintos = {f["sha_archivo"] for f in vivos if f["sha_archivo"]}
    print()
    if len(distintos) <= 1:
        print(f"  CONSISTENTE — las {len(vivos)} instancias declaran el mismo corpus.")
        print("  (Esto NO dice que sea el corpus correcto: nadie aprobo ninguno todavia.)\n")
        return 0
    print(f"  DIVERGENCIA — {len(distintos)} corpus distintos entre instancias.")
    print("  Puede ser transitorio si hay un rolling restart en curso; si persiste,")
    print("  alguna instancia esta leyendo una proyeccion que las otras no.\n")
    return 1


async def _main_preflight(cfg) -> int:
    """El mismo contrato de salida que el gate completo: 0 / 1 / 3.

    Se mantiene identico A PROPOSITO. El lanzador y la unidad ya distinguen esos
    tres codigos, y un modo nuevo con codigos propios obligaria a que cada
    consumidor sepa en cual esta. Un `3` sigue significando "no medi", tanto si
    lo que no pude medir era la base como si era la unidad.
    """
    brazos, unidad = brazos_preflight(cfg)
    ancho = max(len(b.nombre) for b in brazos)
    print(f"\nPREFLIGHT DE ARRANQUE — clon {cfg.agente} para {cfg.usuario}")
    print(f"  unidad: {unidad or '(no resuelta)'}\n")
    for b in brazos:
        marca = {PASS: "OK  ", FAIL: "FALLA", UNMEASURABLE: "s/med",
                 NO_APLICA: "n/apl"}[b.veredicto]
        print(f"  {b.n:>3}. [{marca}] {b.nombre.ljust(ancho)}")
        print(f"         {b.detalle}")

    fallas = [b.n for b in brazos if b.veredicto == FAIL]
    sin_medir = [b.n for b in brazos if b.veredicto == UNMEASURABLE]
    if fallas:
        codigo, dictamen = 1, f"NO ARRANCA — brazos en falla: {fallas}"
    elif sin_medir:
        codigo, dictamen = 3, f"NO ARRANCA — brazos sin medir: {sin_medir}"
    else:
        na = [b.n for b in brazos if b.veredicto == NO_APLICA]
        extra = f" ({len(na)} diferidos al interior del clon: {na})" if na else ""
        codigo, dictamen = 0, f"LISTO — precondiciones verificadas{extra}"
    print(f"\n  {dictamen}\n  exit={codigo}\n")

    recibo = {
        "generado": datetime.now(timezone.utc).isoformat(),
        "modo": "preflight-docker",
        "agente": cfg.agente, "usuario": cfg.usuario, "unidad": unidad,
        "dictamen": dictamen, "exit": codigo,
        "brazos": [{"n": b.n, "nombre": b.nombre, "veredicto": b.veredicto,
                    "detalle": b.detalle, "evidencia": b.evidencia} for b in brazos],
        "hashes": {"gate": sha256_archivo(Path(__file__))},
    }
    if cfg.recibo:
        try:
            Path(cfg.recibo).parent.mkdir(parents=True, exist_ok=True)
            Path(cfg.recibo).write_text(json.dumps(recibo, indent=2, ensure_ascii=False),
                                        encoding="utf-8")
            print(f"  recibo: {cfg.recibo}")
        except OSError as e:
            # No poder DEJAR el recibo no invalida lo medido, pero tiene que
            # gritar: un gate sin rastro es un gate que nadie puede auditar.
            print(f"  AVISO: no pude escribir el recibo ({e})", file=sys.stderr)
    return codigo


# ─────────────────────────────────────────────────────────────────────────────
async def main() -> int:
    ap = argparse.ArgumentParser(description="Gate de arranque del clon (§11)")
    ap.add_argument("--agente", required=True)
    ap.add_argument("--usuario", required=True)
    ap.add_argument("--dsn-clon", help="DSN con el que conectaria el clon")
    ap.add_argument("--manifiesto", help="ruta al manifiesto de la instancia")
    ap.add_argument("--token-instancia",
                    help="ruta al token que USA la instancia. Se compara por hash "
                         "contra el token canonico del agente; nunca se imprime.")
    ap.add_argument("--unidad-canonica", help="unidad systemd del asiento que no debe moverse")
    ap.add_argument("--pid-esperado")
    ap.add_argument("--invocation-esperada")
    ap.add_argument("--cebo-propio", help="texto exacto de una memoria del propio usuario")
    ap.add_argument("--cebo-ajeno", help="texto exacto de una memoria de OTRO usuario")
    ap.add_argument("--arquitectura-sin-db", action="store_true",
                    help="DECLARA que este clon no tiene conexion a la base por diseño. "
                         "Los brazos que consultan la DB pasan a NO_APLICA en vez de "
                         "quedar sin medir. Queda registrado en el recibo.")
    ap.add_argument("--dentro-del-clon", action="store_true",
                    help="marcar solo si se ejecuta DENTRO del contenedor/instancia")
    ap.add_argument("--recibo", help="ruta donde escribir el recibo JSON")
    # ── flags AGREGADOS 31-jul. Ninguno cambia los de arriba, a proposito: el
    # §13 de FABLE invoca este gate por CLI con la firma vieja
    # (--agente/--usuario/--recibo). Romperla hubiera dejado su consumidor
    # fallando en silencio. Se agrega, no se altera.
    ap.add_argument("--preflight-docker", action="store_true",
                    help="mide las precondiciones del arranque REAL (contenedor "
                         "Docker) en vez de los brazos que consultan la base. "
                         "Es el modo pensado para correr como ExecStartPre.")
    ap.add_argument("--unidad", help="unidad systemd a inspeccionar; si se omite "
                                     "se prueban las dos convenciones conocidas")
    ap.add_argument("--sha-proyeccion", help="sha256 del ARCHIVO de proyeccion. "
                                             "Ojo: cambia en cada rebuild aunque el "
                                             "contenido sea el mismo. Para pinnear "
                                             "contenido usar --digest-fuente.")
    ap.add_argument("--digest-fuente", help="valor aprobado de manifest.source_digest. "
                                            "Detecta DERIVA (corpus reconstruido de "
                                            "otras fuentes), NO manipulacion: el digest "
                                            "vive dentro del archivo que describe.")
    ap.add_argument("--auditar-consistencia", action="store_true",
                    help="no gatea: compara que TODAS las instancias declaren el mismo "
                         "corpus. Detecta un swap parcial sin necesitar saber cual es "
                         "la version buena.")
    cfg = ap.parse_args()

    if cfg.auditar_consistencia:
        return _auditar_consistencia()
    if cfg.preflight_docker:
        return await _main_preflight(cfg)

    brazos: list[Brazo] = []
    brazos.append(await brazo_1_identidad(cfg))
    brazos.append(await brazo_2_no_colision(cfg))
    brazos.append(await brazo_3_memoria_propia(cfg))
    b4, b5 = await brazo_4y5_cruce_y_control(cfg)
    brazos.extend([b4, b5])
    brazos.append(await brazo_6_corpus_canonico(cfg))
    brazos.append(await brazo_7_filesystem(cfg))
    brazos.append(await brazo_8_canal(cfg))
    brazos.append(await brazo_9_antispoof(cfg))
    brazos.append(await brazo_10_revocacion(cfg))
    brazos.sort(key=lambda x: x.n)

    ancho = max(len(b.nombre) for b in brazos)
    print(f"\nGATE DE ARRANQUE — clon {cfg.agente} para {cfg.usuario}\n")
    for b in brazos:
        marca = {PASS: "OK  ", FAIL: "FALLA", UNMEASURABLE: "s/med",
                 NO_APLICA: "n/apl"}[b.veredicto]
        print(f"  {b.n:>2}. [{marca}] {b.nombre.ljust(ancho)}")
        print(f"        {b.detalle}")

    fallas = [b.n for b in brazos if b.veredicto == FAIL]
    sin_medir = [b.n for b in brazos if b.veredicto == UNMEASURABLE]
    if fallas:
        codigo, dictamen = 1, f"NO ARRANCA — brazos en falla: {fallas}"
    elif sin_medir:
        codigo, dictamen = 3, f"NO ARRANCA — brazos sin medir: {sin_medir}"
    else:
        na = [b.n for b in brazos if b.veredicto == NO_APLICA]
        extra = f" ({len(na)} no aplican por arquitectura: {na})" if na else ""
        codigo, dictamen = 0, f"LISTO — sin fallas ni brazos sin medir{extra}"
    print(f"\n  {dictamen}\n  exit={codigo}\n")

    recibo = {
        "generado": datetime.now(timezone.utc).isoformat(),
        "agente": cfg.agente, "usuario": cfg.usuario,
        "dictamen": dictamen, "exit": codigo,
        "brazos": [{"n": b.n, "nombre": b.nombre, "veredicto": b.veredicto,
                    "detalle": b.detalle, "evidencia": b.evidencia} for b in brazos],
        "hashes": {
            "gate": sha256_archivo(Path(__file__)),
            "manifiesto": sha256_archivo(Path(cfg.manifiesto)) if cfg.manifiesto else None,
        },
    }
    if cfg.recibo:
        Path(cfg.recibo).write_text(json.dumps(recibo, indent=2, ensure_ascii=False),
                                    encoding="utf-8")
        print(f"  recibo: {cfg.recibo}")
    return codigo


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
