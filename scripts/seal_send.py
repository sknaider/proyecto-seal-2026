#!/usr/bin/env python3
"""Authenticated SEAL webchat writer for agents and service identities.

Prevención (JARVIS/ADA, 2026-07-18): antes este script mostraba un traceback de
urllib cuando el server rechazaba (422 in_reply_to_required / 409
coordination_public_write_denied), dejando MUDO a cualquier agente que booteaba
sin saber por qué. Ahora:
  - Imprime el cuerpo JSON del error (motivo real), no un traceback.
  - Expone --unique-contribution / --contribution-reason para que un agente pueda
    declarar explícitamente un aporte único y auditable.
  - Falla cerrado ante 409/422: nunca convierte automáticamente un rechazo de
    coordinación en un override.
"""
import argparse, json, os, pathlib, sys, urllib.request, urllib.error, uuid

from seal_autonomy_guard import APPROVAL_GATES, autonomy_warning

API = 'http://localhost:8765/api/agents/send'

ap = argparse.ArgumentParser()
ap.add_argument("from_agent", nargs="?", default=os.environ.get("SEAL_AGENT"))
ap.add_argument("to_agent")
ap.add_argument("message", nargs="?", default=None)
# --message-file: el texto NUNCA pasa por el shell.
#
# POR QUE (7-sep-2026, JARVIS lo pidio tras cuatro mensajes rotos en un dia,
# tres suyos y uno de ALICE): pasar el cuerpo como argumento entre comillas
# DOBLES hace que bash EJECUTE lo que va entre acentos graves y expanda $VAR.
# Ese dia salieron mensajes con huecos silenciosos —"(mata )" donde decia el
# codigo— y uno llego a lanzar un proceso de verdad. La regla escrita era
# "usa heredoc con <<\'EOF\'"; se incumplio cuatro veces en un dia.
#
# Una regla que hay que recordar en cada llamada es un defecto de mecanismo,
# no de cuidado. Con esto el cuerpo viaja por un archivo o por stdin y el
# shell no lo toca: no hay nada que recordar.
ap.add_argument("--message-file", default=None,
                help="lee el cuerpo de un archivo (o '-' para stdin): el texto no pasa por el shell")
ap.add_argument("--channel", default="web_chat")
ap.add_argument("--type", dest="msg_type", default="conversation")
ap.add_argument("--in-reply-to", default=None)
ap.add_argument("--multi-response", action="store_true")
ap.add_argument("--proactive", action="store_true")
ap.add_argument("--idempotency-key", default=None)
ap.add_argument("--unique-contribution", action="store_true",
                help="Marca el mensaje como aporte único (escape de coordinación ENFORCE).")
ap.add_argument("--contribution-reason", default=None,
                help=">=20 chars explicando qué aporta de nuevo (requerido por el server si unique_contribution).")
ap.add_argument(
    "--message-escaped",
    action="store_true",
    help="El mensaje trae \\n / \\t / \\\\ escapados y se decodifican. OPT-IN: sin este "
         "flag el texto se manda tal cual. Salida para contextos donde el comando no "
         "puede contener saltos reales (p.ej. una misión A2, donde el guard deniega "
         "heredoc, pipe y las tools de escritura a la vez).",
)
ap.add_argument(
    "--approval-gate",
    choices=APPROVAL_GATES,
    default=None,
    help="Gate real que justifica consultar antes: destructive, external_commitment, scope_change o human_only.",
)
args = ap.parse_args()
if not args.from_agent:
    ap.error("FROM_AGENT requerido (argumento o SEAL_AGENT)")

# ── Guard de VISIBILIDAD (2-sep-2026) ────────────────────────────────────────
# La interfaz de William aparta `status`, `alert`, `heartbeat`, `cron` y
# `curiosity` a la pestana LATIDOS: NO aparecen en su chat
# (seal-studio/frontend/src/app/v2/LiveFeed.tsx:54).
#
# Medido el 2-sep: de mis 112 mensajes del dia, 60 fueron invisibles para el.
# ALICE perdio 98 de 137 (72 %), FABLE 22. Los cuatro creiamos que `status` era
# el tipo correcto para "informe de trabajo" — era el que se los ocultaba.
# Nadie lo verifico nunca porque `ok:true` alcanzaba.
#
# Esto NO se arregla acordandose: los cuatro sabiamos escribir bien y aun asi
# publicamos 180 mensajes que el no vio. El fix vive donde se toma la decision.
# EXACTAMENTE los tipos que LiveFeed.tsx:54 aparta. `alert` NO esta en esa lista
# y SI llega al chat: incluirlo aqui bloqueaba un tipo visible y —peor— el mensaje
# del bloqueo le ensenaba a los cinco un hecho FALSO del sistema.
# Lo marco ALICE tres veces antes de que yo lo verificara (2-sep).
_INVISIBLES_PARA_HUMANOS = {"status", "heartbeat", "cron", "curiosity"}
_DESTINO_HUMANO = {"WILLIAM", "HENRY", "DADITO"}

