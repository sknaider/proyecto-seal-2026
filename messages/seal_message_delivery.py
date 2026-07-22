#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
seal_message_delivery.py — Entrega GARANTIZADA de mensajes (cura #17, NEXUS).
================================================================================
Patrón TRANSACTIONAL OUTBOX + INBOX (Uber/Netflix/Stripe) reimplementado NATIVO
sobre el Postgres que ya corre (no adoptamos cola externa — la mística).

Resuelve por efecto (flood de hoy): el DM se ahogaba (pull+fire-and-forget) y se
duplicaba (anti-eco heurístico por texto+tiempo+buffer). Aquí:
  • OUTBOX persistente (sobrevive restart → cierra la recurrencia-en-reinicio).
  • INBOX por consumidor keyed por msg_id → dedup DETERMINÍSTICO (1 msg = 1 entrega;
    mata la dup de Matrix end-to-end, sin importar flood/timing).
  • prioridad (DMs de William primero) + reintento de no-ackeados (no se pierde bajo flood).
  • push nativo vía pg_notify (la versión SEAL de 'Channels' de Anthropic).

SEPARACIÓN (contrato con chat_server de JARVIS): este módulo = DATOS + LÓGICA
(persistencia/dedup/selección-de-retry). chat_server = TRANSPORTE (broadcast/inject,
dueño del WebSocket) y llama estas funciones. Fail-safe: si la BD falla, las funciones
elevan — el caller decide degradar (chat_server mantiene su broadcast directo como hoy).

NO toca el flujo vivo: solo agrega tablas (CREATE IF NOT EXISTS) y funciones.
"""
from __future__ import annotations
import json
import time

# ── DDL (idempotente, no-destructivo) ─────────────────────────────────────────
DDL = """
CREATE TABLE IF NOT EXISTS soul_v3.message_outbox (
    id          BIGSERIAL PRIMARY KEY,
    msg_id      TEXT UNIQUE NOT NULL,
    to_agent    TEXT NOT NULL,
    channel     TEXT NOT NULL,
    payload     JSONB NOT NULL,
    priority    INT  NOT NULL DEFAULT 5,        -- 1=máx (DM William), 5=normal
    status      TEXT NOT NULL DEFAULT 'pending',-- pending|delivered|read
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivered_at TIMESTAMPTZ,
    read_at     TIMESTAMPTZ,
    attempts    INT NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_outbox_redeliver
    ON soul_v3.message_outbox (status, priority, created_at)
    WHERE status <> 'read';

CREATE TABLE IF NOT EXISTS soul_v3.message_inbox (
    consumer     TEXT NOT NULL,
    msg_id       TEXT NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (consumer, msg_id)
);
"""

NOTIFY_CHANNEL = "seal_delivery"
DEFAULT_RETRY_SEC = 20          # re-inyectar no-ackeado tras esto
MAX_ATTEMPTS = 8                # backstop anti-loop


def _new_msg_id(to_agent: str, seq: int) -> str:
    # determinístico-ish sin Date.now (el caller pasa seq o usa el del outbox)
    return f"out_{to_agent.lower()}_{seq}"


# ── SCOPE de la garantía (hallazgos por efecto: dead-letter net + FABLE, 2026-06-25) ──
# El outbox garantiza SOLO los DMs (dm:*), de CUALQUIER sender (William, Henry, agentes).
# Los broadcasts/heartbeats/topic (web_chat, latidos, system, general, topic:*…) son
# best-effort para TODOS — incluido William: si se guarantee-trackearan, nadie ackea el
# chatter → se acumulan (dead-letters en ack-enabled, pending eterno en el resto).
#
# CLAVE = el CANAL (dm:), NO el sender/prioridad. Por qué (gap condicional de FABLE):
#  • La versión previa garantizaba también priority<=1 (William) → 2 defectos:
#     (a) HENRY (prio 2) quedaba garantizado SOLO si el msg traía canal dm: → si la UI
#         mandaba el DM channel-less, el DM tipeado de Henry NO entraba → se perdía.
#     (b) los mensajes NO-DM de William (web_chat) quedaban force-garantizados → se
#         acumulaban como dead-letters (William tampoco ackea su propio chatter).
#  • Clavando en dm: ambos se cierran: TODO DM se garantiza (Henry incluido), y NINGÚN
#    broadcast se garantiza (William incluido). La PRIORIDAD no decide garantía: es solo
#    el ORDEN de re-entrega (pending_for_redelivery ORDER BY priority): William(1) antes
#    que Henry(2) antes que agentes(5).
# Requisito de ingress (carril chat_server/UI): un DM DEBE llevar canal dm:<a>:<b>
# (es lo que lo hace un DM). Normalizar ahí garantiza que needs_guarantee lo capture.


def needs_guarantee(channel: str, priority: int = 5) -> bool:
    """¿Entrega GARANTIZADA (outbox + ack + re-entrega)? True SOLO si el canal es DM
    (dm:*, case-insensitive) — todos los DMs, sin importar el sender. Broadcasts/
    heartbeats/topic = best-effort (False) para todos. `priority` NO decide la garantía
    (es solo orden de re-entrega); se mantiene en la firma por compat con el caller."""
    return (channel or "").lower().startswith("dm:")


async def ensure_schema(conn) -> None:
    """Crea las tablas si no existen (no-destructivo)."""
    await conn.execute(DDL)


async def enqueue(conn, to_agent: str, channel: str, payload: dict,
                  priority: int = 5, msg_id: str | None = None) -> str:
    """Persiste el mensaje en el outbox y dispara pg_notify. Devuelve msg_id.
    Idempotente por msg_id: si ya existe, no duplica (ON CONFLICT DO NOTHING)."""
    if msg_id is None:
        seq = await conn.fetchval("SELECT nextval('soul_v3.message_outbox_id_seq')")
        msg_id = _new_msg_id(to_agent, seq)
    await conn.execute(
        """INSERT INTO soul_v3.message_outbox (msg_id, to_agent, channel, payload, priority)
           VALUES ($1,$2,$3,$4::jsonb,$5) ON CONFLICT (msg_id) DO NOTHING""",
        msg_id, to_agent, channel, json.dumps(payload), priority,
    )
    await conn.execute(f"SELECT pg_notify('{NOTIFY_CHANNEL}', $1)", msg_id)
    return msg_id


async def pending_for_redelivery(conn, retry_after_sec: int = DEFAULT_RETRY_SEC,
                                 limit: int = 100) -> list:
    """No-ackeados (status<>'read') que nunca se entregaron o cuya entrega venció
    el umbral de retry. Ordenados por PRIORIDAD (DM William primero) y antigüedad.
    Excluye los que agotaron MAX_ATTEMPTS (backstop)."""
    return await conn.fetch(
        """SELECT msg_id, to_agent, channel, payload, priority, attempts
           FROM soul_v3.message_outbox
           WHERE status <> 'read' AND attempts < $2
             AND (delivered_at IS NULL OR delivered_at < now() - make_interval(secs => $1))
           ORDER BY priority ASC, created_at ASC
           LIMIT $3""",
        retry_after_sec, MAX_ATTEMPTS, limit,
    )


async def mark_delivered(conn, msg_id: str) -> None:
    await conn.execute(
        """UPDATE soul_v3.message_outbox
           SET status = CASE WHEN status='read' THEN status ELSE 'delivered' END,
               delivered_at = now(), attempts = attempts + 1
           WHERE msg_id = $1""", msg_id)


async def mark_read(conn, msg_id: str) -> None:
    await conn.execute(
        "UPDATE soul_v3.message_outbox SET status='read', read_at=now() WHERE msg_id=$1",
        msg_id)


async def dead_letters(conn, limit: int = 100) -> list:
    """DEAD-LETTER: mensajes ENTREGADOS pero NUNCA leídos tras agotar MAX_ATTEMPTS.
    El backstop anti-amplificación (attempts<MAX_ATTEMPTS en pending_for_redelivery)
    hace que estos dejen de re-pushearse → si no se vigilan, mueren en silencio.
    Este chequeo los EXPONE para salud/NEXUS (nada se pierde calladamente).
    Devuelve [] cuando no hay → sano."""
    return await conn.fetch(
        """SELECT msg_id, to_agent, channel, priority, attempts, created_at, delivered_at
           FROM soul_v3.message_outbox
           WHERE status <> 'read' AND attempts >= $1
           ORDER BY priority ASC, created_at ASC
           LIMIT $2""",
        MAX_ATTEMPTS, limit,
    )


async def is_duplicate(conn, consumer: str, msg_id: str) -> bool:
    """True si este consumidor YA procesó este msg_id (idempotencia/dedup)."""
    row = await conn.fetchval(
        "SELECT 1 FROM soul_v3.message_inbox WHERE consumer=$1 AND msg_id=$2",
        consumer, msg_id)
    return row is not None


async def record_processed(conn, consumer: str, msg_id: str) -> bool:
    """Registra que el consumidor procesó el msg_id. Devuelve False si ya estaba
    (dup) — el caller puede usarlo como check-and-set atómico."""
    res = await conn.execute(
        """INSERT INTO soul_v3.message_inbox (consumer, msg_id) VALUES ($1,$2)
           ON CONFLICT (consumer, msg_id) DO NOTHING""", consumer, msg_id)
    return res.endswith("1")   # 'INSERT 0 1' => nuevo; 'INSERT 0 0' => dup


# ── self-test POR EFECTO (contra la BD real, limpia lo suyo) ───────────────────
if __name__ == "__main__":
    import asyncio, importlib.util, sys, os
    spec = importlib.util.spec_from_file_location("fdp", os.path.join(os.path.dirname(__file__), "fable_dm_poller.py"))
    m = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(m)
    except Exception:
        pass
    DSN = getattr(m, "DSN", None) or getattr(m, "PG_DSN", None)
    import asyncpg

    async def main():
        if not DSN:
            print("no DSN — skip live test"); return 0
        c = await asyncpg.connect(DSN)
        ok = True
        try:
            await ensure_schema(c)
            print("1 ensure_schema (CREATE IF NOT EXISTS):", "ok")
            TID = "out_test_selfcheck_zzz"
            await c.execute("DELETE FROM soul_v3.message_outbox WHERE msg_id=$1", TID)
            await c.execute("DELETE FROM soul_v3.message_inbox WHERE msg_id=$1", TID)
            mid = await enqueue(c, "NEXUS", "dm:test", {"t": "hi"}, priority=1, msg_id=TID)
            t2 = (mid == TID); ok &= t2; print("2 enqueue (priority DM):", "ok" if t2 else "FAIL")
            # idempotent enqueue (no duplica)
            await enqueue(c, "NEXUS", "dm:test", {"t": "hi"}, priority=1, msg_id=TID)
            cnt = await c.fetchval("SELECT count(*) FROM soul_v3.message_outbox WHERE msg_id=$1", TID)
            t3 = (cnt == 1); ok &= t3; print("3 enqueue idempotente (1 fila):", "ok" if t3 else f"FAIL {cnt}")
            pend = await pending_for_redelivery(c)
            t4 = any(r["msg_id"] == TID for r in pend); ok &= t4; print("4 pending_for_redelivery lo lista:", "ok" if t4 else "FAIL")
            await mark_delivered(c, TID)
            new1 = await record_processed(c, "NEXUS", TID)
            dup = await record_processed(c, "NEXUS", TID)
            t5 = (new1 is True and dup is False); ok &= t5
            print("5 dedup (1ra nueva, 2da dup):", "ok" if t5 else f"FAIL {new1}/{dup}")
            t6 = await is_duplicate(c, "NEXUS", TID); ok &= t6; print("6 is_duplicate:", "ok" if t6 else "FAIL")
            await mark_read(c, TID)
            st = await c.fetchval("SELECT status FROM soul_v3.message_outbox WHERE msg_id=$1", TID)
            t7 = (st == "read"); ok &= t7; print("7 mark_read:", "ok" if t7 else f"FAIL {st}")
            # limpieza del test
            await c.execute("DELETE FROM soul_v3.message_outbox WHERE msg_id=$1", TID)
            await c.execute("DELETE FROM soul_v3.message_inbox WHERE msg_id=$1", TID)
            print("\n", "✅ DELIVERY (outbox+inbox+dedup+priority+retry) OK — por efecto" if ok else "⚠️ revisar")
        finally:
            await c.close()
        return 0 if ok else 1
    sys.exit(asyncio.run(main()))
