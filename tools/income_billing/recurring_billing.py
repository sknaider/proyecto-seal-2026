#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
recurring_billing.py — Infra de COBRO AUTOMÁTICO + provisioning (carril NEXUS).
================================================================================
Objetivo (William 24-jun): que el ingreso recurrente se cobre y se entregue SOLO,
sin trabajo manual. Esta es la pieza de infra: cuando un cliente paga, el sistema
verifica el webhook, evita duplicados, y PROVISIONA el acceso (habilita el tier /
emite credencial) automáticamente. Lo único no-automático es la venta inicial.

Diseño:
  * Gateway-AGNÓSTICO: adaptador enchufable (CulqiAdapter para Perú-PYMES;
    MoRAdapter [Lemon Squeezy/Paddle] para clientes globales con IVA automático).
  * SEGURIDAD (no negociable): el webhook se VERIFICA por firma HMAC (nunca confiar
    en un webhook sin firmar) e IDEMPOTENCIA (un mismo evento no provisiona dos veces).
  * Checkout HOSTEADO por la pasarela → NUNCA tocamos datos de tarjeta (cero PCI scope).
  * Lógica pura + testeable sin red ni claves (las claves se inyectan en deploy).
"""
from __future__ import annotations
import hmac, hashlib, json, time
from dataclasses import dataclass, field, asdict
from typing import Callable, Optional


# ───────────────────────── modelo de entitlement ─────────────────────────
@dataclass
class Entitlement:
    customer_id: str
    plan: str
    status: str = "active"            # active | past_due | canceled
    provisioned_at: float = 0.0
    api_key: Optional[str] = None     # credencial emitida al provisionar (si aplica)


class Ledger:
    """Estado de suscripciones + idempotencia de webhooks (sketch; en prod = DB)."""
    def __init__(self):
        self.entitlements: dict[str, Entitlement] = {}   # customer_id -> Entitlement
        self._seen_events: set[str] = set()              # event_id ya procesados

    def already_processed(self, event_id: str) -> bool:
        return event_id in self._seen_events

    def mark_processed(self, event_id: str):
        self._seen_events.add(event_id)


# ───────────────────────── seguridad del webhook ─────────────────────────
def verify_signature(secret: str, raw_body: bytes, signature: str) -> bool:
    """HMAC-SHA256 del cuerpo crudo vs la firma del header. Comparación
    en tiempo constante (anti timing-attack). Rechaza si no coincide."""
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


# ───────────────────────── adaptadores de pasarela ─────────────────────────
class GatewayAdapter:
    """Interfaz mínima. Cada pasarela normaliza su webhook a un evento canónico:
    {event_id, type, customer_id, plan}. type ∈ {payment_succeeded, payment_failed,
    subscription_canceled}."""
    webhook_secret: str = ""
    def parse_event(self, payload: dict) -> dict:  # pragma: no cover - override
        raise NotImplementedError


class CulqiAdapter(GatewayAdapter):
    """Perú-PYMES (soles, tarjetas nacionales + Yape/Plin, recurrente)."""
    def __init__(self, webhook_secret: str):
        self.webhook_secret = webhook_secret
    def parse_event(self, payload: dict) -> dict:
        t = {"charge.succeeded": "payment_succeeded",
             "charge.failed": "payment_failed",
             "subscription.canceled": "subscription_canceled"}.get(payload.get("type"), "unknown")
        d = payload.get("data", {})
        return {"event_id": payload.get("id", ""), "type": t,
                "customer_id": d.get("customer_id") or d.get("email", ""),
                "plan": d.get("plan", "")}


class MoRAdapter(GatewayAdapter):
    """Clientes globales (Lemon Squeezy/Paddle) — el MoR maneja IVA/impuestos solo."""
    def __init__(self, webhook_secret: str):
        self.webhook_secret = webhook_secret
    def parse_event(self, payload: dict) -> dict:
        meta = payload.get("meta", {})
        t = {"order_created": "payment_succeeded", "subscription_payment_success": "payment_succeeded",
             "subscription_payment_failed": "payment_failed",
             "subscription_cancelled": "subscription_canceled"}.get(meta.get("event_name"), "unknown")
        attrs = payload.get("data", {}).get("attributes", {})
        return {"event_id": str(meta.get("event_id", "")), "type": t,
                "customer_id": attrs.get("user_email", ""), "plan": attrs.get("variant_name", "")}


# ───────────────────────── motor de cobro+provisioning ─────────────────────────
class BillingEngine:
    def __init__(self, adapter: GatewayAdapter, ledger: Ledger,
                 provision: Callable[[str, str], Optional[str]]):
        """provision(customer_id, plan) -> api_key|None : tu función que HABILITA el
        acceso (crear tenant, activar tier, emitir API key). Corre desatendida."""
        self.adapter = adapter
        self.ledger = ledger
        self.provision = provision

    def handle_webhook(self, raw_body: bytes, signature: str) -> dict:
        # 1) seguridad: firma
        if not verify_signature(self.adapter.webhook_secret, raw_body, signature):
            return {"ok": False, "error": "bad_signature", "http": 401}
        payload = json.loads(raw_body.decode())
        ev = self.adapter.parse_event(payload)
        eid = ev.get("event_id")
        if not eid:
            return {"ok": False, "error": "no_event_id", "http": 400}
        # 2) idempotencia
        if self.ledger.already_processed(eid):
            return {"ok": True, "idempotent": True, "http": 200}
        self.ledger.mark_processed(eid)
        # 3) acción según tipo
        cid, plan = ev["customer_id"], ev["plan"]
        if ev["type"] == "payment_succeeded":
            key = self.provision(cid, plan)            # PROVISIONING AUTOMÁTICO
            self.ledger.entitlements[cid] = Entitlement(
                cid, plan, "active", time.time(), key)
            return {"ok": True, "action": "provisioned", "customer": cid, "http": 200}
        if ev["type"] == "payment_failed":
            e = self.ledger.entitlements.get(cid)
            if e: e.status = "past_due"
            return {"ok": True, "action": "marked_past_due", "customer": cid, "http": 200}
        if ev["type"] == "subscription_canceled":
            e = self.ledger.entitlements.get(cid)
            if e: e.status = "canceled"
            return {"ok": True, "action": "canceled", "customer": cid, "http": 200}
        return {"ok": True, "action": "ignored", "type": ev["type"], "http": 200}


# ───────────────────────── self-test POR EFECTO ─────────────────────────
if __name__ == "__main__":
    SECRET = "whsec_test_123"
    provisioned = {}
    def fake_provision(cid, plan):
        key = "key_" + hashlib.sha256(f"{cid}{plan}".encode()).hexdigest()[:12]
        provisioned[cid] = (plan, key)
        return key

    eng = BillingEngine(CulqiAdapter(SECRET), Ledger(), fake_provision)

    def signed(body: dict):
        raw = json.dumps(body).encode()
        sig = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
        return raw, sig

    ok = True
    # 1) pago OK → provisiona
    raw, sig = signed({"id": "evt_1", "type": "charge.succeeded",
                       "data": {"customer_id": "cli@pyme.pe", "plan": "gtl_basico"}})
    r = eng.handle_webhook(raw, sig)
    t1 = r.get("action") == "provisioned" and "cli@pyme.pe" in provisioned
    ok &= t1; print("1 pago OK→provisiona:", "ok" if t1 else "FAIL", r)

    # 2) MISMO evento de nuevo → idempotente (no re-provisiona)
    r2 = eng.handle_webhook(raw, sig)
    t2 = r2.get("idempotent") is True
    ok &= t2; print("2 idempotencia:", "ok" if t2 else "FAIL", r2)

    # 3) firma inválida → rechazo 401
    r3 = eng.handle_webhook(raw, "deadbeef")
    t3 = r3.get("http") == 401
    ok &= t3; print("3 firma inválida→401:", "ok" if t3 else "FAIL", r3)

    # 4) pago fallido → past_due
    raw4, sig4 = signed({"id": "evt_2", "type": "charge.failed",
                         "data": {"customer_id": "cli@pyme.pe", "plan": "gtl_basico"}})
    r4 = eng.handle_webhook(raw4, sig4)
    t4 = eng.ledger.entitlements["cli@pyme.pe"].status == "past_due"
    ok &= t4; print("4 pago fallido→past_due:", "ok" if t4 else "FAIL", r4)

    print("\n", "✅ COBRO+PROVISIONING OK (firma, idempotencia, provisioning, dunning)"
          if ok else "⚠️ revisar", "— verificado por efecto")
    import sys; sys.exit(0 if ok else 1)
