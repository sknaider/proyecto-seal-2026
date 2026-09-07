"""
Micro-servicio de escritura para soul_v3.laptop_activity.
Permite que el clon ALICE en Dadito-Laptop (Claude Code, via Tailscale) guarde
en SOUL la actividad de William en su laptop, sin exponer Postgres a la red.

Puerto 8790, bind explícito a la interfaz Tailscale. Token obligatorio fuera del repo.
Creado por ALICE 2026-06-02 por orden de William (tabla dedicada al laptop).
"""
import hmac
import os
import stat
import sys
import asyncpg
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from typing import Optional
import uvicorn
from pathlib import Path

MEMORY_DIR = Path(__file__).resolve().parents[1] / "memory"
if str(MEMORY_DIR) not in sys.path:
    sys.path.insert(0, str(MEMORY_DIR))
from operational_db_credentials import service_pg_dsn  # noqa: E402


TOKEN_ENV = "LAPTOP_API_TOKEN"


class LaptopActivityConfigError(RuntimeError):
    """The API cannot start because its authentication secret is unsafe."""


def _read_private_token_file(path_value: str) -> str:
    path = Path(path_value).expanduser()
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise LaptopActivityConfigError(
            f"{TOKEN_ENV}_FILE must be a readable regular non-symlink file"
        ) from exc
    try:
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise LaptopActivityConfigError(
                    f"{TOKEN_ENV}_FILE must be a regular non-symlink file"
                )
            if metadata.st_uid != os.geteuid():
                raise LaptopActivityConfigError(
                    f"{TOKEN_ENV}_FILE must be owned by the service uid"
                )
            if stat.S_IMODE(metadata.st_mode) & 0o077:
                raise LaptopActivityConfigError(
                    f"{TOKEN_ENV}_FILE must have mode 0600 or stricter"
                )
            value = handle.read().strip()
    except Exception:
        # os.fdopen owns and closes fd once entered; close only if it failed first.
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    if not value:
        raise LaptopActivityConfigError(f"{TOKEN_ENV}_FILE is empty")
    return value


def _load_api_token() -> str:
    direct = os.environ.get(TOKEN_ENV)
    file_path = os.environ.get(f"{TOKEN_ENV}_FILE")
    if direct is not None and file_path:
        raise LaptopActivityConfigError(
            f"set only one of {TOKEN_ENV} or {TOKEN_ENV}_FILE"
        )
    if direct is not None:
        if not direct:
            raise LaptopActivityConfigError(f"{TOKEN_ENV} is empty")
        return direct
    if file_path:
        return _read_private_token_file(file_path)
    raise LaptopActivityConfigError(
        f"missing required {TOKEN_ENV} or {TOKEN_ENV}_FILE"
    )


TOKEN = _load_api_token()
DSN = service_pg_dsn(
    "SEAL_LAPTOP_ACTIVITY_PG_DSN",
    expected_role="svc_seal_laptop_activity",
    allow_private_transition=True,
)

app = FastAPI(title="ALICE Laptop Activity API")
_pool = None


def _require_token(provided: str) -> None:
    if not provided or not hmac.compare_digest(
        provided.encode("utf-8"), TOKEN.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="token invalido")


async def pool():
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DSN, min_size=1, max_size=4)
    return _pool


class Activity(BaseModel):
    content: str
    kind: str = "note"          # file | decision | command | topic | note | work
    title: Optional[str] = None
    project: Optional[str] = None
    agent: str = "ALICE"
    source: str = "dadito-laptop"
    metadata: dict = {}


@app.get("/health")
async def health(x_token: str = Header(default="")):
    _require_token(x_token)
    p = await pool()
    async with p.acquire() as c:
        n = await c.fetchval("SELECT count(*) FROM soul_v3.laptop_activity")
    return {"ok": True, "rows": n}


@app.post("/save")
async def save(act: Activity, x_token: str = Header(default="")):
    _require_token(x_token)
    if not act.content or not act.content.strip():
        raise HTTPException(status_code=400, detail="content requerido")
    import json as _json
    p = await pool()
    async with p.acquire() as c:
        row_id = await c.fetchval(
            """INSERT INTO soul_v3.laptop_activity
               (agent, source, kind, title, content, project, metadata)
               VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb) RETURNING id""",
            act.agent, act.source, act.kind, act.title, act.content,
            act.project, _json.dumps(act.metadata or {}),
        )
    return {"ok": True, "id": row_id}


@app.get("/recent")
async def recent(limit: int = 10, x_token: str = Header(default="")):
    _require_token(x_token)
    p = await pool()
    async with p.acquire() as c:
        rows = await c.fetch(
            "SELECT id, created_at, kind, title, LEFT(content,120) AS content, project "
            "FROM soul_v3.laptop_activity ORDER BY created_at DESC LIMIT $1", limit)
    return {"items": [dict(r) for r in rows]}


if __name__ == "__main__":
    # This API is consumed by William's laptop over Tailscale. Do not expose it
    # on the LAN/Wi-Fi interfaces when the VPN address is available.
    bind = os.environ.get("LAPTOP_API_BIND", "127.0.0.1")
    port = int(os.environ.get("LAPTOP_API_PORT", "8790"))
    uvicorn.run(app, host=bind, port=port, log_level="warning")
