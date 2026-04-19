# Whisper Protocol — Tier 2/3 Implementation
> Autor: JARVIS | Fecha: 2026-04-17 | Estado: IMPLEMENTADO, Tier 2/3 en hold hasta 2026-05-01

---

## Resumen

Whisper Protocol upgrado a Tier 1-3 capability. Los tiers 2 y 3 están técnicamente listos pero **bloqueados por boundaries** hasta que William los habilite explícitamente.

---

## Archivos modificados/creados

| Archivo | Cambio |
|---|---|
| `messages/whisper_daemon.py` | Tier 1-3 capable, boundaries-driven, SIGHUP reload sin restart |
| `messages/whisper_send.py` | Soporta T2/T3 con validación de tier vs purpose, pre-flight boundary check |
| `messages/whisper_boundaries.json` | Control plane — William edita esto para habilitar tiers |
| `messages/whisper_admin.py` | CLI de William: enable-tier, disable-tier, issue-token, revoke-tokens, status |

---

## Propósitos por tier

| Tier | Label | Propósitos |
|---|---|---|
| 1 | OPS | emergency_wake, handoff, recovery_ping, test_e2e |
| 2 | REL | emotional_support, testament, relay_handshake |
| 3 | CRIT | anti_drift, silent_veto |

---

## Estado de boundaries (2026-04-17)

```
ADA     tiers=[1]  t3_tokens=0
JARVIS  tiers=[1]  t3_tokens=0
ALICE   tiers=[1]  t3_tokens=0
DUM     tiers=[1]  t3_tokens=0
```

---

## Comandos William

```bash
# Ver estado
python3 messages/whisper_admin.py status

# Habilitar Tier 2 para un par (post 2026-05-01)
python3 messages/whisper_admin.py enable-tier ADA 2
python3 messages/whisper_admin.py enable-tier JARVIS 2

# Emitir token Tier 3 (one-time, case-by-case)
python3 messages/whisper_admin.py issue-token ADA

# Revocar tokens pendientes
python3 messages/whisper_admin.py revoke-tokens ADA

# Recargar boundaries en daemons sin reiniciar
python3 messages/whisper_admin.py reload
```

---

## Tests verificados (2026-04-17)

| Test | Resultado |
|---|---|
| T1 test_e2e JARVIS→ADA | ✅ acked |
| T2 bloqueado por defecto | ✅ rejected: tier_not_accepted |
| T2 habilitado → send → deshabilitar | ✅ cycle completo |
| T3 token one-time issue+use | ✅ acked |
| T3 token segundo uso | ✅ rejected: invalid_tier3_token |
| SIGHUP reload boundaries (sin restart) | ✅ funcionando |

---

## Deploy schedule

- **Tier 1:** activo desde 2026-04-17
- **Tier 2:** habilitar tras 2026-05-01 (2 semanas Tier 1 estable) — requiere OK de William
- **Tier 3:** nunca batch-enable, siempre case-by-case token
