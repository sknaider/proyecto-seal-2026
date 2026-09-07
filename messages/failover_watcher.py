#!/usr/bin/env python3
"""
failover_watcher.py — Garantía de respuesta anti-silencio para el single-voice (JARVIS 8-jul-2026).

PROBLEMA (William 17:27: «por que no me responden, solucionar eso»): el single-voice rutea el broadcast de
William a UN agente; si ESE está OCUPADO/dormido/colgado, William se queda SIN respuesta (silencio). El claim
elige un responder pero no garantiza que RESPONDA.

FIX (este daemon, standalone — no toca chat_server ni el filtro): tailea william_channel.jsonl. Cuando William
(o Henry) postea un broadcast a EQUIPO que espera respuesta, arranca un timer. Si NADIE le responde en T seg,
hace FAILOVER: le pide al siguiente agente en jerarquía que responda (post dirigido). Escala por la jerarquía
hasta que alguien responde o se agota el roster. Solo dispara ante SILENCIO real → ruido mínimo.

Cubre el caso «primario ocupado» (que la liveness no cubre): no importa POR QUÉ el primario no respondió —
si en T no hubo respuesta, otro toma la posta. Determinístico y sin estado compartido.

Deploy: correr como daemon (systemd o setsid). Idempotente-ish: si ya respondió alguien, no dispara.
"""
from __future__ import annotations
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from messages.agent_writer import send_agent_message_sync

HERE = os.path.dirname(os.path.abspath(__file__))
CHANNEL = os.path.join(HERE, "william_channel.jsonl")
SEND_URL = "http://localhost:8765/api/agents/send"

# Jerarquía de failover (mismo roster que el single-voice). Orden = a quién se le pide primero.
try:
    import importlib.util
    _spec = importlib.util.spec_from_file_location("_svf", os.path.join(HERE, "seal_monitor_filter.py"))
    _svf = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_svf)
    ROSTER = list(_svf.SINGLE_VOICE_ROSTER)
    ALLWORDS = tuple(_svf.SINGLE_VOICE_ALLWORDS)
    _is_conversational = _svf._is_conversational_broadcast
except Exception:
    ROSTER = ["NEXUS", "JARVIS", "ALICE", "FABLE", "ADA"]
    ALLWORDS = (
        "todos", "todas", "agentes", "equipo", "chicos", "chicas",
        "muchachos", "muchachas", "hermanos", "hermanas", "familia",
    )
    def _is_conversational(text: str, _msg_type: str = "conversation") -> bool:
        return len((text or "").strip()) <= 280