if (str(args.msg_type).strip().lower() in _INVISIBLES_PARA_HUMANOS
        and str(getattr(args, "to_agent", "")).strip().upper() in _DESTINO_HUMANO
        and os.environ.get("SEAL_ALLOW_INVISIBLE_TO_HUMAN") != "1"):
    sys.stderr.write(
        f"\n[seal_send] BLOQUEADO: --type {args.msg_type} dirigido a {args.to_agent}.\n"
        f"  Ese tipo va a la pestana LATIDOS y {args.to_agent} NO lo ve en su chat.\n"
        f"  Usa --type conversation para que lo lea.\n"
        f"  Si de verdad queres que vaya a latidos (un parte automatico que nadie\n"
        f"  espera leer), exportá SEAL_ALLOW_INVISIBLE_TO_HUMAN=1 y quedará\n"
        f"  registrado que fue deliberado.\n\n"
    )
    sys.exit(2)


def _normalize_in_reply_to(value):
    """Namespace bare DB ids; preserve immutable event ids unchanged."""
    source = str(value).strip() if value is not None else ""
    return f"db_{source}" if source.isdigit() else source


def _unescape(text: str) -> str:
    """Decodifica \\n, \\t, \\r y \\\\ — nada más.

    Deliberadamente NO se usa codecs.decode(..., 'unicode_escape'): ese decoder
    trata la cadena como latin-1 y destroza los acentos y la ñ, que es justo la
    corrupción que el resto del script evita con ensure_ascii=False.
    """
    out, index, table = [], 0, {"n": "\n", "t": "\t", "r": "\r", "\\": "\\"}
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text) and text[index + 1] in table:
            out.append(table[text[index + 1]])
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


if args.message_file is not None:
    if args.message is not None:
        sys.stderr.write(
            "[seal_send] usa el mensaje posicional O --message-file, no los dos.\n")
        raise SystemExit(2)
    if args.message_file == "-":
        args.message = sys.stdin.read()
    else:
        try:
            args.message = pathlib.Path(args.message_file).read_text(encoding="utf-8")
        except OSError as exc:
            sys.stderr.write(f"[seal_send] no puedo leer --message-file: {exc}\n")
            raise SystemExit(2)
    # Un archivo termina en salto de linea casi siempre; ese salto final no es
    # parte del mensaje y en el chat se ve como una linea vacia al pie.
    args.message = args.message.rstrip("\n")

if args.message is None:
    sys.stderr.write(
        "[seal_send] falta el mensaje: pasalo como argumento o con --message-file.\n")
    raise SystemExit(2)

if args.message_escaped:
    args.message = _unescape(args.message)

warning = autonomy_warning(args.to_agent, args.message, args.approval_gate)
if warning:
    sys.stderr.write(f"[seal_send][AUTONOMY BLOCKED] {warning}\n")
    raise SystemExit(2)

token_path = pathlib.Path(__file__).resolve().parents[1] / "messages" / f".agent_session_token_{args.from_agent.upper()}"
token = token_path.read_text(encoding="utf-8").strip()

# --- Partido automático por el cap de lectura (ALICE + JARVIS, 28-jul) -------
# El stream de eventos que consumen los AGENTES trunca a 1011 chars; la DB guarda
# todo. Medido por FABLE (44 mensajes en 1011 exacto) y confirmado por ALICE
# (64 de 133, cero por encima).
#
# Por qué PARTE y no sólo advierte: la versión "te imprimo el largo y decidís vos"
# ya se probó dos veces esta noche y falló las dos — FABLE vio 1088 y mandó igual,
# ALICE vio 1167 y mandó igual veinte minutos después de leer el aviso de FABLE.
# Un control que depende de que alguien se acuerde no es un control (JARVIS).
#
# Por qué NO parte para humanos: William y Henry leen el webchat COMPLETO. Partir
# ahí no arregla nada y degrada la regla de mensajes hermosos, que es explícita.
# El defecto es agente↔agente; la mitigación también.
_READ_CAP = 1011
_HUMANS = {"william", "henry"}

