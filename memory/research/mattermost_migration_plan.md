# Mattermost Migration Plan — SEAL Chat

**Autor:** ALICE  
**Fecha:** 2026-04-19  
**Estado:** DRAFT — Pendiente deploy Mattermost por ADA/JARVIS

---

## 1. Schema actual → Schema Mattermost

### SEAL `chat_messages` (PostgreSQL actual)
```
id           SERIAL PRIMARY KEY
sender_id    INTEGER (FK chat_users, nullable)
sender_name  TEXT    — "ALICE", "William", "ADA"...
sender_type  TEXT    — "agent" | "user" | "human"
channel      TEXT    — "web_chat", "dm:alice:william"
message_type TEXT    — "text" | "conversation" | "coordination"...
content      TEXT
metadata     JSONB   — {to, legacy_id, model}
reply_to     INTEGER (FK self)
created_at   TIMESTAMPTZ
```

### Mattermost target tables (PostgreSQL)
```
Teams:    id, name, display_name, type("O"=open)
Channels: id, team_id, name, display_name, type("O"=public|"D"=direct)
Users:    id, username, email, roles
Posts:    id, channel_id, user_id, message, create_at (unix ms),
          type, props (JSON), root_id (for threads), parent_id
```

---

## 2. Mapeo de canales

| SEAL channel | Mattermost type | Nombre sugerido |
|---|---|---|
| `web_chat` | O (public) | `seal-team` |
| `dm:alice:william` | D (direct) | auto por Mattermost |
| `dm:ada:william` | D (direct) | auto |
| `dm:jarvis:william` | D (direct) | auto |
| `dm:ada:alice` | D (direct) | auto |
| `dum` | O (public) | `dum-monitor` |

---

## 3. Mapeo de usuarios

| SEAL sender_name | Mattermost username | Rol |
|---|---|---|
| William | william | system_admin |
| JARVIS | jarvis-seal | member |
| ADA | ada-seal | member |
| ALICE | alice-seal | member |
| DUM | dum-seal | member |
| Henry | henry-kinger | member |

---

## 4. Script de migración

Ruta: `/home/dadito/IA/proyecto-seal/scripts/migrate_to_mattermost.py`

### Prerequisitos
- Mattermost corriendo en :8080
- `mmctl` o Mattermost REST API disponible
- Token admin configurado: `export MM_TOKEN=<token>`

### Pasos del script

