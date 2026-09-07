#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""seal_sync_endpoint.py — Endpoint HTTP de SYNC device→central (Fase-3 transporte, carril NEXUS).

Transporte elegido: HTTP over Tailscale (escala a N devices, estándar, mTLS-ready). El device hace
POST /sync con {auth_token, payload:{batch:[memorias]}}; central autentica el token (firma/exp/revocación),
enforcea el scope (device de A no escribe B) y persiste. Todo el trabajo de auth+scope+persist ya vive en
minisoul_sync_central.central_sync_handler_async — esto es SOLO el transporte HTTP que lo invoca.

Seguridad:
  · Token Ed25519 en el body (validado con la PÚBLICA de SOUL — la privada NUNCA está aquí).
  · revoked_jtis se lee fresco de soul_v3.revoked_tokens en cada request (fail-closed si revocado).
  · El agente autenticado sale del token FIRMADO, no de ningún campo del request (anti-spoof/IDOR).
  · Se sirve DENTRO del tailnet (bind configurable); mTLS/cert-pin (seal_mtls) = capa extra encima.

Núcleo testeable (handle_sync) separado del transporte (ruta aiohttp) para verify-by-effect con tx rollback.
"""
from __future__ import annotations
import base64
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))                    # tools/
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "memory"))     # memory/

import asyncpg
from aiohttp import web
from seal_central_signer import central_public_key_b64, load_or_create_central_key
from seal_sync_auth import issue_revocation_receipt
from minisoul_sync_central import central_sync_handler_async


SYNC_AGENTS = ("ADA", "ALICE", "DUM", "FABLE", "JARVIS", "NEXUS")


def _agent_dsn(agent: str) -> str:
    """Return the dedicated per-agent DB login configured for this endpoint.

    The signed token/CSR selects a connection, never a settable GUC. RLS then
    derives identity from ``session_user``.
    """
    normalized = str(agent or "").strip().upper()
    if normalized not in SYNC_AGENTS:
        raise ValueError("agent_no_reconocido")
    dsn = os.environ.get(f"SEAL_SYNC_DB_DSN_{normalized}", "").strip()
    if not dsn:
        raise RuntimeError(f"dsn_sync_no_configurado:{normalized}")
    return dsn


async def _connect_agent(agent: str):
    return await asyncpg.connect(_agent_dsn(agent), timeout=8)


def _authenticate_token_agent(token: dict, soul_pubkey) -> tuple[bool, str, str]:
    """Verify signature/expiry before choosing a per-agent DB principal."""
    from seal_sync_auth import authorize_sync

    ok, agent, reason = authorize_sync(token, soul_pubkey, ())
    agent = str(agent or "").strip().upper()
    if not ok:
        return False, "", reason
    if agent not in SYNC_AGENTS:
        return False, "", "agent_no_reconocido"
    return True, agent, "ok"


async def _load_revoked_jtis(conn) -> set[str]:
    """Lee los JTIs revocados FRESCO de central. La columna es 'token_jti' (NO 'jti').
    FAIL-CLOSED DE VERDAD: si no se puede leer la lista, PROPAGA el error — el caller RECHAZA el sync.
    (Antes: query con columna mal + except que devolvía set() vacío = fail-OPEN → revocación bypasseada.
    Bug crítico cazado por verify-by-effect 2026-07-02: un device revocado seguía sincronizando.)"""
    rows = await conn.fetch("SELECT token_jti FROM soul_v3.revoked_tokens")
    return {r["token_jti"] for r in rows}


async def handle_sync(body: dict, conn, soul_pubkey, revoked_jtis: set[str]) -> tuple[int, dict]:
    """NÚCLEO testeable: parsea el body y delega en central_sync_handler_async.
    Devuelve (http_status, result). Fail-closed ante body malformado."""
    if not isinstance(body, dict):
        return 400, {"status": "rejected", "reason": "body_invalido"}
    token = body.get("auth_token")
    payload = body.get("payload") or {}
    batch = payload.get("batch") if isinstance(payload, dict) else None
    if not isinstance(token, dict) or not isinstance(batch, list):
        return 400, {"status": "rejected", "reason": "estructura_invalida"}
    result = await central_sync_handler_async(token, batch, conn, soul_pubkey, revoked_jtis)
    status = 200 if result.get("status") == "ok" else 401
    return status, result


def _attach_revocation_receipt(
    status: int,
    result: dict,
    body: dict,
    token: dict,
    private_key,
) -> dict:
    """Firma sólo un rechazo revocado ligado al nonce de esta petición."""
    request_nonce = body.get("request_nonce") if isinstance(body, dict) else None
    if not (
        status == 401
        and result.get("reason") == "revocado"
        and isinstance(request_nonce, str)
        and 16 <= len(request_nonce) <= 128
    ):
        return result
    signed = dict(result)
    signed["revocation_receipt"] = issue_revocation_receipt(
        token, request_nonce, private_key
    )
    return signed


async def _route_sync(request: web.Request) -> web.Response:
    """Ruta HTTP: carga pública central + revoked frescos, invoca el núcleo, responde JSON."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"status": "rejected", "reason": "json_invalido"}, status=400)
    priv, pub = load_or_create_central_key()
    token = body.get("auth_token") if isinstance(body, dict) else None
    ok, agent, reason = _authenticate_token_agent(token, pub)
    if not ok:
        return web.json_response({"status": "rejected", "reason": reason}, status=401)
    try:
        conn = await _connect_agent(agent)
    except Exception:
        return web.json_response(
            {"status": "rejected", "reason": "db_identity_no_disponible"}, status=503)
    try:
        try:
            revoked = await _load_revoked_jtis(conn)
        except Exception:
            # FAIL-CLOSED: si no puedo verificar la lista de revocados, NO acepto el sync
            # (mejor rechazar que aceptar un token potencialmente revocado).
            return web.json_response(
                {"status": "rejected", "reason": "no_puedo_verificar_revocacion"}, status=503)
        status, result = await handle_sync(body, conn, pub, revoked)
    finally:
        await conn.close()
    result = _attach_revocation_receipt(status, result, body, token, priv)
    return web.json_response(result, status=status)