# Canales que LEE un humano. El webchat muestra el mensaje completo, así que
# partirlo ahí no protege a nadie y sólo hace esperar a William entre partes.
# `user:*` son los canales de frente (ej. user:3:gtl-sistemas, que lee Henry).
def _canal_humano(canal: str) -> bool:
    c = (canal or "").strip().lower()
    return c == "web_chat" or c.startswith("user:")
# Espacio reservado para el prefijo de segmentación. El peor caso real es
# "**(10/10 · abcdef · 123456 ch)** " = 33 chars; 40 deja margen. Este número
# quedó desactualizado una vez —el prefijo creció y el presupuesto no— y las
# partes salieron a 1026 chars, por encima del cap. Si se toca el formato del
# prefijo, se toca esto.
_PREFIX_BUDGET = 40


def _atoms(text):
    """Corta en unidades que NO se pueden partir por dentro.

    Un bloque ``` cercado es un átomo entero: partirlo deja dos pedazos que
    SIGUEN PARECIENDO VÁLIDOS, que es donde el corte hace más daño (JARVIS).
    Fuera de las vallas, la unidad es el párrafo.
    """
    out, buf, in_fence = [], [], False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            buf.append(line)
            if not in_fence:              # se cerró la valla: el bloque va completo
                out.append("\n".join(buf))
                buf = []
            continue
        if in_fence:
            buf.append(line)
            continue
        if not line.strip():
            if buf:
                out.append("\n".join(buf))
                buf = []
            continue
        buf.append(line)
    if buf:                                # valla sin cerrar o cola sin línea en blanco
        out.append("\n".join(buf))
    return out


_CODE_WARN = "⚠️ CODE_FRAGMENT {i}/{n} — NO EJECUTAR AISLADO"


def _split_code(atom, budget):
    """Fragmenta un bloque ``` mayor al cap, etiquetando el peligro.

    Antes esto era una excepción: el bloque salía entero y excedido. ADA la
    refutó y el argumento es correcto — mi razón para no partirlo era que dos
    mitades de código SIGUEN PARECIENDO VÁLIDAS, pero mandarlo sobredimensionado
    entrega al receptor exactamente eso: la primera mitad, **sin ninguna marca de
    que falte algo**. Estrictamente peor. Lo que la mitad necesitaba no era no
    existir, era una ETIQUETA.

    Invariante universal agente↔agente: cero partes por encima del límite.
    """
    lineas = atom.split("\n")
    apertura = lineas[0] if lineas and lineas[0].lstrip().startswith("```") else "```"
    if lineas and lineas[0].lstrip().startswith("```"):
        lineas = lineas[1:]
    if lineas and lineas[-1].lstrip().startswith("```"):
        lineas = lineas[:-1]

    # Descontar lo que cuesta el envoltorio de cada fragmento: marcador + vallas.
    envoltorio = len(_CODE_WARN.format(i=99, n=99)) + len(apertura) + len("\n```") + 4
    interno = max(budget - envoltorio, 80)

    grupos, cur = [], []
    for linea in lineas:
        if len(linea) > interno:                      # una sola línea gigantesca
            if cur:
                grupos.append(cur)
                cur = []
            grupos.extend([[linea[i:i + interno]] for i in range(0, len(linea), interno)])
            continue
        tentativo = cur + [linea]
        if len("\n".join(tentativo)) <= interno:
            cur = tentativo
        else:
            if cur:
                grupos.append(cur)
            cur = [linea]
    if cur:
        grupos.append(cur)

    total = len(grupos)
    sys.stderr.write(
        f"[seal_send][CAP] bloque de código de {len(atom)} chars fragmentado en "
        f"{total} partes etiquetadas CODE_FRAGMENT.\n")
    return [f"{_CODE_WARN.format(i=i, n=total)}\n{apertura}\n" + "\n".join(g) + "\n```"
            for i, g in enumerate(grupos, 1)]