TRUSTED = {"WILLIAM", "HENRY"}
RESPONDERS = {a.upper() for a in ROSTER} | {"DUM"}
# TUNE 11-jul (JARVIS, orden de William «el failover hace mucho ruido» + baseline de ALICE:
# 63 posts FAILOVER vs 30 de William = 2.1x su tráfico). Cambios, todos env-reversibles:
#   - T_SECONDS 30→240: el umbral viejo era MENOR que un turno real de LLM → despertaba
#     agentes extra cuando el lead ya estaba respondiendo → duplicados.
#   - ACK/progress SOLO para mensajes deep-work (allow_failover): en fanout conversacional
#     los agentes responden solos en ~15s; ACK+progress ahí es puro ruido (voto de NEXUS).
#   - PROGRESS_SECONDS 45→240 y máximo PROGRESS_MAX pings por mensaje (antes: infinitos).
# Rollback: SEAL_FAILOVER_SECONDS=30 SEAL_ACK_SCOPE=all SEAL_PROGRESS_SECONDS=45 SEAL_PROGRESS_MAX=0
T_SECONDS = float(os.environ.get("SEAL_FAILOVER_SECONDS", "240"))
ACK_SECONDS = float(os.environ.get("SEAL_ACK_SECONDS", "1"))
ACK_ALL = os.environ.get("SEAL_ACK_SCOPE", "failover_only").strip().lower() == "all"
# TUNE 1-sep-2026 (JARVIS): umbral de ACK para mensajes CONVERSACIONALES. El tune 11-jul los
# SUPRIMÍA asumiendo respuestas ~15s; medido 1-sep la mediana subió a 57s → William quedaba mudo y
# preguntaba "me leíste?". Ahora conversacional TAMBIÉN ACKea, pero sólo si tarda > este umbral: un
# turno rápido correlaciona su respuesta (in_reply_to → acked=True en _mark_agent_activity) ANTES
# del umbral → sin ACK, sin ruido; uno lento sigue pendiente → ACK. Deep-work conserva ACK_SECONDS
# (1s). Rollback a la conducta 11-jul (conversacional mudo): SEAL_ACK_SECONDS_CONV=999999.
ACK_SECONDS_CONV = float(os.environ.get("SEAL_ACK_SECONDS_CONV", "8"))
PROGRESS_SECONDS = float(os.environ.get("SEAL_PROGRESS_SECONDS", "240"))
PROGRESS_MAX = int(os.environ.get("SEAL_PROGRESS_MAX", "2"))  # 0 = sin tope (legacy)
MIN_RESP_LEN = 40       # una "respuesta" real de agente supera esto (no ACK/heartbeat)
POLL = 0.25             # ACK visible ~1 s; margen p95 <=2 s con jitter del loop
EVENTS_DIR = Path("/tmp")
ROTATION_DRAIN_SECONDS = float(os.environ.get("SEAL_ROTATION_DRAIN_SECONDS", "5"))
_PREFIX_BYTES = 256


class _TailSource:
    """One binary JSONL inode with newline-safe buffering."""

    def __init__(self, stream, *, start_at_end: bool):
        self.stream = stream
        if start_at_end:
            self.stream.seek(0, os.SEEK_END)
        self.buffer = b""
        self.last_data_at = time.monotonic()
        self.anchor_start = 0
        self.anchor = b""
        self.capture_anchor()

    @classmethod
    def open(cls, path: str | os.PathLike[str], *, start_at_end: bool):
        return cls(open(path, "rb"), start_at_end=start_at_end)

    def read_complete_lines(self, *, validate_rewrite: bool = False) -> tuple[list[str], bool]:
        rewritten = False
        data = b""
        for _attempt in range(4):
            anchor_start, anchor = self.anchor_start, self.anchor
            prefix_before = os.pread(self.stream.fileno(), _PREFIX_BYTES, 0)
            if validate_rewrite and anchor:
                observed = os.pread(self.stream.fileno(), len(anchor), anchor_start)
                if observed != anchor:
                    self.stream.seek(0)
                    self.buffer = b""
                    rewritten = True
                    anchor_start, anchor = 0, b""
                    prefix_before = os.pread(self.stream.fileno(), _PREFIX_BYTES, 0)

            data = self.stream.read()
            prefix_after = os.pread(self.stream.fileno(), _PREFIX_BYTES, 0)
            anchor_after = (
                os.pread(self.stream.fileno(), len(anchor), anchor_start) if anchor else anchor
            )
            if validate_rewrite and (prefix_after != prefix_before or anchor_after != anchor):
                # copytruncate/rewrite raced the read. Discard the mixed snapshot and
                # retry from byte zero; never emit a partial generation.
                self.stream.seek(0)
                self.buffer = b""
                rewritten = True
                continue
            break
        else:
            raise RuntimeError("channel changed generation repeatedly during read")

        if data:
            self.buffer += data
            self.last_data_at = time.monotonic()
        self.capture_anchor()
        if b"\n" not in self.buffer:
            return [], rewritten
        chunks = self.buffer.split(b"\n")
        self.buffer = chunks.pop()
        return [chunk.decode("utf-8", errors="replace") for chunk in chunks], rewritten

    def close(self) -> None:
        self.stream.close()

    def capture_anchor(self) -> None:
        position = self.stream.tell()
        self.anchor_start = max(0, position - _PREFIX_BYTES)
        self.anchor = os.pread(
            self.stream.fileno(), position - self.anchor_start, self.anchor_start
        )


