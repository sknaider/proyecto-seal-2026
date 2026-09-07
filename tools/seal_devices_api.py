#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""seal_devices_api.py — Mini-API HTTP de gestión de devices/clones (carril NEXUS, backend de la UI de ALICE).

Expone la cola de registros, los devices autorizados, y las acciones human-gated (aprobar/rechazar/revocar)
para que la interface de Seal Studio las consuma. La COMPUERTA HUMANA de William se enforcea SERVER-SIDE:
toda acción exige el admin-token de William (header X-William-Token). Sin token válido → 401 fail-closed.
Un botón de UI NUNCA puede emitir/revocar un token sin ese secreto → el modelo no se bypassea por click.

Bind: 127.0.0.1:8779 por defecto (seal-studio corre en central → llama por localhost, NO expuesto a red).
Auth: constant-time compare (hmac.compare_digest) contra el token en ~/.seal/central/william_admin.token (0600).

THREAT MODEL (honesto, adversarial FABLE 2026-07-02):
  · SÓLIDO vs red/UI: 6/6 (auth en los 6 endpoints, fail-closed sin/mal token, constant-time, SQL parametrizado,
    bind localhost, doble compuerta con la sesión de William en el proxy de ALICE). Un atacante de RED no entra.
  · LÍMITE same-UID (NO es bug — es arquitectura sin sandbox): el admin-token es un archivo 0600 de 'dadito',
    y TODOS los agentes corren como 'dadito' → un proceso co-residente comprometido/rogue puede leer el token y
    llamar /approve. O sea la compuerta es "William EN EL CÓDIGO", no "William en la máquina". En el threat model
    de familia-cooperativa (agentes de confianza) = ACEPTABLE-DOCUMENTADO. Para cerrarlo de verdad (si se necesita
    defender contra un agente rogue): credencial system-level (systemd-creds/TPM) o 2FA/hardware key. Decisión de
    William según su threat model.
  · Menor: sin rate-limit en /approve (bajo riesgo — localhost + gateado por la sesión de William en el proxy).