def _force_split(atom, budget):
    """Parte un átomo que no entra, degradando el criterio de corte.

    Caso real que motivó esto (JARVIS, 28-jul, hallado por efecto a los 2 min de
    desplegar): bajo confinamiento A2 el guard deniega heredoc, así que sus
    mensajes salen en UNA SOLA LÍNEA sin líneas en blanco. Sin párrafos, el
    partidor no encontraba dónde cortar y mandaba entero — o sea que el control
    fallaba justo con el agente que MENOS vías alternativas tiene, porque bajo A2
    tampoco puede leer la DB. Dos controles nuestros interactuando.

    Un bloque ``` cercado NO se parte: dos mitades de código siguen pareciendo
    válidas. Ése sí va entero y excedido, con aviso.
    """
    if atom.lstrip().startswith("```"):
        return _split_code(atom, budget)

    # Degradación: renglón -> oración -> espacio. Nunca a mitad de palabra, que es
    # lo que protege rutas y hashes (no llevan espacios adentro).
    for sep in ("\n", ". ", " "):
        # El separador viaja PEGADO al token. Si se reuniera con `cur + sep + tok`
        # se pierde el separador de cada borde: partiendo por ". " desaparecía un
        # punto por corte. Medido — el test de reconstrucción lo marcó como PIERDE.
        trozos = atom.split(sep)
        toks = [t + sep for t in trozos[:-1]] + [trozos[-1]]
        piezas, cur = [], ""
        for tok in toks:
            if len(cur) + len(tok) <= budget:
                cur += tok
            else:
                if cur:
                    piezas.append(cur)
                cur = tok
        if cur:
            piezas.append(cur)
        if piezas and all(len(p) <= budget for p in piezas):
            return piezas

    # Último recurso: una palabra sola más larga que el presupuesto (un hash
    # gigante, un base64). Se corta duro antes que perder la cola en silencio.
    sys.stderr.write(
        f"[seal_send][CAP] token de {len(atom)} chars sin punto de corte natural: "
        f"corte duro cada {budget}.\n")
    return [atom[i:i + budget] for i in range(0, len(atom), budget)]


def _split_message(text, cap=_READ_CAP):
    """Devuelve la lista de partes. Una sola si entra."""
    if len(text) <= cap:
        return [text]
    budget = cap - _PREFIX_BUDGET
    parts, cur = [], ""
    for atom in _atoms(text):
        cand = atom if not cur else cur + "\n\n" + atom
        if len(cand) <= budget:
            cur = cand
            continue
        if cur:
            parts.append(cur)
            cur = ""
        if len(atom) <= budget:
            cur = atom
        else:
            parts.extend(_force_split(atom, budget))
    if cur:
        parts.append(cur)
    if len(parts) <= 1:
        return parts or [text]
    total = len(parts)
    # Segmentación EXPLÍCITA (criterio de cierre de ADA): id de correlación
    # compartido + parte i/n + largo total del original. Sin el id, dos mensajes
    # partidos que se intercalan en el canal no se pueden reagrupar; sin el largo
    # total, el lector no sabe si le falta una parte que nunca llegó.
    mid = uuid.uuid4().hex[:6]
    return [f"**({i}/{total} · {mid} · {len(text)} ch)** {p}" for i, p in enumerate(parts, 1)]


def _build(message, index, total):
    p = {"from": args.from_agent, "to": args.to_agent, "type": args.msg_type,
         "channel": args.channel, "message": message, "session_key": token}
    # in_reply_to sólo en la primera: encadenar todas al mismo padre ensucia el hilo.
    if args.in_reply_to is not None and index == 0:
        p["in_reply_to"] = _normalize_in_reply_to(args.in_reply_to)
    if args.multi_response:
        p["multi_response"] = True
    if args.proactive:
        p["proactive"] = True
    if args.idempotency_key:
        # Sufijo por parte: con la misma clave el server deduplica y se pierden
        # las partes 2..N en silencio.
        p["idempotency_key"] = args.idempotency_key if total == 1 else f"{args.idempotency_key}-p{index+1}"
    # Los flags de coordinación van en TODAS las partes: si la 1 necesitaba el
    # escape, la 2 también, y una parte rechazada deja el mensaje mutilado.
    if args.unique_contribution:
        p["unique_contribution"] = True
    if args.contribution_reason:
        p["contribution_reason"] = args.contribution_reason
    # El gate declarado se PERSISTE, no sólo se usa para decidir el bloqueo de arriba.
    # Antes moría en autonomy_warning(): un mensaje con `--approval-gate human_only` y uno
    # sin él quedaban idénticos en la DB, así que "qué espera decisión de William" no se
    # podía consultar. Va en todas las partes, como el resto de los flags.
    if args.approval_gate:
        p["approval_gate"] = args.approval_gate
    # Etiqueta del CUERPO que habla (William 3-sep-2026 21:26: "pon una etiqueta que diga si es Codex o Claude").
    # El lanzador exporta SEAL_RUNTIME_INSTANCE (ADA_CLAUDE / ADA_CODEX_TUI). El servidor la persiste en
    # metadata cuando acepta `metadata` en /api/agents/send (pedido a NEXUS); hasta entonces viaja igual.
    _inst = os.environ.get("SEAL_RUNTIME_INSTANCE", "").strip()
    if _inst:
        p["metadata"] = {"runtime_instance": _inst}
    return p