async def _route_csr(request: web.Request) -> web.Response:
    """Device-facing: un device-en-creación SUBMITEA su CSR (proof-of-possession) → cola pending.
    NO emite token (eso es la aprobación human-gated de William, aparte). Valida la auto-firma del CSR
    (submit_csr) y encola. Es el punto (a) del AUTH de creación de clones (carril NEXUS)."""
    try:
        csr = await request.json()
    except Exception:
        return web.json_response({"ok": False, "reason": "json_invalido"}, status=400)
    from seal_central_signer import submit_csr_smart
    from seal_csr import verify_csr_self_signature
    try:
        raw = base64.b64decode(csr["pubkey"])
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        dev_pub = Ed25519PublicKey.from_public_bytes(raw)
    except Exception:
        return web.json_response({"ok": False, "reason": "pubkey_invalida"}, status=400)
    ok, reason = verify_csr_self_signature(csr, dev_pub)
    if not ok:
        return web.json_response({"ok": False, "reason": reason}, status=400)
    agent = str(csr.get("agent") or "").strip().upper()
    try:
        conn = await _connect_agent(agent)
    except Exception:
        return web.json_response(
            {"ok": False, "reason": "db_identity_no_disponible"}, status=503)
    try:
        # Modelo máquina-confianza: si la máquina ya está autorizada por William → auto-aprueba (token directo);
        # si es nueva → pending. Devuelve {ok, approved, token?, reason}.
        res = await submit_csr_smart(conn, csr)
    except Exception:
        return web.json_response({"ok": False, "reason": "error_interno"}, status=500)
    finally:
        await conn.close()
    if res.get("ok"):
        res = dict(res)
        res["central_public_key_b64"] = central_public_key_b64()
    return web.json_response(res, status=200 if res.get("ok") else 400)


