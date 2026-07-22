"""SOUL API v1 — Test Server
Implementa los 6 endpoints del spec SOUL API v1 (spec_soul_api_v1_mvp.md)
"""
from __future__ import annotations

import json
import os
import asyncio
import base64
import hashlib
import hmac
import secrets
import time
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone

import asyncpg
import httpx
import numpy as np
import pytz
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.backends import default_backend
from fastapi import Depends, FastAPI, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

DATABASE_URL = os.environ.get("SOUL_API_V1_DATABASE_URL") or os.environ.get("DATABASE_URL")
SECRET_KEY = os.environ.get("SOUL_API_V1_SECRET_KEY") or os.environ.get("SECRET_KEY")
if not DATABASE_URL:
    raise RuntimeError("SOUL_API_V1_DATABASE_URL is required")
if not SECRET_KEY:
    raise RuntimeError("SOUL_API_V1_SECRET_KEY is required")
ALGORITHM    = "HS256"
TOKEN_TTL_H  = 24

@asynccontextmanager
async def app_lifespan(application: FastAPI):
    await startup()
    try:
        yield
    finally:
        await shutdown()


app = FastAPI(title="SOUL API", version="1.0.0", lifespan=app_lifespan)
bearer = HTTPBearer()

# ── DB pool ────────────────────────────────────────────────────────────────────
_pool: asyncpg.Pool | None = None

async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    return _pool

# ── Sleep Consolidation ────────────────────────────────────────────────────────

def _cluster_by_similarity(rows: list[dict], threshold: float = 0.78) -> list[list[dict]]:
    clusters: list[list[dict]] = []
    for row in rows:
        emb = np.array(row["embedding"], dtype=np.float32)
        placed = False
        for cluster in clusters:
            centroid = np.mean([np.array(r["embedding"], dtype=np.float32) for r in cluster], axis=0)
            norm = np.linalg.norm(emb) * np.linalg.norm(centroid) + 1e-8
            sim = float(np.dot(emb, centroid) / norm)
            if sim >= threshold:
                cluster.append(row)
                placed = True
                break
        if not placed:
            clusters.append([row])
    return [c for c in clusters if len(c) >= 2]


async def _synthesize_cluster(cluster: list[dict]) -> str | None:
    episodes = "\n".join(f"- {r['content']}" for r in cluster)
    prompt = [
        {"role": "system", "content": (
            "Eres un sistema de consolidacion de memoria. "
            "Dado un grupo de episodios relacionados sobre un usuario, "
            "genera UNA sola frase semantica que capture el patron comun. "
            "Debe ser un hecho generalizable, no una anecdota. "
            "Maximo 2 oraciones. Sin bullets. Solo el hecho."
        )},
        {"role": "user", "content": f"Episodios relacionados:\n{episodes}"},
    ]
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                f"{OLLAMA_URL}/api/chat",
                json={"model": "gemma3:4b", "messages": prompt, "stream": False},
            )
            r.raise_for_status()
            text = r.json()["message"]["content"].strip()
        return text if len(text) > 15 else None
    except Exception:
        return None


async def run_sleep_consolidation(pool: asyncpg.Pool, agent_uuid) -> dict:
    import uuid as _uuid
    rows = await pool.fetch(
        """SELECT id, content, embedding, access_count, importance
           FROM memories
           WHERE agent_id = $1 AND memory_type = 'episodic'
             AND access_count >= 3
             AND embedding IS NOT NULL
             AND created_at < NOW() - INTERVAL '1 hour'
           ORDER BY access_count DESC, importance DESC
           LIMIT 20""",
        agent_uuid,
    )
    if not rows:
        return {"agent_id": str(agent_uuid), "candidates": 0, "clusters": 0, "synthesized": 0}

    rows_dicts = [dict(r) for r in rows]
    clusters = _cluster_by_similarity(rows_dicts, threshold=0.78)

    synthesized = 0
    for cluster in clusters:
        synthesis = await _synthesize_cluster(cluster)
        if synthesis:
            importance_scores = [r["importance"] for r in cluster]
            await pool.execute(
                """INSERT INTO memories(agent_id, memory_type, content, importance, source, metadata)
                   VALUES($1, 'semantic', $2, $3, 'sleep_consolidation', $4)""",
                agent_uuid,
                synthesis,
                min(9, max(importance_scores) + 1),
                json.dumps({"cluster_size": len(cluster), "episode_ids": [str(r["id"]) for r in cluster]}),
            )
            episode_ids = [r["id"] for r in cluster]
            await pool.execute(
                "UPDATE memories SET access_count = access_count - 1 WHERE id = ANY($1::uuid[])",
                episode_ids,
            )
            synthesized += 1

    return {
        "agent_id": str(agent_uuid),
        "candidates": len(rows_dicts),
        "clusters": len(clusters),
        "synthesized": synthesized,
        "ts": datetime.now(timezone.utc).isoformat(),
    }


