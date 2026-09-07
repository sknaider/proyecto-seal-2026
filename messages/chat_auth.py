"""
chat_auth.py — SEAL Chat Pro JWT authentication layer.

Provides JWT token creation/validation and FastAPI dependency injection
for protected routes.

Usage:
    from chat_auth import create_token, get_current_user, require_auth

    # Create token on login
    token = create_token(user_id=1, username="william")

    # FastAPI dependency — injects user dict or returns 401
    @app.get("/api/protected")
    async def protected(user: dict = Depends(require_auth)):
        return {"hello": user["username"]}
"""
from __future__ import annotations

import hashlib
import os
import time
from typing import Optional

import jwt
from fastapi import Request, WebSocket

# ── Config ──────────────────────────────────────────────────────────────────

# Secret key for JWT signing — generate once, persist across restarts
_SECRET_FILE = os.path.expanduser("~/.seal_chat_jwt_secret")


def _get_secret() -> str:
    """Load or generate JWT secret key."""
    if os.path.exists(_SECRET_FILE):
        with open(_SECRET_FILE) as f:
            return f.read().strip()
    import secrets
    secret = secrets.token_hex(32)
    with open(_SECRET_FILE, "w") as f:
        f.write(secret)
    os.chmod(_SECRET_FILE, 0o600)
    return secret


JWT_SECRET = _get_secret()
JWT_ALGORITHM = "HS256"
TOKEN_EXPIRY_SECONDS = 72 * 3600  # 3 days


# ── Token operations ────────────────────────────────────────────────────────

def create_token(user_id: int, username: str, role: str = "user") -> str:
    """Create a signed JWT token."""
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "iat": int(time.time()),
        "exp": int(time.time()) + TOKEN_EXPIRY_SECONDS,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict | None:
    """Decode and validate a JWT token. Returns payload or None."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def hash_token(token: str) -> str:
    """Hash a token for DB storage (session tracking)."""
    return hashlib.sha256(token.encode()).hexdigest()


def _fresh_session_identity(jwt_user: dict, session_user: dict) -> dict:
    """Return identity from the live DB session, never from stale JWT claims.

    The JWT proves possession and identifies the session token. Authorization
    attributes may change after login, so ``role`` and the user profile come
    from ``ChatDB.validate_session`` on every protected request.
    """

    return {
        **jwt_user,
        "sub": str(session_user["user_id"]),
        "username": session_user["username"],
        "display_name": session_user.get("display_name"),
        "role": session_user["role"],
        "avatar_url": session_user.get("avatar_url"),
    }


# ── FastAPI dependency injection ────────────────────────────────────────────

def _extract_token(request: Request) -> str | None:
    """Extract JWT from Authorization header or cookie."""
    # 1. Authorization: Bearer <token>
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    # 2. Cookie
    token = request.cookies.get("seal_token")
    if token:
        return token
    # 3. Query param (for WebSocket connections)
    token = request.query_params.get("token")
    return token


def _extract_token_ws(websocket: WebSocket) -> str | None:
    """Extract JWT from WebSocket query params or cookies."""
    token = websocket.query_params.get("token")
    if token:
        return token
    # Try cookies
    token = websocket.cookies.get("seal_token")
    return token


async def get_current_user(request: Request) -> dict | None:
    """Extract and decode user from request. Returns user dict or None.
    Does NOT raise — use require_auth for protected routes."""
    token = _extract_token(request)
    if not token:
        return None
    return decode_token(token)


async def get_ws_user(websocket: WebSocket) -> dict | None:
    """Extract, decode, and validate the backing WebSocket session."""
    token = _extract_token_ws(websocket)
    if not token:
        return None
    user = decode_token(token)
    if not user or _auth_db is None:
        return None
    try:
        session = await _auth_db.validate_session(hash_token(token))
    except Exception:
        return None
    return _fresh_session_identity(user, session) if session else None


_auth_db = None  # Injected by chat_server.py at startup via set_auth_db()


def set_auth_db(db) -> None:
    """Register the shared ChatDB instance for session validation."""
    global _auth_db
    _auth_db = db


async def require_auth(request: Request) -> dict:
    """FastAPI dependency that requires authentication.
    Validates JWT signature AND checks session exists in DB (prevents post-logout reuse).
    Returns user payload or raises 401."""
    from fastapi import HTTPException
    token = _extract_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = decode_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    # A signed JWT is only a possession proof, never current authorization.
    # Without the live session store we cannot know whether it was revoked or
    # whether its role changed, so fail closed instead of trusting stale claims.
    if _auth_db is None:
        raise HTTPException(status_code=503, detail="Session validation unavailable")
    try:
        session = await _auth_db.validate_session(hash_token(token))
        if not session:
            raise HTTPException(status_code=401, detail="Session expired or revoked")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Session validation unavailable") from exc
    identity = _fresh_session_identity(user, session)
    # Internal-only proof passed to bounded destructive DB functions. It is
    # never serialized to clients; it binds owner actions to this live session
    # instead of trusting possession of the server's admin DB credential alone.
    identity["_session_token_hash"] = hash_token(token)
    return identity


async def require_admin(request: Request) -> dict:
    """FastAPI dependency that requires admin role."""
    from fastapi import HTTPException
    user = await require_auth(request)
    if user.get("role") not in ("admin", "superuser"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