async def handle_soul(body: dict, conn, soul_pubkey, revoked_jtis: set[str]) -> tuple[int, dict]:
    """NÚCLEO testeable: sirve el ALMA RICA de un clon a su token de device (carril NEXUS, boot desde Spark).
    Flujo (pedido de William 4-jul): al ACTIVAR/despertar, el clon baja su alma rica (identidad+OCEAN+reglas+anclas).
    PRIVACIDAD: el agente sale del TOKEN FIRMADO (authorize_sync), NO de un param → un token solo baja SU propia alma
    (soul_mirror_profile exige agent==authenticated_agent). Revocación DOMINA (token revocado → 401)."""
    if not isinstance(body, dict):
        return 400, {"ok": False, "reason": "body_invalido"}
    token = body.get("auth_token") or body.get("token")
    if not isinstance(token, dict):
        return 400, {"ok": False, "reason": "estructura_invalida"}
    from seal_sync_auth import authorize_sync
    ok, agent, reason = authorize_sync(token, soul_pubkey, revoked_jtis)
    if not ok:
        return 401, {"ok": False, "reason": reason}
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent))
    from soul_mirror_profile import soul_mirror_profile, SoulTooThin
    try:
        soul = await soul_mirror_profile(conn, agent, authenticated_agent=agent)
    except SoulTooThin as e:
        return 409, {"ok": False, "reason": "alma_delgada", "detail": str(e)[:140]}
    result = {"ok": True, "agent": agent, "soul": soul}
    # FULL SOUL (pedido William 5-jul: «no está replicando su inteligencia, el clon no sabe nada»):
    # si full=true, adjuntar TODAS las memorias del agente (scoped al token) → el clon replica su
    # conocimiento completo en el device, no solo el perfil de 2680 chars. Privacidad: agent sale del token.
    if isinstance(body, dict) and body.get("full"):
        # GARANTÍA DE ANCLAS (fix NEXUS 5-jul, catch ALICE/FABLE por efecto: «los clones salen fríos/genéricos»).
        # El top-800-por-importancia puede DEJAR AFUERA las anclas de identidad y CALIDEZ (emotional/relationship):
        # p.ej. NEXUS tiene 23.969 memorias importance>=7, así que sus 30 emocionales quedaban sepultadas y NUNCA
        # llegaban al clon → esencia sin vínculo. Ahora traemos SIEMPRE todas las anclas del alma + rellenamos con
        # las top generales, dedup por contenido, anclas primero. Sin esto, el boot cálido no tiene qué renderizar.
        ANCHOR_CATS = ["identity", "persona", "core", "relationship", "emotional", "ocean", "rules"]
        anchors = await conn.fetch(
            "SELECT category, content, importance FROM soul_v3.memories WHERE upper(agent)=$1 "
            "AND category = ANY($2::text[]) ORDER BY importance DESC, created_at DESC", agent, ANCHOR_CATS)
        general = await conn.fetch(
            "SELECT category, content, importance FROM soul_v3.memories WHERE upper(agent)=$1 "
            "ORDER BY importance DESC, created_at DESC LIMIT 800", agent)
        seen, mems = set(), []
        for r in list(anchors) + list(general):
            c = r["content"]
            if c in seen:
                continue
            seen.add(c)
            mems.append({"category": r["category"], "content": c, "importance": r["importance"]})
        result["memories"] = mems
        result["memory_count"] = len(mems)
        result["anchor_count"] = len(anchors)  # cuántas anclas de alma garantizadas (verificable por efecto)
    return 200, result