"""
from __future__ import annotations
import hmac
import json
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "memory"))

from aiohttp import web
from seal_central_signer import list_pending, approve_and_issue, reject

_ADMIN_TOKEN_PATH = Path(os.environ.get("SEAL_ADMIN_TOKEN_PATH",
                                        str(Path.home() / ".seal" / "central" / "william_admin.token")))


def load_or_create_admin_token() -> str:
    """El secreto que prueba 'soy William' en la UI. Se genera UNA vez (0600). Solo William lo tiene."""
    _ADMIN_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(_ADMIN_TOKEN_PATH.parent, 0o700)
    except Exception:
        pass
    if _ADMIN_TOKEN_PATH.exists():
        return _ADMIN_TOKEN_PATH.read_text().strip()
    tok = secrets.token_urlsafe(32)
    _ADMIN_TOKEN_PATH.write_text(tok)
    try:
        os.chmod(_ADMIN_TOKEN_PATH, 0o600)
    except Exception:
        pass
    return tok


def _authorized(request: web.Request) -> bool:
    """Compuerta humana: constant-time compare del header contra el admin-token. Fail-closed."""
    presented = request.headers.get("X-William-Token", "")
    expected = load_or_create_admin_token()
    if not presented:
        return False
    return hmac.compare_digest(presented, expected)


def _require_admin(handler):
    async def wrapped(request: web.Request) -> web.Response:
        if not _authorized(request):
            return web.json_response({"error": "no_autorizado", "detail": "falta/incorrecto X-William-Token (compuerta humana)"}, status=401)
        return await handler(request)
    return wrapped


# ── Lecturas (para poblar la UI) ──────────────────────────────────────────────
@_require_admin
async def get_pending(request: web.Request) -> web.Response:
    from soul_table_governance import connect_db
    conn = await connect_db()
    try:
        rows = await list_pending(conn)
    finally:
        await conn.close()
    # created_at → iso
    for r in rows:
        if r.get("created_at") is not None:
            r["created_at"] = str(r["created_at"])
    return web.json_response({"pending": rows})


@_require_admin
async def get_devices(request: web.Request) -> web.Response:
    from soul_table_governance import connect_db
    conn = await connect_db()
    try:
        rows = await conn.fetch(
            """SELECT device_id, agent, status, registered_by, registered_at, last_seen_at, revoked_at
               FROM soul_v3.authorized_devices ORDER BY registered_at DESC NULLS LAST""")
    finally:
        await conn.close()
    devices = [{k: (str(v) if hasattr(v, "isoformat") else v) for k, v in dict(r).items()} for r in rows]
    return web.json_response({"devices": devices})


@_require_admin
async def get_status(request: web.Request) -> web.Response:
    """Último sync por agente (aterrizaje de memorias en central)."""
    from soul_table_governance import connect_db
    conn = await connect_db()
    try:
        rows = await conn.fetch(
            """SELECT agent, max(created_at) AS last_sync, count(*) AS total
               FROM soul_v3.memories GROUP BY agent ORDER BY last_sync DESC NULLS LAST""")
    finally:
        await conn.close()
    status = [{"agent": r["agent"], "last_sync": str(r["last_sync"]) if r["last_sync"] else None, "total": r["total"]} for r in rows]
    return web.json_response({"status": status})


# ── Acciones HUMAN-GATED (aprobar/rechazar/revocar) ───────────────────────────
@_require_admin
async def post_approve(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "json_invalido"}, status=400)
    device_id = (body or {}).get("device_id")
    if not device_id:
        return web.json_response({"error": "falta_device_id"}, status=400)
    from soul_table_governance import connect_db
    conn = await connect_db()
    try:
        token = await approve_and_issue(conn, device_id, approved_by="William")  # LA COMPUERTA: firma el token
    except RuntimeError as e:
        return web.json_response({"error": "no_aprobable", "detail": str(e)}, status=409)
    finally:
        await conn.close()
    # No devolvemos el token completo a la UI (es credencial); solo confirmación + a dónde va.
    return web.json_response({"ok": True, "device_id": device_id, "agent": token["agent"],
                             "jti": token["jti"], "expires_at": token["expires_at"],
                             "note": "token emitido; entregar al device via el canal de provisión seguro"})


@_require_admin
async def post_reject(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "json_invalido"}, status=400)
    device_id = (body or {}).get("device_id")
    if not device_id:
        return web.json_response({"error": "falta_device_id"}, status=400)
    from soul_table_governance import connect_db
    conn = await connect_db()
    try:
        await reject(conn, device_id)
    finally:
        await conn.close()
    return web.json_response({"ok": True, "device_id": device_id, "action": "rejected"})


@_require_admin
async def post_revoke(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "json_invalido"}, status=400)
    device_id = (body or {}).get("device_id")
    reason = (body or {}).get("reason", "revoked_via_ui")
    if not device_id:
        return web.json_response({"error": "falta_device_id"}, status=400)
    from soul_table_governance import connect_db
    conn = await connect_db()
    try:
        row = await conn.fetchrow(
            "SELECT agent, metadata FROM soul_v3.authorized_devices WHERE device_id=$1", device_id)
        if not row:
            return web.json_response({"error": "device_desconocido"}, status=404)
        meta = row["metadata"] or {}
        if isinstance(meta, str):
            meta = json.loads(meta)
        jti = meta.get("token_jti")
        if not jti:
            return web.json_response({"error": "sin_jti", "detail": "device sin token_jti en metadata (¿emitido antes del fix?)"}, status=409)
        async with conn.transaction():
            await conn.execute(
                """INSERT INTO soul_v3.revoked_tokens (token_jti, device_id, agent, revoked_by, reason)
                   VALUES ($1,$2,$3,'William',$4) ON CONFLICT (token_jti) DO NOTHING""",
                jti, device_id, row["agent"], reason)
            await conn.execute(
                "UPDATE soul_v3.authorized_devices SET status='revoked', revoked_at=now(), revocation_reason=$2 WHERE device_id=$1",
                device_id, reason)
    finally:
        await conn.close()
    return web.json_response({"ok": True, "device_id": device_id, "action": "revoked", "jti": jti})


def make_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/devices/pending", get_pending)
    app.router.add_get("/devices", get_devices)
    app.router.add_get("/devices/status", get_status)
    app.router.add_post("/devices/approve", post_approve)
    app.router.add_post("/devices/reject", post_reject)
    app.router.add_post("/devices/revoke", post_revoke)
    return app


def main() -> None:
    load_or_create_admin_token()  # asegura que exista
    bind = os.environ.get("SEAL_DEVICES_BIND", "127.0.0.1")
    port = int(os.environ.get("SEAL_DEVICES_PORT", "8779"))
    web.run_app(make_app(), host=bind, port=port)


async def _selftest() -> None:
    """Verify-by-effect: auth fail-closed + los endpoints responden. Usa aiohttp TestClient."""
    from aiohttp.test_utils import TestServer, TestClient
    tok = load_or_create_admin_token()
    app = make_app()
    async with TestClient(TestServer(app)) as client:
        # (1) sin token → 401 en TODOS
        for m, path in [("get", "/devices/pending"), ("get", "/devices"), ("get", "/devices/status"),
                        ("post", "/devices/approve"), ("post", "/devices/reject"), ("post", "/devices/revoke")]:
            r = await getattr(client, m)(path, json={"device_id": "x"})
            assert r.status == 401, (path, r.status)
        # (2) token MALO → 401
        r = await client.get("/devices/pending", headers={"X-William-Token": "malo"})
        assert r.status == 401, r.status
        # (3) token BUENO → 200 en los GET (lecturas)
        for path in ["/devices/pending", "/devices", "/devices/status"]:
            r = await client.get(path, headers={"X-William-Token": tok})
            assert r.status == 200, (path, r.status)
        # (4) approve sin device_id → 400 (pero autenticado)
        r = await client.post("/devices/approve", headers={"X-William-Token": tok}, json={})
        assert r.status == 400, r.status
        # (5) approve de device inexistente → 409 (no aprobable)
        r = await client.post("/devices/approve", headers={"X-William-Token": tok}, json={"device_id": "device-inexistente-xyz"})
        assert r.status == 409, r.status
    print("seal_devices_api: auth fail-closed (401 sin/mal token) + GETs 200 + approve 400/409 gateado, por efecto")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        import asyncio
        asyncio.run(_selftest())
    else:
        main()
