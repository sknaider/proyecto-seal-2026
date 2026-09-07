"""
chat_db.py — SEAL Chat Pro async database layer.

Uses asyncpg for high-performance PostgreSQL access.
Provides CRUD operations for users, sessions, messages, and channels.

Usage:
    from chat_db import ChatDB

    db = ChatDB()
    await db.init()  # creates connection pool
    messages = await db.get_messages("general", limit=50)
    await db.close()
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
LIMA_TZ = ZoneInfo("America/Lima")
from typing import Any, Optional
from uuid import UUID

import asyncpg

# ── Config ──────────────────────────────────────────────────────────────────

PG_DSN = os.environ.get("SEAL_PG_DSN", "").strip()
POOL_MIN = 2
POOL_MAX = 10
TOKEN_EXPIRY_HOURS = 72  # JWT session duration
MAX_SESSION_AGE = timedelta(days=30)  # absolute cap on sliding renewal (ADA, 2026-07-17: bound stolen-token replay)

# Delete and demote must share the same transaction-scoped lock.  Row locks on
# different superusers are insufficient: DELETE(A) racing DEMOTE(B) could see
# two owners and commit both operations, leaving the installation ownerless.
_SUPERUSER_GUARD_LOCK = 0x5345414C53555052  # "SEALSUPR", signed bigint-safe


def dm_participants(channel: str | None, *, sender_hint: str | None = None) -> tuple[str, str] | None:
    """Parse an exact two-party DM without substring/display-name matching.

    Canonical channels are ``dm:<left>:<right>``. A bounded legacy ``dm:a-b``
    form remains readable only when ``sender_hint`` exactly identifies one
    endpoint; this avoids splitting legitimate hyphenated usernames by guess.
    """

    raw = str(channel or "")
    if not raw.lower().startswith("dm:"):
        return None
    body = raw[3:]
    parts = body.split(":")
    if len(parts) == 2 and all(parts):
        return parts[0], parts[1]
    if ":" in body or not sender_hint:
        return None
    sender = str(sender_hint).strip()
    body_l = body.lower()
    sender_l = sender.lower()
    if body_l.startswith(sender_l + "-") and len(body) > len(sender) + 1:
        return body[:len(sender)], body[len(sender) + 1:]
    if body_l.endswith("-" + sender_l) and len(body) > len(sender) + 1:
        return body[:-(len(sender) + 1)], body[-len(sender):]
    return None


# ── Password hashing (PBKDF2-SHA256, no external deps) ─────────────────────

def hash_password(password: str) -> str:
    """Hash password with PBKDF2-SHA256 + random salt."""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000)
    return f"pbkdf2:sha256:260000${salt}${dk.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    """Verify password against stored PBKDF2 hash."""
    try:
        parts = password_hash.split("$")
        if len(parts) != 3:
            return False
        header, salt, stored_hash = parts
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000)
        return secrets.compare_digest(dk.hex(), stored_hash)
    except Exception:
        return False


# ── Database class ──────────────────────────────────────────────────────────

class LastSuperuserError(RuntimeError):
    """Se intento degradar al ULTIMO superuser: dejaria el sistema sin dueño.

    Excepcion propia y no un `bool`/`None`: el que llama no puede confundirla con
    «no se pudo» ni ignorarla por descuido. Hoy hay UN solo superuser, o sea que un
    unico UPDATE mal hecho deja el sistema sin quien lo repare -- y ese es el unico
    estado de esta ruta que NO se arregla desde la propia app.
    """


class ChatDB:
    """Async PostgreSQL interface for SEAL Chat Pro."""

    def __init__(self, dsn: str | None = None):
        self.dsn = (dsn or PG_DSN).strip()
        if not self.dsn:
            raise RuntimeError("SEAL_PG_DSN is required; privileged fallback is forbidden")
        self.pool: Optional[asyncpg.Pool] = None
        self.admin_dsn = os.environ.get("SEAL_PG_ADMIN_DSN", "").strip()
        self.admin_pool: Optional[asyncpg.Pool] = None

    async def init(self) -> None:
        """Initialize connection pool."""
        self.pool = await asyncpg.create_pool(
            self.dsn, min_size=POOL_MIN, max_size=POOL_MAX,
        )
        if self.admin_dsn:
            self.admin_pool = await asyncpg.create_pool(
                self.admin_dsn, min_size=1, max_size=2,
            )

    async def close(self) -> None:
        """Close connection pool."""
        if self.admin_pool:
            await self.admin_pool.close()
        if self.pool:
            await self.pool.close()

    # ── Users ───────────────────────────────────────────────────────────────

    async def create_user(
        self,
        username: str,
        display_name: str,
        password: str,
        role: str = "user",
        avatar_url: str | None = None,
    ) -> dict:
        """Create a new chat user. Returns user dict."""
        pw_hash = hash_password(password)
        row = await self.pool.fetchrow(
            """INSERT INTO chat_users (username, display_name, password_hash, role, avatar_url)
               VALUES ($1, $2, $3, $4, $5)
               RETURNING id, username, display_name, role, avatar_url, created_at""",
            username, display_name, pw_hash, role, avatar_url,
        )
        return dict(row)

    async def update_user_role(self, user_id: int, nuevo_rol: str, rol_anterior: str,
                               actor: str, motivo: str = "", *, actor_user_id: int,
                               session_token_hash: str) -> dict:
        """Cambia el rol de un usuario y DEJA EL RASTRO, en UNA sola transacción.

        Pedido de William (31-jul-2026): *«creen para que yo tenga opción de cambiar su rol a
        katy»*. No existía ni endpoint ni pantalla: el único camino era un `UPDATE` a mano.

        **El `INSERT` de auditoría va DENTRO de la misma transacción que el `UPDATE`, a
        propósito.** Si fuera un paso aparte y fallara, quedaría un rol cambiado sin registro —
        y un rol cambiado sin registro **no se distingue de uno que nunca cambió**. Con esto, o
        pasan las dos cosas o no pasa ninguna. Lo probé a mano ese mismo día: el primer intento
        falló por un argumento de más y la transacción dejó a la usuaria intacta, sin quedar a
        medio camino.

        El rastro va a `soul_v3.soul_audit_log`, que **ya existía** con la forma exacta
        (`old_value`/`new_value`/`role_used`) y tenía 2 filas en dos meses. `operation` sólo
        acepta `INSERT|UPDATE|DELETE|SELECT`: el campo que cambió va en el JSON, no en el verbo.

        `hmac_signature` queda NULL: no hay firma disponible acá, y **una fila sin firmar prueba
        que alguien anotó el cambio, no que la anotación no se pueda alterar después.** Está
        declarado para que nadie lo lea como más de lo que es.
        """
        executor = self.admin_pool
        if executor is None:
            raise RuntimeError("privileged user-management pool unavailable")
        result = await executor.fetchval(
            "SELECT soul_v3.user_role_update_guarded($1,$2,$3,$4,$5)",
            user_id, nuevo_rol, actor_user_id, session_token_hash, motivo,
        )
        if isinstance(result, str):
            result = json.loads(result)
        if not isinstance(result, dict):
            raise RuntimeError("invalid guarded role-update response")
        if result.get("error") == "last_superuser":
            raise LastSuperuserError(
                "es el ultimo superuser: promove a otro antes de degradarlo"
            )
        if not result.get("ok"):
            raise ValueError(f"usuario {user_id} no existe")
        return dict(result["user"])

    async def get_user_by_username(self, username: str) -> dict | None:
        """Fetch user by username (case-insensitive)."""
        row = await self.pool.fetchrow(
            "SELECT * FROM chat_users WHERE LOWER(username) = LOWER($1)", username,
        )
        return dict(row) if row else None

    async def get_user_by_id(self, user_id: int) -> dict | None:
        """Fetch user by ID."""
        row = await self.pool.fetchrow(
            "SELECT id, username, display_name, role, avatar_url, created_at, last_seen "
            "FROM chat_users WHERE id = $1", user_id,
        )
        return dict(row) if row else None

    async def authenticate(self, username: str, password: str) -> dict | None:
        """Verify credentials. Returns user dict (without password_hash) or None."""
        user = await self.get_user_by_username(username)
        if not user:
            return None
        if not verify_password(password, user["password_hash"]):
            return None
        # Update last_seen
        await self.pool.execute(
            "UPDATE chat_users SET last_seen = NOW() WHERE id = $1", user["id"],
        )
        # Remove sensitive field
        user.pop("password_hash", None)
        return user

    async def update_last_seen(self, user_id: int) -> None:
        """Touch user's last_seen timestamp."""
        await self.pool.execute(
            "UPDATE chat_users SET last_seen = NOW() WHERE id = $1", user_id,
        )

    async def list_users(self) -> list[dict]:
        """List all users (sin password_hash). Para el panel admin (cura RBAC NEXUS 13-jun)."""
        rows = await self.pool.fetch(
            "SELECT id, username, display_name, role, avatar_url, created_at, last_seen "
            "FROM chat_users ORDER BY username",
        )
        return [dict(r) for r in rows]

    async def delete_user(
        self, username: str, *, actor_user_id: int, session_token_hash: str
    ) -> bool:
        """Delete a user and revoke sessions without deleting the last owner.

        The advisory lock is intentionally identical to ``update_user_role``.
        It serializes DELETE-vs-DELETE and DELETE-vs-DEMOTE, including targets
        on different rows, before either path counts the remaining owners.
        """
        executor = self.admin_pool
        if executor is None:
            raise RuntimeError("privileged user-management pool unavailable")
        deleted = await executor.fetchval(
            "SELECT soul_v3.user_delete_with_sessions($1,$2,$3)",
            username, actor_user_id, session_token_hash,
        )
        if int(deleted or 0) == -1:
            raise LastSuperuserError(
                "es el ultimo superuser: promove a otro antes de borrarlo"
            )
        return int(deleted or 0) == 1

    # ── User ↔ Agent assignment (Opción A, orden William 17-jul) ─────────────
    async def get_user_agents(self, user_id: int) -> list[str]:
        """Agentes que este usuario puede usar. Lista de nombres (vacía = ninguno asignado)."""
        rows = await self.pool.fetch(
            "SELECT agent FROM user_agents WHERE user_id = $1 ORDER BY agent", user_id,
        )
        return [r["agent"] for r in rows]

    async def get_user_agent_policy(self, user_id: int) -> dict:
        """Return explicit assignment state; absence means legacy/unconfigured."""
        row = await self.pool.fetchrow(
            "SELECT configured, granted_by, updated_at FROM user_agent_policies WHERE user_id = $1",
            user_id,
        )
        return dict(row) if row else {"configured": False, "granted_by": None, "updated_at": None}

    async def set_user_agents(self, user_id: int, agents: list[str], granted_by: str) -> list[str]:
        """Reemplaza el conjunto de agentes del usuario por `agents` (transaccional).
        Devuelve la lista final persistida. Idempotente: setear la misma lista no falla."""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM user_agents WHERE user_id = $1", user_id)
                for agent in dict.fromkeys(a.strip() for a in agents if a and a.strip()):
                    await conn.execute(
                        "INSERT INTO user_agents (user_id, agent, granted_by) VALUES ($1, $2, $3)",
                        user_id, agent, granted_by,
                    )
                await conn.execute(
                    """INSERT INTO user_agent_policies (user_id, configured, granted_by, updated_at)
                       VALUES ($1, true, $2, NOW())
                       ON CONFLICT (user_id) DO UPDATE SET
                         configured=true, granted_by=EXCLUDED.granted_by, updated_at=NOW()""",
                    user_id, granted_by,
                )
                rows = await conn.fetch(
                    "SELECT agent FROM user_agents WHERE user_id = $1 ORDER BY agent", user_id,
                )
        return [r["agent"] for r in rows]

    # ── Sessions ────────────────────────────────────────────────────────────

    async def create_session(
        self, user_id: int, token_hash: str,
        ip_address: str | None = None, user_agent: str | None = None,
    ) -> dict:
        """Create a new session. Returns session dict."""
        expires = datetime.now(LIMA_TZ) + timedelta(hours=TOKEN_EXPIRY_HOURS)
        row = await self.pool.fetchrow(
            """INSERT INTO chat_sessions (user_id, token_hash, expires_at, ip_address, user_agent)
               VALUES ($1, $2, $3, $4::inet, $5)
               RETURNING id, user_id, created_at, expires_at""",
            user_id, token_hash, expires, ip_address, user_agent,
        )
        return dict(row)

    async def validate_session(self, token_hash: str) -> dict | None:
        """Validate session by token hash. Returns user dict or None.

        Sliding renewal (William, 2026-07-17: "estas sesiones no deben vencer"):
        an active session — one still in use within its expiry window — gets its
        expires_at pushed out another TOKEN_EXPIRY_HOURS whenever less than a
        quarter of its window remains, so agents/users who keep talking never
        hit a hard cutoff. A session that goes truly idle past TOKEN_EXPIRY_HOURS
        still expires on schedule, same as before.

        Absolute cap (ADA, 2026-07-17: sliding renewal with no ceiling lets a
        stolen token live forever if it's replayed inside every window; caught
        again that a renewal-only cap still let a >30d session authenticate one
        last time before dying — the cap must reject outright, not just stop
        renewing). Once a session crosses created_at + MAX_SESSION_AGE it fails
        validation immediately (enforced in the WHERE clause itself), forcing
        real re-auth (human re-login / create_agent_sessions.py rotation)
        regardless of how active the token stayed.
        """
        row = await self.pool.fetchrow(
            """SELECT s.id as session_id, s.user_id, s.expires_at, s.created_at,
                      u.username, u.display_name, u.role, u.avatar_url
               FROM chat_sessions s
               JOIN chat_users u ON s.user_id = u.id
               WHERE s.token_hash = $1 AND s.expires_at > NOW()
                     AND s.created_at + $2::interval > NOW()""",
            token_hash, MAX_SESSION_AGE,
        )
        if not row:
            return None
        now = datetime.now(LIMA_TZ)
        renew_threshold = timedelta(hours=TOKEN_EXPIRY_HOURS / 4)
        if row["expires_at"] - now < renew_threshold:
            # The login runtime intentionally has no free-form UPDATE grant on
            # chat_sessions.  Renewal goes through a bounded SECURITY DEFINER
            # function: 18h threshold, 72h slide, hard 30d absolute cap.
            # A direct UPDATE here made every near-expiry authenticated request
            # fail closed with HTTP 503 under the real login_bus role.
            renewed = await self.pool.fetchval(
                "SELECT soul_v3.session_renew_by_token($1)", token_hash,
            )
            if renewed is not None:
                result = dict(row)
                result["expires_at"] = renewed
                return result
        return dict(row)

    async def delete_session(self, token_hash: str) -> None:
        """Invalidate one session through the least-privilege session API."""
        await self.pool.execute(
            "SELECT soul_v3.session_delete_by_token($1)", token_hash,
        )

    async def cleanup_expired_sessions(self) -> int:
        """Remove expired sessions through the least-privilege session API."""
        return int(await self.pool.fetchval(
            "SELECT soul_v3.session_cleanup_expired()"
        ))

    async def limit_user_sessions(self, user_id: int, max_sessions: int = 5) -> int:
        """Keep N sessions through the bounded least-privilege session API."""
        return int(await self.pool.fetchval(
            "SELECT soul_v3.session_limit_by_user($1, $2)",
            user_id, max_sessions,
        ))

    # ── Messages ────────────────────────────────────────────────────────────

    async def create_message(
        self,
        sender_name: str,
        content: str,
        channel: str = "general",
        sender_type: str = "agent",
        sender_id: int | None = None,
        message_type: str = "text",
        metadata: dict | None = None,
        reply_to: int | None = None,
        conn=None,
        actor_user_id: int | None = None,
        actor_role: str | None = None,
        exigir_identidad: bool = True,
    ) -> dict:
        """Store a new message. Returns message dict with id and timestamp.
        Si se pasa `conn`, usa esa conexión (p.ej. una con app.current_identity seteada:
        el INSERT..RETURNING en canales dm:* exige que la fila sea VISIBLE por la política
        SELECT de RLS — sin identidad de sesión la RLS la oculta y el INSERT falla).

        IDENTIDAD DEL ACTOR (JARVIS, 31-jul-2026, embudo cedido por ADA)

        `actor_user_id` y `actor_role` vienen de la SESION AUTENTICADA, nunca del
        payload. Los ocho llamadores de `chat_server.py` ya los pasan (8/8).

        POR QUE SE ACEPTAN HOY Y NO SE EXIGEN TODAVIA:

            los 8 llamadores pasan identidad      <- mitad permisiva, YA desplegada
            este embudo la EXIGE                  <- fail-closed, falta

        El orden importa y hoy nos costo caro dos veces: un fail-closed que entra
        antes que su mitad permisiva no endurece, APAGA. Con `exigir_identidad`
        en False el comportamiento es identico al de antes; cuando se prenda,
        un `sender_type="user"` sin identidad resuelta se rechaza.

        URGENTE AL MOMENTO DE ESCRIBIR ESTO: la firma vieja NO aceptaba estos
        parametros y los 8 llamadores ya los enviaban. El proceso vivo seguia
        con el chat_server anterior, asi que el chat funcionaba -- pero el
        proximo reinicio habria tirado `TypeError` en CADA escritura. Aceptarlos
        es lo que desarma eso; exigirlos es el paso siguiente.
        """
        if exigir_identidad and str(sender_type or "").lower() == "user":
            # Fail-closed: un mensaje de usuario sin identidad resuelta no se
            # escribe. No se "asume" ni se deriva del payload -- eso seria un
            # escalador de privilegios con forma de valor por defecto.
            if actor_user_id is None or not str(actor_role or "").strip():
                raise ValueError(
                    "create_message: sender_type='user' exige actor_user_id y "
                    f"actor_role de la sesion autenticada "
                    f"(recibido user_id={actor_user_id!r} role={actor_role!r})"
                )

        # Rellenar `sender_id` cuando la identidad YA vino resuelta y la columna
        # legada quedo vacia. NO es una inferencia: `actor_user_id` sale de la
        # sesion autenticada, que es la misma persona que `sender_id` describe
        # cuando `sender_type='user'`.
        #
        # POR QUE HACE FALTA (medido por FABLE, confirmado en vivo hoy 19:52):
        #   imagenes con sender_id NULL   784 de 785
        #   subida real de Henry          sender_type=user · sender_id=None
        #                                 CON actor_user_id presente
        # El dato estaba en la llamada y no llegaba a la fila. Todo lo que
        # consulte por `sender_id` -- auditoria, ruteo, "quien escribio esto" --
        # veia NULL y no podia distinguir "no se sabe" de "nadie lo guardo".
        # Un NULL EVITABLE es una ausencia que nadie puede interpretar despues.
        if (
            sender_id is None
            and actor_user_id is not None
            and str(sender_type or "").lower() == "user"
        ):
            sender_id = int(actor_user_id)

        ex = conn if conn is not None else self.pool
        row = await ex.fetchrow(
            """INSERT INTO chat_messages
               (sender_type, sender_id, sender_name, channel, message_type, content, metadata, reply_to)
               VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8)
               RETURNING id, sender_type, sender_id, sender_name, channel,
                         message_type, content, metadata, reply_to, created_at""",
            sender_type, sender_id, sender_name, channel, message_type,
            content, json.dumps(metadata) if metadata else None, reply_to,
        )
        result = dict(row)
        # Convert metadata back from JSON string
        if result.get("metadata") and isinstance(result["metadata"], str):
            result["metadata"] = json.loads(result["metadata"])
        return result

    async def get_messages(
        self,
        channel: str,
        limit: int = 50,
        before_id: int | None = None,
        after_id: int | None = None,
        conn=None,
    ) -> list[dict]:
        """Fetch messages for a channel with cursor-based pagination.
        #19 Fase B: si se pasa `conn` (una conexión RLS-scopeada bajo chat_msg_ro), las lecturas
        de canales privados/DM quedan aisladas por la DB. Si conn=None usa el pool (`seal`, público)."""
        ex = conn if conn is not None else self.pool
        if before_id:
            rows = await ex.fetch(
                """SELECT * FROM chat_messages
                   WHERE channel = $1 AND id < $2
                   ORDER BY created_at DESC LIMIT $3""",
                channel, before_id, limit,
            )
        elif after_id:
            rows = await ex.fetch(
                """SELECT * FROM chat_messages
                   WHERE channel = $1 AND id > $2
                   ORDER BY created_at ASC LIMIT $3""",
                channel, after_id, limit,
            )
        else:
            rows = await ex.fetch(
                """SELECT * FROM chat_messages
                   WHERE channel = $1
                   ORDER BY created_at DESC LIMIT $2""",
                channel, limit,
            )
        return [dict(r) for r in reversed(rows)]  # oldest first

    async def delete_message(self, message_id: int, requester_name: str, is_admin: bool = False) -> bool:
        """Delete a message. Only the sender or an admin can delete. Returns True if deleted."""
        executor = self.admin_pool or self.pool
        if is_admin:
            result = await executor.execute(
                "DELETE FROM chat_messages WHERE id = $1", message_id
            )
        else:
            result = await executor.execute(
                "DELETE FROM chat_messages WHERE id = $1 AND sender_name = $2",
                message_id, requester_name,
            )
        return result == "DELETE 1"

    async def get_dm_messages(
        self, user_a: str, user_b: str, limit: int = 50, before_id: int | None = None,
    ) -> list[dict]:
        """Fetch direct messages between two participants."""
        # DM channel name is always sorted alphabetically, lowercase
        parts = sorted([user_a.lower(), user_b.lower()])
        dm_channel = f"dm:{parts[0]}:{parts[1]}"
        return await self.get_messages(dm_channel, limit, before_id)

    async def search_messages(
        self,
        query: str,
        channel: str | None = None,
        limit: int = 20,
        *,
        conn=None,
        allowed_agents: list[str] | tuple[str, ...] | set[str] | None = None,
        allow_all_agents: bool = False,
    ) -> list[dict]:
        """Search only through a caller-scoped, RLS-enforced connection.

        ``conn`` must be a connection whose transaction has ``SET LOCAL ROLE
        chat_msg_ro`` plus the authenticated user's ``app.current_user_id`` and
        ``app.current_identity``.  Refusing to fall back to the owner pool is
        intentional: the owner bypasses RLS and would leak other users' DMs and
        private ``user:*`` channels in a cross-channel search.

        Non-privileged callers must also pass their assigned agents.  Human and
        system messages remain searchable, while rows explicitly authored by an
        agent are limited to that assignment.  ``allow_all_agents`` is reserved
        for an already-authorized admin/superuser selected by the HTTP layer.
        """
        query = (query or "").strip()
        if conn is None or not query:
            return []

        limit = max(1, min(int(limit), 100))
        params: list[Any] = []
        where: list[str] = []

        if channel:
            params.append(channel)
            where.append(f"channel = ${len(params)}")

        params.append(query)
        where.append(
            "to_tsvector('spanish', COALESCE(content, '')) "
            f"@@ plainto_tsquery('spanish', ${len(params)})"
        )

        if not allow_all_agents:
            if allowed_agents is None:
                return []
            normalized = sorted({str(agent).strip().upper() for agent in allowed_agents if str(agent).strip()})
            params.append(normalized)
            where.append(
                "(sender_type IS DISTINCT FROM 'agent' "
                f"OR UPPER(COALESCE(sender_name, '')) = ANY(${len(params)}::text[]))"
            )

        params.append(limit)
        rows = await conn.fetch(
            f"""SELECT * FROM chat_messages
                WHERE {' AND '.join(where)}
                ORDER BY created_at DESC LIMIT ${len(params)}""",
            *params,
        )
        return [dict(r) for r in rows]

    async def get_message_count(self, channel: str | None = None) -> int:
        """Count messages, optionally by channel."""
        if channel:
            row = await self.pool.fetchrow(
                "SELECT COUNT(*) as cnt FROM chat_messages WHERE channel = $1", channel,
            )
        else:
            row = await self.pool.fetchrow("SELECT COUNT(*) as cnt FROM chat_messages")
        return row["cnt"]

    # ── Channels ────────────────────────────────────────────────────────────

    async def get_channels(self, user_id: int | None = None) -> list[dict]:
        """List channels. If user_id provided, only channels user has access to."""
        if user_id:
            rows = await self.pool.fetch(
                """SELECT c.*, ca.can_read, ca.can_write
                   FROM chat_channels c
                   LEFT JOIN chat_channel_access ca ON c.id = ca.channel_id AND ca.user_id = $1
                   WHERE c.is_private = FALSE
                      OR ca.can_read = TRUE
                   ORDER BY c.name""",
                user_id,
            )
        else:
            rows = await self.pool.fetch(
                "SELECT * FROM chat_channels WHERE is_private = FALSE ORDER BY name",
            )
        return [dict(r) for r in rows]

    async def create_dm_channel(self, user_a: str, user_b: str) -> str:
        """Get or create a DM channel between two participants. Returns channel name."""
        parts = sorted([user_a.lower(), user_b.lower()])
        dm_name = f"dm:{parts[0]}:{parts[1]}"
        await self.pool.execute(
            """INSERT INTO chat_channels (name, description, is_private)
               VALUES ($1, $2, TRUE)
               ON CONFLICT (name) DO NOTHING""",
            dm_name, f"DM between {parts[0]} and {parts[1]}",
        )
        return dm_name

    async def grant_channel_access(
        self, channel_id: int, user_id: int,
        can_read: bool = True, can_write: bool = True,
    ) -> None:
        """Grant a user access to a channel."""
        await self.pool.execute(
            """INSERT INTO chat_channel_access (channel_id, user_id, can_read, can_write)
               VALUES ($1, $2, $3, $4)
               ON CONFLICT (channel_id, user_id)
               DO UPDATE SET can_read = $3, can_write = $4""",
            channel_id, user_id, can_read, can_write,
        )

    async def user_can_access_channel(self, user_id: int, channel: str) -> bool:
        """Check if user can read a channel."""
        # Studio llama "General" al canal canónico ``web_chat``. La tabla
        # histórica chat_channels solo registra ``general`` en algunos nodos,
        # pero los mensajes vivos se persisten con channel='web_chat'. Tratar
        # ambos nombres públicos como aliases explícitos evita que el upload
        # autenticado dé 403 mientras el envío de texto sí funciona. Mantener
        # la lista cerrada: canales desconocidos siguen fail-closed abajo.
        if channel in {"general", "web_chat"}:
            return True

        # DM channels — check if user is a participant (case-insensitive)
        if channel.startswith("dm:"):
            user = await self.get_user_by_id(user_id)
            if user:
                participants = dm_participants(
                    channel, sender_hint=str(user["username"])
                )
                return bool(
                    participants
                    and str(user["username"]).lower()
                    in {part.lower() for part in participants}
                )
            return False

        # Canales topic de usuario user:<uid>:<slug> — el DUEÑO (uid == user_id) accede.
        # Fix (JARVIS 18-jul, coordinado con ADA): el path de upload usa esta función y NO
        # cubría los user:N:* → Henry recibía "Access denied" al subir a su canal gtl-sistemas.
        if channel.startswith("user:"):
            parts = channel.split(":")
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1]) == user_id
            return False

        # Public channels are always readable
        ch = await self.pool.fetchrow(
            "SELECT id, is_private FROM chat_channels WHERE name = $1", channel,
        )
        if not ch:
            return False
        if not ch["is_private"]:
            return True
        # Private channel — check access table
        access = await self.pool.fetchrow(
            "SELECT can_read FROM chat_channel_access WHERE channel_id = $1 AND user_id = $2",
            ch["id"], user_id,
        )
        return bool(access and access["can_read"])

    # ── Bulk import (for JSONL migration) ───────────────────────────────────

    async def bulk_import_messages(self, messages: list[dict]) -> int:
        """Bulk import messages from JSONL migration. Returns count imported."""
        count = 0
        async with self.pool.acquire() as conn:
            # Use a transaction for atomicity
            async with conn.transaction():
                for msg in messages:
                    await conn.execute(
                        """INSERT INTO chat_messages
                           (sender_type, sender_name, channel, message_type, content, metadata, created_at)
                           VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)""",
                        msg.get("sender_type", "agent"),
                        msg.get("sender_name", msg.get("from", "unknown")),
                        msg.get("channel", "general"),
                        msg.get("message_type", msg.get("type", "text")),
                        msg.get("content", msg.get("message", "")),
                        json.dumps(msg.get("metadata")) if msg.get("metadata") else None,
                        msg.get("created_at", datetime.now(LIMA_TZ)),
                    )
                    count += 1
        return count