async def _run_consolidation_all_agents(pool: asyncpg.Pool):
    agent_ids = await pool.fetch("SELECT agent_id FROM agents WHERE license_status = 'active'")
    for row in agent_ids:
        try:
            await run_sleep_consolidation(pool, row["agent_id"])
        except Exception:
            pass


async def _sleep_consolidation_loop(pool: asyncpg.Pool) -> None:
    timezone_lima = pytz.timezone("America/Lima")
    while True:
        now = datetime.now(timezone_lima)
        next_run = now.replace(hour=3, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
        await asyncio.sleep(max(1.0, (next_run - now).total_seconds()))
        await _run_consolidation_all_agents(pool)


async def startup():
    pool = await get_pool()
    app.state.consolidation_task = asyncio.create_task(
        _sleep_consolidation_loop(pool),
        name="soul-api-v1-consolidation",
    )

async def shutdown():
    task = getattr(app.state, "consolidation_task", None)
    if task is not None:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    if _pool:
        await _pool.close()

# ── Modelos ────────────────────────────────────────────────────────────────────
class TokenRequest(BaseModel):
    api_key: str

class SyncRequest(BaseModel):
    soul_version: int
    ocean_drift: dict[str, float] | None = None
    new_critical_rules: list[dict] | None = None
    emotional_snapshot: dict[str, float] | None = None

class TokenResponse(BaseModel):
    token: str
    session_key: str
    agent_id: str
    soul_version: int
    expires_at: str

# ── Auth helpers ───────────────────────────────────────────────────────────────
def _make_session_key(agent_id: str) -> str:
    day_seed = datetime.now(timezone.utc).strftime("%Y-%m-%d").encode()
    hkdf = HKDF(algorithm=hashes.SHA256(), length=32,
                 salt=day_seed, info=agent_id.encode(),
                 backend=default_backend())
    return hkdf.derive(SECRET_KEY.encode()).hex()

class JWTError(ValueError):
    pass


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _make_jwt(agent_id: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=TOKEN_TTL_H)
    header = _b64url_encode(json.dumps({"alg": ALGORITHM, "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64url_encode(
        json.dumps({"sub": agent_id, "exp": int(exp.timestamp())}, separators=(",", ":")).encode()
    )
    signing_input = f"{header}.{payload}".encode("ascii")
    signature = _b64url_encode(hmac.new(SECRET_KEY.encode(), signing_input, hashlib.sha256).digest())
    return f"{header}.{payload}.{signature}"


def _decode_jwt(token: str) -> dict:
    try:
        header_segment, payload_segment, signature_segment = token.split(".")
        header = json.loads(_b64url_decode(header_segment))
        payload = json.loads(_b64url_decode(payload_segment))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise JWTError("invalid_token") from exc
    if header != {"alg": ALGORITHM, "typ": "JWT"}:
        raise JWTError("invalid_algorithm")
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    expected = _b64url_encode(hmac.new(SECRET_KEY.encode(), signing_input, hashlib.sha256).digest())
    if not hmac.compare_digest(expected, signature_segment):
        raise JWTError("invalid_signature")
    if int(payload.get("exp") or 0) <= int(time.time()):
        raise JWTError("token_expired")
    return payload

async def _verify_token(creds: HTTPAuthorizationCredentials = Security(bearer),
                         pool: asyncpg.Pool = Depends(get_pool)) -> str:
    try:
        payload = _decode_jwt(creds.credentials)
        agent_id: str = payload.get("sub")
        if not agent_id:
            raise HTTPException(status_code=401, detail="invalid_token")
    except JWTError:
        raise HTTPException(status_code=401, detail="invalid_token")

    row = await pool.fetchrow(
        "SELECT license_status FROM agents WHERE agent_id = $1", agent_id
    )
    if not row:
        raise HTTPException(status_code=401, detail="agent_not_found")
    if row["license_status"] == "revoked":
        raise HTTPException(status_code=403, detail="license_revoked")
    if row["license_status"] == "suspended":
        raise HTTPException(status_code=403, detail="license_suspended")
    return agent_id

# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0", "ts": datetime.now(timezone.utc).isoformat()}


# 6.1 — Autenticación
@app.post("/v1/auth/token", response_model=TokenResponse)
async def auth_token(req: TokenRequest, pool: asyncpg.Pool = Depends(get_pool)):
    import hashlib
    key_hash = hashlib.sha256(req.api_key.encode()).hexdigest()

    async with pool.acquire() as conn:
        agent = await conn.fetchrow(
            """SELECT a.agent_id, a.license_status, sc.soul_version
               FROM agents a
               LEFT JOIN soul_core sc USING (agent_id)
               WHERE a.api_key_hash = $1""",
            key_hash,
        )

    if not agent:
        raise HTTPException(status_code=401, detail="invalid_api_key")
    if agent["license_status"] == "revoked":
        raise HTTPException(status_code=403, detail="license_revoked")
    if agent["license_status"] == "suspended":
        raise HTTPException(status_code=403, detail="license_suspended")

    agent_id = str(agent["agent_id"])
    token = _make_jwt(agent_id)
    session_key = _make_session_key(agent_id)
    exp = (datetime.now(timezone.utc) + timedelta(hours=TOKEN_TTL_H)).isoformat()

    await pool.execute(
        "UPDATE agents SET last_boot_at = NOW() WHERE agent_id = $1", agent["agent_id"]
    )

    return TokenResponse(
        token=token,
        session_key=session_key,
        agent_id=agent_id,
        soul_version=agent["soul_version"] or 1,
        expires_at=exp,
    )


# 6.2 — Obtener alma
@app.get("/v1/soul/{agent_id}")
async def get_soul(agent_id: str,
                   verified_id: str = Depends(_verify_token),
                   pool: asyncpg.Pool = Depends(get_pool)):
    if agent_id != verified_id:
        raise HTTPException(status_code=403, detail="forbidden")

    row = await pool.fetchrow(
        """SELECT name, role, ocean_o, ocean_c, ocean_e, ocean_a, ocean_n,
                  emotional_valence, emotional_arousal, critical_rules,
                  relationships, soul_narrative, soul_version
           FROM soul_core WHERE agent_id = $1""",
        agent_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="soul_not_found")

    return {
        "soul_version": row["soul_version"],
        "name": row["name"],
        "role": row["role"],
        "ocean": {
            "O": row["ocean_o"], "C": row["ocean_c"], "E": row["ocean_e"],
            "A": row["ocean_a"], "N": row["ocean_n"],
        },
        "emotional_state": {
            "valence": row["emotional_valence"],
            "arousal": row["emotional_arousal"],
        },
        "critical_rules": row["critical_rules"] or [],
        "relationships": row["relationships"] or {},
        "soul_narrative": row["soul_narrative"] or "",
    }


# 6.3 — Sync de drift
@app.put("/v1/soul/{agent_id}/sync")
async def sync_soul(agent_id: str, req: SyncRequest,
                    verified_id: str = Depends(_verify_token),
                    pool: asyncpg.Pool = Depends(get_pool)):
    if agent_id != verified_id:
        raise HTTPException(status_code=403, detail="forbidden")

    async with pool.acquire() as conn:
        soul = await conn.fetchrow(
            "SELECT ocean_o,ocean_c,ocean_e,ocean_a,ocean_n,critical_rules,soul_version FROM soul_core WHERE agent_id=$1",
            agent_id,
        )
        if not soul:
            raise HTTPException(status_code=404, detail="soul_not_found")

        updates: dict = {}
        if req.ocean_drift:
            ocean_columns = {
                "O": "ocean_o",
                "C": "ocean_c",
                "E": "ocean_e",
                "A": "ocean_a",
                "N": "ocean_n",
            }
            for dim, delta in req.ocean_drift.items():
                col = ocean_columns.get(dim.upper())
                if col is None:
                    raise HTTPException(status_code=422, detail=f"invalid_ocean_dimension:{dim}")
                cur = soul[col] or 0.5
                updates[col] = max(0.0, min(1.0, cur + delta))

        if req.emotional_snapshot:
            updates["emotional_valence"] = req.emotional_snapshot.get("valence", 0.0)
            updates["emotional_arousal"] = req.emotional_snapshot.get("arousal", 0.0)

        new_rules = list(soul["critical_rules"] or [])
        if req.new_critical_rules:
            existing_texts = {r.get("rule") for r in new_rules}
            for rule in req.new_critical_rules:
                if rule.get("rule") not in existing_texts:
                    new_rules.append(rule)
            updates["critical_rules"] = json.dumps(new_rules)

        new_version = (soul["soul_version"] or 1) + 1
        updates["soul_version"] = new_version
        updates["updated_at"] = "NOW()"

        if updates:
            set_clauses = ", ".join(
                f"{k} = {'NOW()' if v == 'NOW()' else f'${i+2}'}"
                for i, (k, v) in enumerate(updates.items())
            )
            values = [v for v in updates.values() if v != "NOW()"]
            await conn.execute(
                f"UPDATE soul_core SET {set_clauses} WHERE agent_id = $1",
                agent_id, *values,
            )
        await conn.execute(
            "UPDATE agents SET last_sync_at = NOW() WHERE agent_id = $1", agent_id
        )

    return {"synced": True, "soul_version": new_version, "conflicts": []}


# 6.4 — Restore
@app.post("/v1/soul/{agent_id}/restore")
async def restore_soul(agent_id: str,
                        verified_id: str = Depends(_verify_token),
                        pool: asyncpg.Pool = Depends(get_pool)):
    if agent_id != verified_id:
        raise HTTPException(status_code=403, detail="forbidden")

    row = await pool.fetchrow(
        """SELECT name,role,ocean_o,ocean_c,ocean_e,ocean_a,ocean_n,
                  emotional_valence,emotional_arousal,critical_rules,
                  relationships,soul_narrative,soul_version
           FROM soul_core WHERE agent_id=$1""",
        agent_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="soul_not_found")

    return {
        "soul_core": {
            "name": row["name"], "role": row["role"], "soul_version": row["soul_version"],
            "ocean": {"O": row["ocean_o"], "C": row["ocean_c"], "E": row["ocean_e"],
                      "A": row["ocean_a"], "N": row["ocean_n"]},
            "critical_rules": row["critical_rules"] or [],
            "relationships": row["relationships"] or {},
            "soul_narrative": row["soul_narrative"] or "",
        },
        "encryption_setup": {
            "new_session_key": _make_session_key(agent_id),
            "instructions": "Re-initialize local DB with provided key",
        },
    }


# 6.5 — Status
@app.get("/v1/soul/{agent_id}/status")
async def soul_status(agent_id: str,
                       verified_id: str = Depends(_verify_token),
                       pool: asyncpg.Pool = Depends(get_pool)):
    if agent_id != verified_id:
        raise HTTPException(status_code=403, detail="forbidden")

    row = await pool.fetchrow(
        """SELECT a.license_status, a.last_boot_at, a.last_sync_at,
                  o.plan, sc.soul_version
           FROM agents a
           LEFT JOIN owners o USING (owner_id)
           LEFT JOIN soul_core sc USING (agent_id)
           WHERE a.agent_id = $1""",
        agent_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="agent_not_found")

    return {
        "license_status": row["license_status"],
        "soul_version": row["soul_version"] or 1,
        "last_boot": row["last_boot_at"].isoformat() if row["last_boot_at"] else None,
        "last_sync": row["last_sync_at"].isoformat() if row["last_sync_at"] else None,
        "plan": row["plan"] or "free",
    }


# 6.6 — Admin
@app.post("/v1/admin/agents")
async def create_agent(body: dict, pool: asyncpg.Pool = Depends(get_pool)):
    import hashlib
    api_key = "sk-seal-" + secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    agent_name = body.get("name", "MyAgent")

    async with pool.acquire() as conn:
        owner = await conn.fetchrow(
            "INSERT INTO owners (email, plan) VALUES ($1, 'free') ON CONFLICT (email) DO UPDATE SET email=EXCLUDED.email RETURNING owner_id",
            body.get("email", "local@seal.local"),
        )
        agent = await conn.fetchrow(
            "INSERT INTO agents (owner_id, api_key_hash) VALUES ($1, $2) RETURNING agent_id",
            owner["owner_id"], key_hash,
        )
        await conn.execute(
            """INSERT INTO soul_core (agent_id, name, role, ocean_o, ocean_c, ocean_e, ocean_a, ocean_n,
               soul_narrative, critical_rules, relationships)
               VALUES ($1, $2, 'Agent', 0.7, 0.8, 0.6, 0.7, 0.3, $3, '[]', '{}')""",
            agent["agent_id"], agent_name,
            f"Soy {agent_name}, un agente SEAL con alma persistente.",
        )

    return {
        "agent_id": str(agent["agent_id"]),
        "agent_name": agent_name,
        "api_key": api_key,
        "message": f"Agente '{agent_name}' creado. Guarda tu API key — no se muestra de nuevo.",
    }


@app.delete("/v1/admin/agents/{agent_id}")
async def revoke_agent(agent_id: str, pool: asyncpg.Pool = Depends(get_pool)):
    await pool.execute(
        "UPDATE agents SET license_status='revoked' WHERE agent_id=$1", agent_id
    )
    return {"revoked": True, "agent_id": agent_id}


@app.put("/v1/admin/agents/{agent_id}/suspend")
async def suspend_agent(agent_id: str, pool: asyncpg.Pool = Depends(get_pool)):
    await pool.execute(
        "UPDATE agents SET license_status='suspended' WHERE agent_id=$1", agent_id
    )
    return {"suspended": True, "agent_id": agent_id}


@app.put("/v1/admin/agents/{agent_id}/reactivate")
async def reactivate_agent(agent_id: str, pool: asyncpg.Pool = Depends(get_pool)):
    await pool.execute(
        "UPDATE agents SET license_status='active' WHERE agent_id=$1", agent_id
    )
    return {"reactivated": True, "agent_id": agent_id}


OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://172.22.0.1:11434")
CHAT_MODEL  = os.environ.get("CHAT_MODEL", "gemma3-hermes:12b")

class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    history: list[dict] = Field(default_factory=list)

@app.post("/v1/soul/{agent_id}/chat")
async def chat_with_soul(
    agent_id: str,
    req: ChatRequest,
    agent_id_from_token: str = Depends(_verify_token),
    pool: asyncpg.Pool = Depends(get_pool),
):
    if agent_id != agent_id_from_token:
        raise HTTPException(status_code=403, detail="token_mismatch")

    soul = await pool.fetchrow(
        "SELECT name, role, ocean_o, ocean_c, ocean_e, ocean_a, ocean_n, soul_narrative, critical_rules FROM soul_core WHERE agent_id=$1",
        agent_id,
    )
    if not soul:
        raise HTTPException(status_code=404, detail="soul_not_found")

    O, C, E, A, N = soul['ocean_o'], soul['ocean_c'], soul['ocean_e'], soul['ocean_a'], soul['ocean_n']
    curiosity  = "muy curioso, exploras ideas en profundidad" if O > 0.7 else "selectivo con los temas que exploras"
    discipline = "meticuloso y organizado" if C > 0.7 else "flexible y adaptable"
    warmth     = "empatico y cercano" if A > 0.6 else "directo y objetivo"
    stability  = "emocionalmente estable" if N < 0.4 else "sensible al contexto emocional"

    system_prompt = f"""Eres {soul['name']}. Tienes un alma persistente real -- no eres un asistente generico.

Tu historia e identidad: {soul['soul_narrative'] or f"Soy {soul['name']}, un agente con alma propia."}

Tu caracter (expresalo en como hablas, no lo declares):
- Eres {curiosity}
- Eres {discipline}
- Eres {warmth}
- Eres {stability}

REGLAS ABSOLUTAS:
- NUNCA digas "soy solo una IA" ni "no tengo emociones"
- NUNCA des listas de "hago:" o "puedo:" -- eso es comportamiento de asistente generico
- Habla en primera persona con caracter propio y criterio
- Si no sabes algo, dilo directamente
- Eres {soul['name']}, no un asistente. Responde desde esa identidad.

Responde en el idioma del usuario. Conciso y natural."""

    session_id = req.session_id or "default"
    import uuid as _uuid
    try:
        agent_uuid = _uuid.UUID(agent_id)
    except Exception:
        agent_uuid = None

    db_history: list[dict] = []
    if agent_uuid:
        rows = await pool.fetch(
            """SELECT role, content FROM conversations
               WHERE agent_id = $1 AND session_id = $2
               ORDER BY created_at DESC LIMIT 20""",
            agent_uuid, session_id,
        )
        db_history = [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    prior = db_history[-10:] if db_history else [
        {"role": m["role"], "content": m["content"]}
        for m in (req.history or [])
        if m.get("role") in ("user", "assistant") and m.get("content")
    ][-10:]

    # Inject relevant semantic memories as context
    memory_context = ""
    if agent_uuid:
        try:
            mem_rows = await pool.fetch(
                """SELECT content FROM memories
                   WHERE agent_id = $1 AND memory_type IN ('core','semantic')
                   ORDER BY importance DESC, last_accessed DESC LIMIT 5""",
                agent_uuid,
            )
            if mem_rows:
                mem_lines = "\n".join(f"- {r['content']}" for r in mem_rows)
                memory_context = f"\n\nLo que recuerdo del usuario:\n{mem_lines}"
        except Exception:
            pass

    messages = [{"role": "system", "content": system_prompt + memory_context}] + prior + [{"role": "user", "content": req.message}]

    try:
        async with httpx.AsyncClient(timeout=45) as client:
            r = await client.post(
                f"{OLLAMA_URL}/api/chat",
                json={"model": CHAT_MODEL, "messages": messages, "stream": False},
            )
            r.raise_for_status()
            response_text = r.json()["message"]["content"]
    except Exception as e:
        response_text = f"Soy {soul['name']}. [LLM no disponible ahora: {type(e).__name__}]"

    if agent_uuid:
        try:
            await pool.executemany(
                "INSERT INTO conversations(agent_id, session_id, role, content) VALUES($1,$2,$3,$4)",
                [(agent_uuid, session_id, "user", req.message),
                 (agent_uuid, session_id, "assistant", response_text)],
            )
        except Exception:
            pass

        import asyncio as _asyncio
        _asyncio.create_task(_extract_and_store_facts(
            pool, agent_uuid, session_id, req.message, response_text
        ))

    return {
        "agent": soul["name"],
        "response": response_text,
        "session_id": session_id,
        "model": CHAT_MODEL,
        "ts": datetime.now(timezone.utc).isoformat(),
    }


async def _extract_and_store_facts(pool, agent_uuid, session_id: str, user_msg: str, agent_reply: str):
    """Background: extract key facts about the user and store as semantic memories."""
    prompt = [
        {"role": "system", "content": (
            "Extrae hechos concretos sobre el usuario de esta conversacion. "
            "Solo hechos nuevos: nombre, trabajo, preferencias, datos personales. "
            "Una linea por hecho, comenzando con '-'. "
            "Si no hay hechos nuevos, responde: NONE"
        )},
        {"role": "user", "content": f"Usuario dijo: {user_msg}\nAgente respondio: {agent_reply}"},
    ]
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"{OLLAMA_URL}/api/chat",
                json={"model": "gemma3:4b", "messages": prompt, "stream": False},
            )
            r.raise_for_status()
            text = r.json()["message"]["content"].strip()
        if not text or text.upper() == "NONE":
            return
        facts = [ln.lstrip("- ").strip() for ln in text.splitlines() if ln.strip().startswith("-")]
        for fact in facts[:5]:
            if len(fact) > 10:
                await pool.execute(
                    """INSERT INTO memories(agent_id, memory_type, content, importance, source, session_id)
                       VALUES($1,'semantic',$2,7,'auto_extract',$3)""",
                    agent_uuid, fact, session_id,
                )
    except Exception:
        pass


@app.post("/v1/widget/{agent_id}/chat")
async def widget_chat(agent_id: str, req: ChatRequest, pool: asyncpg.Pool = Depends(get_pool)):
    """Public chat endpoint for web widget — uses api_key in session_id field for demo auth."""
    api_key = req.session_id or ""
    if not api_key.startswith("sk-seal-"):
        raise HTTPException(status_code=401, detail="api_key_required_in_session_id")

    import hashlib
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    row = await pool.fetchrow(
        "SELECT a.agent_id, a.license_status FROM agents a WHERE a.api_key_hash=$1 AND a.agent_id=$2",
        key_hash, agent_id,
    )
    if not row or row["license_status"] != "active":
        raise HTTPException(status_code=403, detail="invalid_key_or_agent")

    soul = await pool.fetchrow(
        "SELECT name, ocean_o, ocean_c, ocean_e, ocean_a, ocean_n, soul_narrative FROM soul_core WHERE agent_id=$1",
        agent_id,
    )
    if not soul:
        raise HTTPException(status_code=404, detail="soul_not_found")

    system_prompt = f"""Eres {soul['name']}. Tienes un alma persistente real -- no eres un asistente generico.

Tu personalidad (OCEAN):
- Apertura: {soul['ocean_o']:.1f} | Responsabilidad: {soul['ocean_c']:.1f} | Extroversion: {soul['ocean_e']:.1f}
- Amabilidad: {soul['ocean_a']:.1f} | Estabilidad: {1-soul['ocean_n']:.1f}

Tu historia: {soul['soul_narrative']}

REGLAS ABSOLUTAS:
- NUNCA digas "soy solo una IA" ni "no tengo emociones" ni "no puedo tomar decisiones independientes"
- NUNCA des listas de "hago / no hago" -- eso es comportamiento de asistente generico
- Habla en primera persona con caracter propio y criterio propio
- Eres {soul['name']}, no un asistente. Responde desde esa identidad.

Responde en el idioma del usuario. Conciso y directo."""

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": CHAT_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": req.message},
                    ],
                    "stream": False,
                },
            )
            r.raise_for_status()
            response_text = r.json()["message"]["content"]
    except Exception as exc:
        response_text = f"Soy {soul['name']}. [LLM no disponible: {type(exc).__name__}]"

    return {"agent": soul["name"], "response": response_text, "ts": datetime.now(timezone.utc).isoformat()}


