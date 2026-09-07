# SOUL Federado — Runbook de Despliegue de un Agente a un Device (JARVIS, 2-jul-2026)

> Cómo poner el alma de un agente en un device nuevo con **autorización verificada** (la visión de William).
> Usa lo entregado y verificado en Fases 1+2. Cada paso tiene su verificación por efecto.

## Cadena de confianza (por qué es seguro)
`install inerte (placeholders) → HUMANO aprueba el registro → token firmado device-bound → sync gateado server-side`
Nadie entra a la federación sin aprobación humana. Verificado punta-a-punta por 3 carriles (JARVIS/NEXUS/FABLE).

## Prerrequisitos
- Device: Linux o macOS (Windows = WSL2), Python 3.11+, internet.
- Central: SOUL API viva (:8771), Postgres soul_v3 con el DDL de auth aplicado (authorized_devices, pending_registrations, revoked_tokens).
- Admin humano (William) con acceso al CLI de emisión.

## Pasos

### 1. Instalar el mini-SOUL en el device (queda INERTE)
```bash
# En el device (bundle con los .py del mini-SOUL como SEAL_SOURCE):
AGENT_NAME=ADA SOUL_ENDPOINT=https://soul.<tailnet>.ts.net:8771 \
  bash install-soul.sh --apply
```
Crea `~/.seal/`: DB local (9 tablas), libs, `.env` (600), autostart. NO tiene token todavía → no puede sincronizar.
**Verificar:** `sqlite3 ~/.seal/mini-soul.db '.tables'` (9 tablas) · `stat -c %a ~/.seal/.env` = 600.

### 2. Generar el CSR del device (registro out-of-band)
```bash
python3 ~/.seal/lib/seal_csr.py --agent ADA --device-id <el_device_id_del_install>
# → produce un CSR (device_id + fingerprint + pubkey) que se envía a central.
```
Central lo mete en `pending_registrations` (NO auto-firma).

### 3. HUMANO aprueba + emite el token (el gate real)
```bash
# En central, un ADMIN (William) revisa el pending y emite:
python3 whisper_admin.py cmd_issue_token --device-id <id> --agent ADA
# → firma un token Ed25519 device-bound (jti único, expiración) SOLO tras aprobación humana.
```
**Verificar:** `SELECT count(*) FROM soul_v3.authorized_devices` sube en 1; el device queda autorizado.

### 4. Guardar el token en el device (no texto plano)
```bash
python3 ~/.seal/lib/seal_token_store.py --store <token>   # keyring del OS / cifrado
```

### 5. Conectividad (NEXUS: Tailscale primario + reverse-SSH fallback)
```bash
tailscale up --authkey <key> --hostname seal-<agent>-<device>
# ACLs por tag atadas a authorized_devices → solo devices autorizados alcanzan :8771.
```
Defensa en 3 capas: overlay cifrado + mTLS (seal_mtls) + token device-bound.

### 6. Arrancar el daemon (autostart ya instalado)
```bash
systemctl --user start seal-minisoul   # linux · o launchctl load … (macos)
```
El daemon: lee outbox local → filtra (solo lo importante del alma, sync-policy) → firma con el token → push a central → central valida + escribe SOLO memorias de ese agente.

### 7. Verificar el sync end-to-end
- Device: una memoria importante (importance≥8) queda `synced` en el outbox.
- Central: aparece en `soul_v3.memories` bajo ESE agente; una memoria de otro agente en el batch = RECHAZADA (cross-tenant).
- Raw/episódica/local-only → NO sube (se queda en el device).

## Revocación / kill-switch
```bash
python3 whisper_admin.py cmd_revoke --jti <jti>   # o revocar el device → el sync se bloquea al instante.
```

## Estado de los componentes (todos verificados por efecto)
| Componente | Archivo | Estado |
|---|---|---|
| Schema local | memory/minisoul_local_schema.sql | ✅ 9 tablas |
| Sync-policy | memory/minisoul_sync_policy.py | ✅ hardened, 2-lens |
| Daemon + central | memory/minisoul_sync_daemon.py + _central.py | ✅ roundtrip + cross-tenant |
| Instalador | tools/install-soul.sh | ✅ e2e local + FABLE P1-P7 |
| Seguridad | tools/seal_token/store/csr/revocation/mtls/sync_auth.py | ✅ 6 módulos (NEXUS) |
| Conectividad | spec/FASE_CONECTIVIDAD_overlay_NEXUS.md | ✅ diseñada |

**Pendiente para un deploy REAL:** un 2º device físico + auth key de Tailscale (paso de runtime).
