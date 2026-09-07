#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""seal_central_signer.py — Emisor central HUMAN-GATED de device-tokens (Fase-3 seguridad, carril NEXUS).

Cierra el P6 EN CÓDIGO (antes era diseño): el token de un device se emite SOLO tras la aprobación
explícita de William. Flujo:
    device (seal_csr) → CSR firmado → submit_csr() → pending_device_registrations
    William revisa list_pending() → approve_and_issue() [LA COMPUERTA HUMANA] → central FIRMA el token
    → authorized_devices (active) → el device lo guarda (seal_token_store) y lo PRESENTA (no se auto-firma).

Clave de firma de SOUL central: Ed25519, generada UNA vez y persistida 0600. Solo central la tiene (firma);
los devices validan con la pública. NADA auto-firma: approve_and_issue REQUIERE un pending + un approved_by.

Sin red. Usa asyncpg (connect_db del gobierno) + seal_token/seal_csr. cryptography ya instalado.
"""
from __future__ import annotations
import base64
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives import serialization

sys.path.insert(0, str(Path(__file__).resolve().parent))
from seal_token import issue_token
from seal_csr import verify_csr_self_signature  # valida la auto-firma del CSR (proof-of-possession)

_CENTRAL_DIR = Path(os.environ.get("SEAL_CENTRAL_DIR", str(Path.home() / ".seal" / "central")))
_KEY_PATH = _CENTRAL_DIR / "soul_signing_ed25519.pem"


# ── Clave de firma de SOUL central (persistida, 0600) ─────────────────────────
def load_or_create_central_key() -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    """Carga la privada de firma de SOUL central; la genera+persiste (0600) la primera vez.
    SOLO central tiene esta clave. Los devices reciben/pinnean la PÚBLICA para validar tokens."""
    _CENTRAL_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(_CENTRAL_DIR, 0o700)
    except Exception:
        pass
    if _KEY_PATH.exists():
        priv = serialization.load_pem_private_key(_KEY_PATH.read_bytes(), password=None)
    else:
        priv = Ed25519PrivateKey.generate()
        pem = priv.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        _KEY_PATH.write_bytes(pem)
        try:
            os.chmod(_KEY_PATH, 0o600)
        except Exception:
            pass
    return priv, priv.public_key()


def central_public_key_b64() -> str:
    """La pública de SOUL (base64 raw) para distribuir/pinnear en los devices."""
    _, pub = load_or_create_central_key()
    raw = pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


# ── Flujo CSR → pending → aprobación humana → emisión ─────────────────────────
async def submit_csr(conn, csr: dict) -> tuple[bool, str]:
    """El device manda su CSR. Se valida la AUTO-FIRMA (proof-of-possession) y se encola en pending.
    NO emite token. Devuelve (ok, razon). Fail-closed."""
    required = {"device_id", "device_fingerprint", "agent", "pubkey", "requested_at", "nonce", "sig"}
    if not isinstance(csr, dict) or not required <= set(csr):
        return False, "csr_invalido"
    # Verificar que el device posee la privada del pubkey que declara (proof-of-possession).
    # verify_csr_self_signature retorna Tuple[bool,str] — DEBE desempacarse. Usar la tupla como
    # bool directo es un bug (tupla no-vacía siempre truthy → bypass). Fix NEXUS (catch FABLE 2026-07-02).
    try:
        raw = base64.b64decode(csr["pubkey"])
        dev_pub = Ed25519PublicKey.from_public_bytes(raw)
    except Exception:
        return False, "pubkey_invalida"
    ok_sig, why_sig = verify_csr_self_signature(csr, dev_pub)
    if not ok_sig:
        # Devolver la razón DESCRIPTIVA directa (agent_no_reconocido, firma_invalida, *_format_invalido…)
        # en vez de prefijar siempre "auto_firma_invalida" — para que la UI muestre el error real.
        return False, why_sig
    import json
    await conn.execute(
        """INSERT INTO soul_v3.pending_device_registrations
             (device_id, device_fingerprint, agent, csr_data, expires_at)
           VALUES ($1,$2,$3,$4::jsonb, now() + interval '7 days')
           ON CONFLICT (device_id) DO UPDATE SET csr_data=EXCLUDED.csr_data, created_at=now()""",
        csr["device_id"], csr["device_fingerprint"], csr["agent"], json.dumps(csr))
    return True, "encolado_pendiente_aprobacion"


async def list_pending(conn) -> list[dict]:
    """Lo que William revisa antes de aprobar."""
    rows = await conn.fetch(
        "SELECT device_id, agent, device_fingerprint, created_at FROM soul_v3.pending_device_registrations ORDER BY created_at")
    return [dict(r) for r in rows]


async def approve_and_issue(conn, device_id: str, approved_by: str = "William", ttl_s: int = 86400) -> dict:
    """LA COMPUERTA HUMANA. Solo William/admin la invoca. Requiere un pending existente.
    Verifica el CSR pendiente → FIRMA el device-token con la clave de SOUL → lo mueve a authorized_devices.
    Devuelve el token firmado (para entregar al device). Fail-closed: sin pending → error."""
    row = await conn.fetchrow(
        "SELECT device_fingerprint, agent, csr_data FROM soul_v3.pending_device_registrations WHERE device_id=$1",
        device_id)
    if not row:
        raise RuntimeError(f"no hay CSR pendiente para device '{device_id}' — nada que aprobar")
    fp, agent = row["device_fingerprint"], row["agent"]

    priv, _ = load_or_create_central_key()
    token = issue_token(agent=agent, device_id=device_id, fingerprint=fp, private_key=priv, ttl_s=ttl_s)

    import json as _json
    # Guardar el jti del token emitido en metadata → permite revocación por device_id (sin ALTER de schema).
    meta = _json.dumps({"token_jti": token["jti"], "issued_at": token["issued_at"], "expires_at": token["expires_at"]})
    await conn.execute(
        """INSERT INTO soul_v3.authorized_devices (device_id, device_fingerprint, agent, registered_by, status, metadata)
           VALUES ($1,$2,$3,$4,'active',$5::jsonb)
           ON CONFLICT (device_id) DO UPDATE SET status='active', device_fingerprint=EXCLUDED.device_fingerprint,
             registered_by=EXCLUDED.registered_by, metadata=EXCLUDED.metadata, updated_at=now()""",
        device_id, fp, agent, approved_by, meta)
    await conn.execute("DELETE FROM soul_v3.pending_device_registrations WHERE device_id=$1", device_id)
    return token


async def submit_csr_smart(conn, csr: dict) -> dict:
    """Modelo MÁQUINA-CONFIANZA (pedido de William "deshabilitar la aprobación redundante", guard FABLE):
    valida PoP y, si el FINGERPRINT (la máquina física) YA está autorizado por William (cualquier agente
    activo), AUTO-APRUEBA este clon nuevo en esa máquina (emite token directo). Si la máquina es DESCONOCIDA
    → va a pending (William revisa la máquina nueva). Calibrado: NO es blanket-disable; una máquina se aprueba
    UNA vez (human-gate), y los clones nuevos EN ESA MISMA máquina se auto-activan.
    Devuelve {ok, approved, token?|pending?, reason}."""
    required = {"device_id", "device_fingerprint", "agent", "pubkey", "requested_at", "nonce", "sig"}
    if not isinstance(csr, dict) or not required <= set(csr):
        return {"ok": False, "reason": "csr_invalido"}
    try:
        raw = base64.b64decode(csr["pubkey"])
        dev_pub = Ed25519PublicKey.from_public_bytes(raw)
    except Exception:
        return {"ok": False, "reason": "pubkey_invalida"}
    ok_sig, why_sig = verify_csr_self_signature(csr, dev_pub)
    if not ok_sig:
        return {"ok": False, "reason": why_sig}

    fp, agent, device_id = csr["device_fingerprint"], csr["agent"], csr["device_id"]
    # REVOCACIÓN PER-AGENTE DOMINA la confianza-de-máquina (fix hueco NEXUS 2026-07-04, cazado por
    # efecto verificando mi propia rotación): si ESTE (fingerprint, agent) está REVOCADO, NO auto-aprobar
    # aunque la máquina tenga OTRO agente activo — si no, revocar un clon se bypassa vía otro clon de la
    # misma máquina (y el ON CONFLICT lo revivía a 'active'). Re-habilitar un clon revocado = gate humano, no auto.
    revoked_here = await conn.fetchrow(
        "SELECT 1 FROM soul_v3.authorized_devices WHERE device_fingerprint=$1 AND agent=$2 AND status='revoked' LIMIT 1",
        fp, agent)
    if revoked_here:
        return {"ok": False, "approved": False, "reason": "agente_revocado_en_esta_maquina"}
    # ¿la MÁQUINA (fingerprint) ya fue aprobada por William? (cualquier agente activo en ella)
    # Cross-agent trust is exposed only as a boolean SECURITY DEFINER
    # capability. Per-agent endpoint logins never read other agents' rows.
    trusted = await conn.fetchval(
        "SELECT soul_v3.seal_sync_machine_is_trusted($1)", fp)
    if trusted:
        # AUTO-APROBAR este agente en la máquina de confianza (emitir token directo)
        import json as _json
        priv, _ = load_or_create_central_key()
        token = issue_token(agent=agent, device_id=device_id, fingerprint=fp, private_key=priv, ttl_s=86400)
        meta = _json.dumps({"token_jti": token["jti"], "auto_approved": True, "reason": "maquina_ya_autorizada"})
        # ON CONFLICT sobre (device_fingerprint, agent) — la uniqueness REAL de un clon (un agente por
        # máquina). Cubre la ROTACIÓN/re-registro: mismo (fp,agent) con device_id posiblemente NUEVO
        # → actualiza la fila existente (no crashea por la constraint fp_agent). Fix bug NEXUS 2026-07-03
        # (cazado por efecto testeando rotación: device_id nuevo para (fp,agent) existente → UniqueViolation).
        await conn.execute(
            """INSERT INTO soul_v3.authorized_devices (device_id, device_fingerprint, agent, registered_by, status, metadata)
               VALUES ($1,$2,$3,'William(auto:trusted-machine)','active',$4::jsonb)
               ON CONFLICT (device_fingerprint, agent) DO UPDATE SET device_id=EXCLUDED.device_id,
                 status='active', metadata=EXCLUDED.metadata, updated_at=now()""",
            device_id, fp, agent, meta)
        await conn.execute("DELETE FROM soul_v3.pending_device_registrations WHERE device_id=$1", device_id)
        return {"ok": True, "approved": True, "token": token, "reason": "auto_aprobado_maquina_confiable"}

    # máquina NUEVA/desconocida → pending (William revisa)
    ok, reason = await submit_csr(conn, csr)
    return {"ok": ok, "approved": False, "reason": reason}


async def reject(conn, device_id: str) -> None:
    await conn.execute("DELETE FROM soul_v3.pending_device_registrations WHERE device_id=$1", device_id)


async def _selftest():
    """Verifica por efecto contra soul_v3 en una transacción con ROLLBACK (no persiste nada)."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "memory"))
    from soul_table_governance import connect_db
    from seal_csr import generate_device_keypair, build_csr
    from seal_sync_auth import authorize_sync

    _, cpub = load_or_create_central_key()
    conn = await connect_db()
    tr = conn.transaction()
    await tr.start()
    try:
        # device arma su CSR
        dpriv, _ = generate_device_keypair()
        csr = build_csr("NEXUS", dpriv)
        did = csr["device_id"]

        # (0) NEGATIVO (catch FABLE): CSR con firma forjada → submit_csr DEBE rechazar (no encolar).
        #     Prueba que el proof-of-possession se enforza de verdad (no el bypass de tupla-truthy).
        forged = dict(csr); forged["sig"] = base64.b64encode(b"\x00" * 64).decode("ascii")
        fok, fwhy = await submit_csr(conn, forged)
        assert not fok and "firma_invalida" in fwhy, ("forjado debió rechazarse", fok, fwhy)
        assert not any(p["device_id"] == did for p in await list_pending(conn)), "forjado NO debió encolarse"

        # (1) submit → encola pendiente (NO emite token)
        ok, why = await submit_csr(conn, csr)
        assert ok, why
        pend = await list_pending(conn)
        assert any(p["device_id"] == did for p in pend), "no quedó pendiente"

        # (2) aprobar SIN pending de otro device → falla
        try:
            await approve_and_issue(conn, "device-inexistente"); assert False
        except RuntimeError:
            pass

        # (3) William aprueba → central FIRMA el token → authorized_devices
        token = await approve_and_issue(conn, did, approved_by="William")
        assert token["agent"] == "NEXUS" and token["device_id"] == did

        # (4) el token firmado por central VALIDA contra la pública de SOUL (authorize_sync)
        ok, agent, why = authorize_sync(token, cpub)
        assert ok and agent == "NEXUS", (ok, agent, why)

        # (5) ya no está en pending; está en authorized_devices
        assert not any(p["device_id"] == did for p in await list_pending(conn))
        r = await conn.fetchrow("SELECT status FROM soul_v3.authorized_devices WHERE device_id=$1", did)
        assert r and r["status"] == "active"
        print("seal_central_signer: CSR→pending→APROBACIÓN humana→token firmado por central→valida, todo por efecto")
    finally:
        await tr.rollback()   # nada persiste en soul_v3
        await conn.close()


if __name__ == "__main__":
    import asyncio
    asyncio.run(_selftest())