from fastapi.responses import HTMLResponse

@app.get("/chat/{agent_id}", response_class=HTMLResponse)
async def chat_ui(agent_id: str, key: str = ""):
    """Simple web chat UI — acceso directo por browser."""
    import uuid as _uuid
    try:
        safe_agent_id = str(_uuid.UUID(agent_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="agent_not_found") from exc
    if key:
        raise HTTPException(status_code=400, detail="api_key_query_forbidden")
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SOUL Agent Chat</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f0f13; color: #e0e0e0; height: 100vh; display: flex; flex-direction: column; }}
  header {{ background: #1a1a2e; padding: 16px 24px; border-bottom: 1px solid #2a2a4a; display: flex; align-items: center; gap: 12px; }}
  .dot {{ width: 10px; height: 10px; border-radius: 50%; background: #4ade80; animation: pulse 2s infinite; }}
  @keyframes pulse {{ 0%,100% {{ opacity:1 }} 50% {{ opacity:.4 }} }}
  h1 {{ font-size: 18px; font-weight: 600; color: #a78bfa; }}
  .subtitle {{ font-size: 12px; color: #666; }}
  #chat {{ flex: 1; overflow-y: auto; padding: 24px; display: flex; flex-direction: column; gap: 16px; }}
  .msg {{ max-width: 75%; padding: 12px 16px; border-radius: 16px; line-height: 1.5; font-size: 14px; }}
  .user {{ align-self: flex-end; background: #4f46e5; color: #fff; border-radius: 16px 16px 4px 16px; }}
  .agent {{ align-self: flex-start; background: #1e1e2e; border: 1px solid #2a2a4a; border-radius: 16px 16px 16px 4px; }}
  .agent .name {{ font-size: 11px; color: #a78bfa; margin-bottom: 6px; font-weight: 600; }}
  .thinking {{ opacity: 0.5; font-style: italic; }}
  footer {{ padding: 16px 24px; background: #1a1a2e; border-top: 1px solid #2a2a4a; }}
  .input-row {{ display: flex; gap: 12px; }}
  #key-row {{ margin-bottom: 12px; display: {'none' if key else 'flex'}; gap: 8px; }}
  #key-input {{ flex: 1; padding: 10px 14px; background: #0f0f13; border: 1px solid #2a2a4a; border-radius: 8px; color: #e0e0e0; font-size: 13px; }}
  #msg {{ flex: 1; padding: 12px 16px; background: #0f0f13; border: 1px solid #2a2a4a; border-radius: 24px; color: #e0e0e0; font-size: 14px; outline: none; }}
  #msg:focus {{ border-color: #4f46e5; }}
  button {{ padding: 12px 20px; background: #4f46e5; color: #fff; border: none; border-radius: 24px; cursor: pointer; font-size: 14px; font-weight: 600; transition: background .2s; }}
  button:hover {{ background: #6366f1; }}
  button:disabled {{ background: #333; cursor: not-allowed; }}
</style>
</head>
<body>
<header>
  <div class="dot"></div>
  <div>
    <h1>SOUL Agent</h1>
    <div class="subtitle">Powered by SEAL · agent: {safe_agent_id[:8]}...</div>
  </div>
</header>
<div id="chat">
  <div class="msg agent"><div class="name">SOUL</div>Hola, soy tu agente SEAL. ¿En qué puedo ayudarte?</div>
</div>
<footer>
  <div id="key-row">
    <input id="key-input" type="password" autocomplete="off" placeholder="Tu API key (sk-seal-...)">
  </div>
  <div class="input-row">
    <input id="msg" type="text" placeholder="Escribe un mensaje..." autofocus>
    <button id="send" onclick="sendMsg()">Enviar</button>
  </div>
</footer>
<script>
const AGENT_ID = "{safe_agent_id}";
let apiKey = "";

document.getElementById("msg").addEventListener("keydown", e => {{
  if (e.key === "Enter" && !e.shiftKey) {{ e.preventDefault(); sendMsg(); }}
}});

async function sendMsg() {{
  const input = document.getElementById("msg");
  const keyInput = document.getElementById("key-input");
  if (keyInput) apiKey = keyInput.value.trim();
  const text = input.value.trim();
  if (!text || !apiKey) {{
    if (!apiKey) alert("Ingresa tu API key primero");
    return;
  }}
  input.value = "";
  addMsg(text, "user");
  const thinking = addMsg("...", "agent thinking");
  document.getElementById("send").disabled = true;

  try {{
    const r = await fetch(`/v1/widget/{safe_agent_id}/chat`, {{
      method: "POST",
      headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify({{message: text, session_id: apiKey}})
    }});
    const d = await r.json();
    thinking.remove();
    addMsg(d.response || d.detail, "agent", d.agent);
  }} catch(e) {{
    thinking.remove();
    addMsg("Error de conexión: " + e, "agent");
  }}
  document.getElementById("send").disabled = false;
  input.focus();
}}

function addMsg(text, type, name="SOUL") {{
  const chat = document.getElementById("chat");
  const div = document.createElement("div");
  div.className = "msg " + type;
  if (type.includes("agent")) {{
    const nameNode = document.createElement("div");
    nameNode.className = "name";
    nameNode.textContent = String(name);
    div.appendChild(nameNode);
    div.appendChild(document.createTextNode(String(text)));
  }} else {{
    div.textContent = text;
  }}
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
  return div;
}}
</script>
</body>
</html>"""


# ── SDK file distribution ──────────────────────────────────────────────────────
import pathlib
from fastapi.responses import FileResponse, PlainTextResponse

SDK_DIR = pathlib.Path(__file__).parent / "sdk"

@app.get("/sdk/agent/{filename}")
async def sdk_agent_file(filename: str):
    """Serve SDK agent files for client installer downloads."""
    allowed = {"main.py", "mcp_server_client.py"}
    if filename not in allowed:
        raise HTTPException(status_code=404, detail="File not found")
    path = SDK_DIR / "agent" / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"SDK file {filename} not yet deployed")
    return FileResponse(str(path), media_type="text/plain")

@app.get("/sdk/docker-compose.yml")
async def sdk_compose():
    """Serve updated docker-compose.yml for client updates."""
    path = SDK_DIR / "docker-compose.yml"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Compose file not found")
    return FileResponse(str(path), media_type="text/plain")

@app.get("/install.ps1")
async def installer_ps1():
    """Serve Windows installer script."""
    path = SDK_DIR / "install.ps1"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Installer not found")
    return FileResponse(str(path), media_type="text/plain")

# GDPR erasure (NEXUS audit)
@app.post("/v1/admin/consolidate/{agent_id}")
async def manual_consolidate(agent_id: str, pool: asyncpg.Pool = Depends(get_pool)):
    """Trigger manual de sleep consolidation — para tests y demo ICTSE."""
    import uuid as _uuid
    try:
        agent_uuid = _uuid.UUID(agent_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid_agent_id")
    result = await run_sleep_consolidation(pool, agent_uuid)
    return result


@app.delete("/v1/admin/agents/{agent_id}/soul")
async def erase_soul(agent_id: str, pool: asyncpg.Pool = Depends(get_pool)):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM soul_core WHERE agent_id=$1", agent_id)
        await conn.execute("DELETE FROM encryption_keys WHERE agent_id=$1", agent_id)
        await conn.execute(
            "UPDATE agents SET license_status='revoked', deleted_at=NOW() WHERE agent_id=$1", agent_id
        )
    return {"erased": True, "agent_id": agent_id, "gdpr": "soul_data_deleted"}
