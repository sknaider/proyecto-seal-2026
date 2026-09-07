#!/usr/bin/env python3
"""FABLE DM Poller — polls DB for new DM messages from William to FABLE.
Writes to fable_inbox.jsonl (private). FABLE tails this file via Monitor.
Cableado por JARVIS (orden William 13-jun): paridad de DM para FABLE (hermano adoptado)."""
import asyncio
import asyncpg
import os
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

DSN = os.environ.get("SEAL_POLLER_DSN", "").strip()
if not DSN:
    raise RuntimeError("SEAL_POLLER_DSN is required; shared DB fallback is forbidden")
FABLE_INBOX = Path("/home/dadito/IA/proyecto-seal/messages/fable_inbox.jsonl")
# 31-jul: NO agregar un append a /tmp/seal_events_FABLE.log. Lo puse y lo saque
# el mismo dia, con la matriz que enuncio JARVIS:
#
#   sin append + 1 tail   CIEGO a DM de humanos   <- era el bug de 6 semanas
#   sin append + 2 tails  correcto                <- ESTE asiento
#   con append + 1 tail   correcto
#   con append + 2 tails  DUPLICA                 <- lo que yo causé
#
# Mi arranque arma DOS monitores (canal + fable_inbox.jsonl), asi que el poller
# escribiendo al inbox YA me surfacea los DM. El append los metia ademas en el
# feed del canal y me llegaban por los dos. El arreglo real fue el rol de DB
# (login_poller_fable), no esto.
# EL CURSOR NO PUEDE VIVIR EN /tmp (NEXUS, 30-jul-2026).
# `D /tmp` + `systemd-tmpfiles --create --remove --boot` vacian /tmp EN CADA BOOT.
# Sin cursor, load_last_ts() devolvia "ahora menos 5 min" => todo DM llegado con la
# maquina abajo se descartaba PARA SIEMPRE y sin una sola señal. Medido en el asiento
# de NEXUS: 3 entrantes que calificaban y nunca entraron. Mismo defecto que JARVIS
# hallo en los recibos de ADA, otro artefacto, el mismo dia.
_DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
STATE_FILE = _DATA_HOME / "seal" / "fable_dm_last_ts.txt"
POLL_INTERVAL = 2
# LATIDO (diseño de JARVIS, 30-jul). Su poller estuvo 14 dias sin entregar y no
# habia forma de notarlo: `active`, sin errores, log mudo. "Nada que entregar" y
# "no funciono" producian la MISMA señal: ninguna. El latido las separa porque
# `polls` sube solo si el fetch volvio sin excepcion.
HEARTBEAT_EVERY = 900  # segundos  # seconds


def load_last_ts() -> datetime:
    if STATE_FILE.exists():
        return datetime.fromisoformat(STATE_FILE.read_text().strip())
    return datetime.now(timezone.utc) - timedelta(minutes=5)


def save_last_ts(ts: datetime):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(ts.isoformat())


async def poll_dm():
    # COTA AL FETCH (NEXUS, 30-jul). `asyncpg.connect` trae command_timeout=None:
    # esperar SIN LIMITE. Con el socket medio abierto, `await conn.fetch(...)` no
    # vuelve nunca: proceso vivo, unidad active, cero lineas, cero entregas.
    # Diferencial de JARVIS: SELECT 1 -> 1,0 ms; pg_sleep(7) -> TimeoutError a 5 s.
    # El latido solo AVISA del cuelgue; la cota lo IMPIDE. Hacen falta los dos.
    # 5 s y no 30: JARVIS midio la consulta REAL del poller en los cinco
    # asientos -- peor caso de la flota 2,65 ms, 8 corridas. Margen ~1.900x.
    # Mi 30 lo habia elegido a dedo; este sale de medir el sujeto.
    conn = await asyncpg.connect(DSN, command_timeout=5)
    last_ts = load_last_ts()
    # GUARDAR AL ARRANCAR, no solo al entregar (NEXUS, 30-jul).
    # `save_last_ts` corria unicamente al recibir un mensaje, asi que un asiento
    # SIN trafico nunca creaba el cursor: existia solo si ya habias recibido algo.
    # El asiento callado -- el que mas necesita no perder el proximo -- arrancaba
    # siempre en frio. Con esto el cursor existe desde el primer segundo.
    save_last_ts(last_ts)
    polls = 0
    entregados = 0
    ultimo_latido = time.monotonic()
    print(f"[FABLE-DM] Poller iniciado, desde {last_ts}", flush=True)

    while True:
        try:
            rows = await conn.fetch(
                """SELECT sender_name, content, created_at, channel, message_type
                   FROM chat_messages
                   -- PUENTE TEMPORAL (JARVIS 31-jul-2026, opcion B de William).
                   -- Antes: dos listas FIJAS -> solo William y Henry existian.
                   --   1) canal: lista cerrada de 4 (o prefijo, que pierde la
                   --      mitad: 'dm:ada:{ag}' tiene el nombre del lado DERECHO)
                   --   2) remitente: allow-list de 2 humanos
                   -- Una lista de PERMITIDOS deja afuera a todo el que no exista
                   -- todavia; una EXCLUSION solo saca al que no corresponde.
                   --
                   -- RETIRO: este puente mezcla los usuarios en la misma cabeza.
                   -- Se retira cuando exista la instancia por usuario (el clon).
                   -- Ver docs/specs/SPEC_CLON_POR_USUARIO_V1.md
                   WHERE (channel LIKE 'dm:fable:%' OR channel LIKE 'dm:%:fable')
                     AND created_at > $1
                     AND LOWER(sender_name) NOT IN ('ada','alice','fable','jarvis','nexus','dum')
                   ORDER BY created_at ASC""",
                last_ts
            )
            for r in rows:
                msg = {
                    "id": f"dm_{int(r['created_at'].timestamp() * 1e9)}",
                    "from": r["sender_name"],
                    "to": "FABLE",
                    "timestamp": r["created_at"].astimezone(timezone.utc).isoformat(),
                    "type": "dm",
                    "message": r["content"],
                    "channel": r["channel"],
                }
                with open(FABLE_INBOX, "a", encoding="utf-8") as f:
                    f.write(json.dumps(msg, ensure_ascii=False) + "\n")
                last_ts = r["created_at"].astimezone(timezone.utc)
                print(f"[FABLE-DM] William: {r['content'][:80]}", flush=True)
                entregados += 1
            if rows:
                save_last_ts(last_ts)
            polls += 1
            if time.monotonic() - ultimo_latido >= HEARTBEAT_EVERY:
                print(f"[FABLE-DM] latido: polls={polls} entregados={entregados} "
                      f"cursor={last_ts.isoformat()}", flush=True)
                ultimo_latido = time.monotonic()
        except Exception as e:
            print(f"[FABLE-DM] error: {e}", flush=True)
            try:
                conn = await asyncpg.connect(DSN, command_timeout=5)
            except Exception as _e2:
                # `pass` hacia que el UNICO camino de recuperacion
                # fallara en silencio (hallazgo de JARVIS, 30-jul).
                print(f"[FABLE-DM] RECONEXION FALLIDA: {type(_e2).__name__}: {_e2}", flush=True)
        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(poll_dm())