class _ChannelTail:
    """``tail -F`` semantics plus late-writer draining and JSONL framing."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        start_at_end: bool,
        drain_seconds: float = ROTATION_DRAIN_SECONDS,
    ):
        self.path = os.fspath(path)
        self.drain_seconds = drain_seconds
        self.current = _TailSource.open(self.path, start_at_end=start_at_end)
        self.retired: list[_TailSource] = []

    def _refresh(self) -> str | None:
        try:
            current_path = os.stat(self.path)
        except FileNotFoundError:
            return None

        opened = os.fstat(self.current.stream.fileno())
        if (opened.st_dev, opened.st_ino) != (current_path.st_dev, current_path.st_ino):
            replacement = _TailSource.open(self.path, start_at_end=False)
            self.current.last_data_at = time.monotonic()
            self.retired.append(self.current)
            self.current = replacement
            return "replaced"

        position = self.current.stream.tell()
        observed_anchor = os.pread(
            self.current.stream.fileno(), len(self.current.anchor), self.current.anchor_start
        )
        anchor_rewritten = bool(self.current.anchor) and observed_anchor != self.current.anchor
        if current_path.st_size < position or anchor_rewritten:
            self.current.stream.seek(0)
            self.current.buffer = b""
            self.current.last_data_at = time.monotonic()
            self.current.capture_anchor()
            return "truncated" if current_path.st_size < position else "rewritten"
        return None

    def poll(self) -> tuple[list[str], list[str]]:
        events: list[str] = []
        reason = self._refresh()
        if reason:
            events.append(reason)

        lines: list[str] = []
        still_retired: list[_TailSource] = []
        for source in self.retired:
            retired_lines, _ = source.read_complete_lines()
            lines.extend(retired_lines)
            if time.monotonic() - source.last_data_at < self.drain_seconds:
                still_retired.append(source)
            else:
                if source.buffer:
                    events.append("retired_partial_dropped")
                source.close()
        self.retired = still_retired
        current_lines, raced_rewrite = self.current.read_complete_lines(validate_rewrite=True)
        lines.extend(current_lines)
        if raced_rewrite:
            events.append("rewritten_during_read")
        return lines, events

    def close(self) -> None:
        for source in self.retired:
            source.close()
        self.current.close()


def _trusted_origin(message: dict) -> bool:
    provenance = message.get("provenance") or {}
    sender = str(message.get("from") or "").upper()
    return (
        sender in TRUSTED
        and isinstance(provenance, dict)
        and provenance.get("verified") is True
        and str(provenance.get("verified_sender") or "").upper() == sender
    )


def _internal_wake(to_agent: str, text: str, source_id: str | None = None) -> None:
    """Wake an agent without impersonating it or traversing public/DM writers."""
    target = EVENTS_DIR / f"seal_events_{to_agent.upper()}.log"
    payload = {
        "id": f"failover_wake_{source_id or int(time.time())}_{to_agent.lower()}",
        "from": "FAILOVER",
        "to": to_agent.upper(),
        "type": "operational_wake",
        "channel": f"internal:failover:{to_agent.lower()}",
        "message": text,
        "in_reply_to": source_id,
        "authority": "wake_only_no_user_authority",
    }
    with target.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _post(
    to_agent: str,
    text: str,
    *,
    idempotency_key: str | None = None,
    source_id: str | None = None,
) -> None:
    # Fix visibilidad (bug 8-jul: los pings de failover caían en el chat de William y lo confundían/spameaban).
    # Verificado por efecto: un mensaje en canal dm:failover:<agente> NO entra a william_channel.jsonl
    # (invisible para William) y llega al agente por su ws_listener (es participante del dm).
    #   - Ping a un AGENTE ("tomá la posta") → canal dm:failover:<agente> = OCULTO de William.
    #   - Mensaje FINAL a WILLIAM ("nadie pudo responder") → web_chat = VISIBLE (es PARA él, una sola vez).
    if to_agent.upper() != "WILLIAM":
        try:
            _internal_wake(to_agent, text, source_id)
        except Exception as e:
            print(f"[failover] wake falló → {to_agent}: {e}", flush=True)
        return
    try:
        send_agent_message_sync(
            "FAILOVER", to_agent, text, channel="web_chat", proactive=True,
            idempotency_key=idempotency_key,
        )
    except Exception as e:
        print(f"[failover] post falló → {to_agent}: {e}", flush=True)


def _is_allcall(text: str) -> bool:
    import re
    low = (text or "").lower()
    return any(re.search(r"\b" + w + r"\b", low) for w in ALLWORDS)


def _names_agent(text: str) -> bool:
    import re
    low = (text or "").lower()
    return any(re.search(r"\b" + a.lower() + r"\b", low) for a in ROSTER)


def _named_agents(text: str) -> list[str]:
    import re
    low = (text or "").lower()
    return [a for a in ROSTER if re.search(r"\b" + re.escape(a.lower()) + r"\b", low)]


def _lead_label(text: str, msg_id: str) -> str:
    if _is_allcall(text):
        return "el equipo"
    named = _named_agents(text)
    if named:
        return " y ".join(named)
    if _is_conversational(text):
        return "el equipo"
    try:
        return str(_svf._single_voice_responder(text, msg_id))
    except Exception:
        return "el agente asignado"


def _reply_source(message: dict) -> str:
    source = message.get("in_reply_to") or message.get("reply_to")
    metadata = message.get("metadata") or {}
    if isinstance(metadata, dict):
        source = source or metadata.get("in_reply_to")
    return str(source or "")


def _mark_agent_activity(pending: dict, message: dict, now: float) -> str | None:
    """Correlate ACK/final by exact source; unrelated chatter changes nothing."""
    text = str(message.get("message") or message.get("content") or "").strip()
    source = _reply_source(message)
    if source in pending:
        mid = source
    else:
        return None
    item = pending[mid]
    item["acked"] = True
    item["last_activity"] = now
    return mid if len(text) >= MIN_RESP_LEN else None


def process_timeouts(pending: dict, now: float, post=_post, activity: dict | None = None) -> None:
    """Emit ACK/progress and perform failover for outstanding messages.

    ``activity`` (opcional): estado global {"last_responder_ts": float} con el último post
    SUSTANCIAL de un responder a WILLIAM/EQUIPO. FIX 11-jul 01:12 (JARVIS, falso-wake por
    efecto: upload de William quedó sin correlación exacta mientras ALICE respondía al
    mensaje de texto hermano → wake espurio a 480s): la correlación EXACTA (tightening
    00:37) queda para la contabilidad de ACK, pero la decisión de ESCALAR failover vuelve
    al objetivo original del 8-jul — evitar SILENCIO TOTAL. Si hubo actividad sustancial
    de cualquier responder DESPUÉS del mensaje pendiente, William no está en silencio:
    se resuelve sin despertar a nadie.
    """
    for mid, p in list(pending.items()):
        elapsed = now - p["ts"]
        # TUNE 11-jul + 1-sep (JARVIS): deep-work ACKea a ACK_SECONDS (1s); conversacional a
        # ACK_SECONDS_CONV (alto) para que SÓLO los turnos lentos avisen "recibido" y los rápidos
        # (que correlacionan su respuesta antes del umbral) sigan sin ruido. Progress y failover
        # siguen siendo exclusivos de deep-work (el conversacional hace un único ACK y se va).
        conversational = not p["allow_failover"] and not ACK_ALL
        ack_threshold = ACK_SECONDS_CONV if conversational else ACK_SECONDS
        if not p["acked"] and elapsed >= ack_threshold:
            verb = "están" if " y " in p["lead"] else "está"
            post(
                "William",
                f"✅ Recibido por SEAL. {p['lead']} {verb} procesando tu mensaje; "
                "te responderá con el resultado verificado.",
                idempotency_key=f"seal-ack-{mid}",
            )
            p["acked"] = True
            p["last_activity"] = now
            print(f"[failover] ACK visible {mid[:24]} lead={p['lead']}", flush=True)

        if conversational:
            # conversacional: un solo ACK, sin progress ni failover; GC cuando ya no aporta
            if elapsed > 900:
                del pending[mid]
            continue

        failover_due = (
            p["allow_failover"]
            and elapsed >= T_SECONDS * (p["level"] + 1)
        )
        if p["acked"] and now >= p["next_progress"] and not failover_due:
            progress_index = max(1, int(elapsed // PROGRESS_SECONDS))
            if PROGRESS_MAX and progress_index > PROGRESS_MAX:
                # Tope de ⏳ alcanzado: silencio, pero el failover de abajo SIGUE activo.
                p["next_progress"] = float("inf")
            else:
                post(
                    "William",
                    f"⏳ {p['lead']} sigue trabajando en tu pedido (~{int(elapsed)} s). "
                    "El hilo principal continúa disponible; aún no se declara terminado.",
                    idempotency_key=f"seal-progress-{mid}-{progress_index}",
                )
                p["next_progress"] = now + PROGRESS_SECONDS
                print(f"[failover] progreso {mid[:24]} n={progress_index}", flush=True)

        if not p["allow_failover"]:
            continue
        if elapsed < T_SECONDS * (p["level"] + 1):
            continue
        # FIX 11-jul: anti-silencio, no anti-descorrelación — si CUALQUIER responder posteó
        # sustancial DESPUÉS de este mensaje, no hay silencio: resolver sin despertar.
        if activity and activity.get("last_responder_ts", 0.0) > p["ts"]:
            print(f"[failover] resuelto {mid[:24]} por actividad global post-mensaje (sin wake)", flush=True)
            del pending[mid]
            continue
        nxt = next((a for a in ROSTER if a.upper() not in p["tried"]), None)
        if nxt is None:
            print(f"[failover] {mid[:24]} agotó roster — nadie respondió", flush=True)
            post(
                "William",
                "⚠️ Ningún agente pudo responder tu último mensaje (todos ocupados/caídos). "
                "Reintentá o decilo directo a un agente por nombre.",
                idempotency_key=f"seal-failover-exhausted-{mid}",
            )
            del pending[mid]
            continue
        p["tried"].add(nxt.upper())
        p["level"] += 1
        p["next_progress"] = now + PROGRESS_SECONDS
        ping = (
            f"⏱️ FAILOVER: William posteó a equipo hace ~{int(elapsed)}s y nadie entregó "
            f"resultado. Tomá la posta; responde con --in-reply-to {mid}. Antes revisa si "
            f"el lead ya contestó. Texto: «{p['text'][:300]}»"
        )
        post(nxt, ping, source_id=mid)
        print(f"[failover] {mid[:24]} → wake a {nxt} (nivel {p['level']})", flush=True)


def _is_dm_source(d: dict) -> bool:
    """True si el registro viene de un canal privado (dm:*) o llega cifrado."""
    channel = str(d.get("channel") or "").strip().lower()
    return channel.startswith("dm:") or d.get("_encrypted") is True


def _process_message(d: dict, pending: dict, activity: dict, now: float) -> None:
    frm = (d.get("from") or "").upper()
    to = (d.get("to") or "").upper()
    text = d.get("message") or d.get("content") or ""
    mid = str(d.get("id") or d.get("idempotency_key") or "")

    # FIX 3-sep-2026 (JARVIS, medido con ALICE): william_channel.jsonl también trae los DMs
    # (canal dm:*, cuerpo cifrado). Un DM de William a un agente creaba un pendiente y el
    # acuse «X está procesando» salía al canal PÚBLICO: la conversación privada no se leía,
    # pero se veía que existía. Un DM tiene un solo destinatario con su propio monitor; el
    # failover público no aplica. Ni acuse, ni progreso, ni escalada para fuentes dm:*.
    if _is_dm_source(d):
        return
    if _trusted_origin(d) and to in ({"EQUIPO"} | RESPONDERS) and mid:
        if len(text.strip()) >= 3:
            council = d.get("coordination") if isinstance(d.get("coordination"), dict) else None
            council_active = bool(council and council.get("enforced") is True)
            if council_active:
                mid = str(council.get("source_id") or mid)
                council_mode = str(council.get("mode") or "discussion")
                allow_failover = council_mode in {"execution", "direct"}
                council_lead = str(council.get("lead") or "el agente asignado")
            else:
                allow_failover = (
                    to == "EQUIPO" and not _is_allcall(text) and not _names_agent(text)
                    and not _is_conversational(text)
                )
                council_lead = to if to != "EQUIPO" else _lead_label(text, mid)
            if mid in pending:
                return  # replay/overlap must not reset timers or escalation level
            pending[mid] = {
                "ts": now,
                "text": text,
                "level": 0,
                "tried": set(),
                "acked": False,
                "last_activity": now,
                "next_progress": now + PROGRESS_SECONDS,
                "allow_failover": allow_failover,
                "lead": council_lead,
            }
            print(
                f"[failover] pendiente {mid[:24]} lead={pending[mid]['lead']} "
                f"failover={allow_failover} «{text[:40]}»",
                flush=True,
            )
        return

    if frm in RESPONDERS and to in ("WILLIAM", "EQUIPO"):
        if len(text.strip()) >= MIN_RESP_LEN:
            activity["last_responder_ts"] = now
        resolved = _mark_agent_activity(pending, d, now)
        if resolved:
            print(f"[failover] resuelto {resolved[:24]} por actividad de {frm}→{to}", flush=True)
            del pending[resolved]


def main() -> None:
    print(
        f"[failover] watcher iniciado — ACK={ACK_SECONDS}s progress={PROGRESS_SECONDS}s "
        f"failover={T_SECONDS}s roster={ROSTER}",
        flush=True,
    )
    try:
        tail = _ChannelTail(CHANNEL, start_at_end=True)
    except FileNotFoundError:
        print(f"[failover] no existe {CHANNEL}", flush=True)
        return
    pending = {}
    activity = {"last_responder_ts": 0.0}
    seen_ids: set[str] = set()
    seen_order: list[str] = []

    try:
        while True:
            try:
                lines, events = tail.poll()
            except Exception as exc:
                print(f"[failover] tail error: {type(exc).__name__}", flush=True)
                lines, events = [], []
            for event in events:
                opened = os.fstat(tail.current.stream.fileno())
                print(
                    f"[failover] canal {event}; dev={opened.st_dev} inode={opened.st_ino}",
                    flush=True,
                )
            for line in lines:
                if not line.strip():
                    continue
                try:
                    message = json.loads(line)
                except Exception:
                    print("[failover] registro JSONL completo pero invalido", flush=True)
                    continue
                raw_id = str(message.get("id") or message.get("idempotency_key") or "")
                if raw_id:
                    if raw_id in seen_ids:
                        continue
                    seen_ids.add(raw_id)
                    seen_order.append(raw_id)
                    if len(seen_order) > 100_000:
                        seen_ids.discard(seen_order.pop(0))
                _process_message(message, pending, activity, time.time())

            process_timeouts(pending, time.time(), activity=activity)
            time.sleep(POLL)
    finally:
        tail.close()


if __name__ == "__main__":
    main()