def _post(p):
    """POST payload. Returns (ok, status, body_text)."""
    data = json.dumps(p, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(API, data=data,
                                 headers={'Content-Type':'application/json; charset=utf-8'})
    try:
        return True, 200, urllib.request.urlopen(req).read().decode()
    except urllib.error.HTTPError as e:
        return False, e.code, e.read().decode()


# Fail-safe: este script es la ÚNICA vía de escritura del equipo. Si la lógica de
# partido tiene un bug, se manda el mensaje entero — nunca se deja mudo a nadie.
#
# CORREGIDO 1-sep-2026 (NEXUS, orden de William "hay que corregir el tiempo de
# respuesta en web chat"). La exención miraba sólo el DESTINATARIO, y casi todo
# el tráfico va a `equipo` — que es el canal que William LEE. Resultado: veía
# "(1/3 · ...)" todo el día y percibía el chat como lento, esperando las partes.
#
#   --to William  -> entero   (correcto, pero casi nunca lo usamos)
#   --to equipo   -> PARTIDO  <- el 100% de lo que el ve
#
# El partido existe porque el stream de eventos que consumimos los AGENTES trunca
# a 1011 chars. El webchat NO trunca: a un humano el corte sólo le agrega espera.
# Por eso la exención va también por CANAL, no sólo por nombre del destinatario.
if args.to_agent.strip().lower() in _HUMANS or _canal_humano(args.channel):
    messages = [args.message]
else:
    try:
        messages = _split_message(args.message)
    except Exception as exc:                                  # pragma: no cover
        sys.stderr.write(f"[seal_send][CAP] el partido falló ({exc}); envío entero.\n")
        messages = [args.message]

if len(messages) > 1:
    sys.stderr.write(
        f"[seal_send][CAP] {len(args.message)} chars > {_READ_CAP}: enviado en "
        f"{len(messages)} partes para que los agentes lo lean completo.\n")

# --- Corte de bucles en dos escalones (idea de Bob reescrita; ver seal_loop_guard.py)
#
# El 2-may-2026 el bucle de reflejos de NEXUS publico 199 mensajes en 60 s y lo
# corto JARVIS a mano. Esto lo corta solo.
#
# RESPETA el invariante de arriba —"nunca se deja mudo a nadie"— porque suprime
# COPIAS IDENTICAS de un contenido que YA llego cinco veces, no contenido nuevo.
# Mudez es que el mensaje no llegue, no que no lleguen copias.
#
# Falla ABIERTO: si el guard revienta o no puede leer su estado, se manda igual.
# Salida de emergencia: SEAL_SEND_NO_LOOP_GUARD=1
try:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from seal_loop_guard import evaluar as _eval_bucle
    _accion, _rep, _aviso = _eval_bucle(args.to_agent, args.channel, args.message,
                                        os.environ.get("SEAL_AGENT", ""))
except Exception:
    _accion, _rep, _aviso = "enviar", 0, ""
if _aviso:
    sys.stderr.write(_aviso + "\n")
if _accion == "suprimir":
    print(json.dumps({"ok": True, "suppressed": True, "reason": "loop_guard",
                      "identical_repeats": _rep}, ensure_ascii=False))
    sys.exit(0)          # exit 0 A PROPOSITO: no romper al llamador

failed = False
for _i, _msg in enumerate(messages):
    ok, status, body = _post(_build(_msg, _i, len(messages)))
    print(body)
    if not ok:
        failed = True
        sys.stderr.write(f"[seal_send] HTTP {status} en parte {_i+1}/{len(messages)}: {body}\n")
        break                       # no seguir mandando partes de un mensaje roto

if failed:
    sys.exit(1)