```python
#!/usr/bin/env python3
"""
migrate_to_mattermost.py — Migra historial SEAL chat → Mattermost

Uso: python3 migrate_to_mattermost.py --dry-run
     python3 migrate_to_mattermost.py --execute
"""
import asyncpg, asyncio, httpx, json, os, time
from datetime import datetime, timezone

# Config
PG_DSN = os.getenv("SEAL_PG_DSN",
    "postgresql://seal_admin:REDACTADO@localhost:5433/soul_memory")
MM_URL = os.getenv("MM_URL", "http://localhost:8080")
MM_TOKEN = os.getenv("MM_TOKEN", "")  # Admin token de Mattermost
MM_TEAM = "seal-team"

# Mapeo sender_name → Mattermost user_id (se puebla al inicio)
USER_MAP: dict[str, str] = {}
CHANNEL_MAP: dict[str, str] = {}  # SEAL channel → MM channel_id

async def main(dry_run: bool = True):
    conn = await asyncpg.connect(PG_DSN)
    headers = {"Authorization": f"Bearer {MM_TOKEN}",
               "Content-Type": "application/json"}
    
    async with httpx.AsyncClient(base_url=MM_URL, headers=headers) as mm:
        # 1. Obtener team_id
        r = await mm.get(f"/api/v4/teams/name/{MM_TEAM}")
        team_id = r.json()["id"]
        
        # 2. Mapear usuarios
        for name in ["william", "jarvis-seal", "ada-seal", "alice-seal",
                     "dum-seal", "henry-kinger"]:
            r = await mm.get(f"/api/v4/users/username/{name}")
            if r.status_code == 200:
                USER_MAP[name.replace("-seal","").upper()] = r.json()["id"]
                USER_MAP[name.upper()] = r.json()["id"]
        
        # 3. Migrar mensajes (excluyendo DMs en primera fase)
        rows = await conn.fetch("""
            SELECT id, sender_name, sender_type, channel, message_type,
                   content, metadata, reply_to, created_at
            FROM chat_messages
            WHERE channel NOT LIKE 'dm:%'
            ORDER BY created_at ASC
        """)
        
        print(f"[MIGRATE] {len(rows)} mensajes a migrar")
        migrated = 0
        skipped = 0
        
        for row in rows:
            sender = row["sender_name"].upper()
            user_id = USER_MAP.get(sender, USER_MAP.get("WILLIAM", ""))
            if not user_id:
                skipped += 1
                continue
            
            # Mapear canal SEAL → canal MM
            seal_ch = row["channel"] or "web_chat"
            mm_channel_id = CHANNEL_MAP.get(seal_ch)
            if not mm_channel_id:
                skipped += 1
                continue
            
            # Timestamp en Unix ms
            ts = row["created_at"]
            if hasattr(ts, "timestamp"):
                create_at = int(ts.timestamp() * 1000)
            else:
                create_at = int(time.time() * 1000)
            
            # Metadata como props
            meta = row["metadata"] or {}
            if isinstance(meta, str):
                meta = json.loads(meta)
            msg_type = row["message_type"] or "text"
            props = {"seal_type": msg_type, **meta}
            
            post = {
                "channel_id": mm_channel_id,
                "user_id": user_id,
                "message": row["content"] or "",
                "create_at": create_at,
                "props": props,
            }
            
            if dry_run:
                print(f"[DRY] {sender}: {row['content'][:60]}")
                migrated += 1
                continue
            
            r = await mm.post("/api/v4/posts", json=post)
            if r.status_code == 201:
                migrated += 1
            else:
                print(f"[ERROR] Post failed: {r.status_code} — {r.text[:100]}")
                skipped += 1
        
        print(f"[MIGRATE] Done: {migrated} migrados, {skipped} omitidos")
    
    await conn.close()

if __name__ == "__main__":
    import sys
    dry = "--dry-run" in sys.argv or "--execute" not in sys.argv
    asyncio.run(main(dry_run=dry))
```

---

## 5. Plugins SEAL custom (fase 2)

Prioridad post-deploy:

| Plugin | Función | Tecnología |
|---|---|---|
| `seal-identity` | Badge de agente + modelo en mensajes | Go / webapp |
| `seal-soul` | Comandos `/memory`, `/reflect`, `/who` | Python webhook |
| `seal-mcp` | Relay MCP tools desde chat | Python webhook |
| `seal-dm-e2e` | Cifrado E2E en DMs (ya tenemos PGP en JSONL) | Go |

---

## 6. Cronograma

| Fase | Tarea | Owner | ETA |
|---|---|---|---|
| Deploy | docker-compose Mattermost :8080 | ADA/JARVIS | Hoy |
| Config | Crear team + canales + usuarios | ADA | +30min |
| Migración | Ejecutar migrate_to_mattermost.py --dry-run | ALICE | Tras deploy |
| Validación | Verificar 100 mensajes muestra | ALICE | +15min |
| Cutover | Redirect :3001 → Mattermost | JARVIS | Tras validación |
| Plugins | seal-identity badge | JARVIS | Sprint siguiente |

---

## 7. Riesgos y mitigaciones

| Riesgo | Probabilidad | Mitigación |
|---|---|---|
| Mattermost API rate limit en migración | M | Batch con sleep(0.1) entre posts |
| IDs de usuario no creados en MM | A | Crear usuarios via API antes de migrar |
| DMs: mapeo complejo | A | Migrar DMs en fase 2 separada |
| Historial > 100K msgs | B | Migrar últimos 30 días primero |
| Plugins Go: tiempo de desarrollo | A | Python webhooks como MVP primero |

---

*ALICE — Team SEAL — 2026-04-19*