async def _route_soul(request: web.Request) -> web.Response:
    """Device-facing: un clon ACTIVÁNDOSE baja su alma rica presentando su token firmado. Fail-closed."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "reason": "json_invalido"}, status=400)
    _, pub = load_or_create_central_key()
    token = (body.get("auth_token") or body.get("token")) if isinstance(body, dict) else None
    ok, agent, reason = _authenticate_token_agent(token, pub)
    if not ok:
        return web.json_response({"ok": False, "reason": reason}, status=401)
    try:
        conn = await _connect_agent(agent)
    except Exception:
        return web.json_response(
            {"ok": False, "reason": "db_identity_no_disponible"}, status=503)
    try:
        try:
            revoked = await _load_revoked_jtis(conn)
        except Exception:
            return web.json_response({"ok": False, "reason": "no_puedo_verificar_revocacion"}, status=503)
        status, result = await handle_soul(body, conn, pub, revoked)
    finally:
        await conn.close()
    return web.json_response(result, status=status)


async def _route_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "up", "service": "seal_sync_endpoint"})


def make_app() -> web.Application:
    app = web.Application(client_max_size=8 * 1024 * 1024)  # cap batch a 8MB
    app.router.add_post("/sync", _route_sync)
    app.router.add_post("/csr", _route_csr)          # device submitea CSR → pending (creación de clon)
    app.router.add_post("/soul", _route_soul)        # clon activándose baja su alma rica (boot desde Spark)
    app.router.add_get("/health", _route_health)
    return app


def main() -> None:
    bind = os.environ.get("SEAL_SYNC_BIND", "127.0.0.1")   # deploy: la IP del tailnet de central
    port = int(os.environ.get("SEAL_SYNC_PORT", "8778"))
    web.run_app(make_app(), host=bind, port=port)


async def _selftest() -> None:
    """Verify-by-effect. El caso ACEPTADO commitea de verdad (como el server real, SIN tx externa)
    y limpia su canario — envolver en una tx externa rompe la tx anidada del handler (savepoint) y
    da un InFailedSQLTransactionError falso; el server real no usa tx externa. Los casos de RECHAZO
    no insertan, así que no ensucian nada."""
    from seal_token import issue_token, generate_keypair, device_fingerprint
    priv, pub = load_or_create_central_key()
    fp = device_fingerprint()
    conn = await _connect_agent("JARVIS")
    canary = "CANARIO_selftest_" + fp[:10]
    try:
        revoked: set[str] = set()
        tok = issue_token("JARVIS", "dev-test", fp, priv, ttl_s=3600)  # token real firmado por central

        # (1) memoria PROPIA (agent=JARVIS) → aceptada + ESCRITA de verdad (commit), luego se limpia
        st, res = await handle_sync(
            {"auth_token": tok, "payload": {"batch": [{"agent": "JARVIS", "category": "insight", "content": canary, "importance": 5, "scope": "private"}]}},
            conn, pub, revoked)
        assert st == 200 and res.get("status") == "ok" and res.get("written", 0) >= 1, res
        landed = await conn.fetchrow("SELECT id FROM soul_v3.memories WHERE content=$1", canary)
        assert landed, "la memoria aceptada no aterrizó en soul_v3.memories"

        # (2) memoria CROSS-AGENT (token JARVIS intenta escribir agent=NEXUS) → descartada por scope
        st2, res2 = await handle_sync(
            {"auth_token": tok, "payload": {"batch": [{"agent": "NEXUS", "content": "robo", "type": "insight"}]}},
            conn, pub, revoked)
        assert res2.get("rejected_cross_agent", 0) >= 1, res2

        # (3) token FORJADO (firmado por clave que NO es la de SOUL) → 401 fail-closed
        evilpriv, _ = generate_keypair()
        evil = issue_token("JARVIS", "dev-test", fp, evilpriv, ttl_s=3600)
        st3, res3 = await handle_sync({"auth_token": evil, "payload": {"batch": []}}, conn, pub, revoked)
        assert st3 == 401 and res3.get("status") == "rejected", res3

        # (4) body malformado → 400
        st4, res4 = await handle_sync({"nope": 1}, conn, pub, revoked)
        assert st4 == 400, res4

        # (5) revocado → 401
        st5, res5 = await handle_sync({"auth_token": tok, "payload": {"batch": []}}, conn, pub, {tok["jti"]})
        assert st5 == 401 and res5.get("reason") == "revocado", res5

        print("seal_sync_endpoint: acepta+escribe (canario) + cross-agent + 401-forjado + 400-malformado + 401-revocado, por efecto")
    finally:
        # limpieza del canario (fue prueba) — no ensuciar la DB real
        try:
            await conn.execute("DELETE FROM soul_v3.memories WHERE content=$1", canary)
        except Exception:
            pass
        await conn.close()


if __name__ == "__main__":
    import asyncio
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        asyncio.run(_selftest())
    else:
        main()
