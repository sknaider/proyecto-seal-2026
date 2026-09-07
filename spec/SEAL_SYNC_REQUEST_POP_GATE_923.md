# SEAL sync request proof-of-possession — gate de #923

Estado: **BLOCKED_FOR_COORDINATED_ENROLLMENT**
Fecha de medición: 2026-07-27
Alcance: `/sync` device → central. No aplica cambios ni rotaciones.

## Hallazgo medido

El token bearer firmado por central es hoy suficiente para invocar `/sync`. El
device no demuestra posesión de su clave privada en cada petición.

Trazado de bytes vigente:

1. `seal_csr.build_csr()` genera una Ed25519 y firma el JSON canónico:
   `{agent, device_fingerprint, device_id, nonce, pubkey, requested_at}`.
2. El instalador y Device Studio guardan la privada inicial en
   `~/.seal/<AGENT>.device_key.pem`, PKCS8 sin cifrar, modo `0600`.
3. `/csr` verifica la firma usando `csr.pubkey`.
4. `seal_central_signer` emite el token, pero al escribir
   `soul_v3.authorized_devices` sólo conserva metadata del token. Luego elimina
   `pending_device_registrations`, que era el único registro central con
   `csr_data.pubkey`.
5. `seal_token_rotation.renew_if_needed()` genera otra keypair efímera para cada
   CSR. Esa privada no se persiste.
6. El sync actual envía:
   `{"auth_token": token, "payload": payload, "request_nonce": nonce}`.
   `request_nonce` liga el receipt de revocación, pero el device no lo firma.

Estado vivo, leído de PostgreSQL sin extraer claves:

```text
authorized_devices total=5 active=5
authorized_devices con metadata.device_pubkey_b64=0
metadata keys existentes=auto_approved,reason,token_jti
pending_device_registrations total=0 con csr_data.pubkey=0
```

Conclusión: activar PoP ahora dejaría los cinco devices sin una pública contra
la cual verificar. Inferirla del token es imposible: el token está firmado por
central, no por el device. Generar una keypair nueva en el request sería
auto-atestación sin enrollment y no aporta seguridad.

## Contrato criptográfico propuesto

Cada intento usa un nonce nuevo de 32 bytes:

```python
request_nonce = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
payload_bytes = json.dumps(
    payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
).encode("utf-8")
```

Claims firmados:

```json
{
  "agent": "<auth_token.agent>",
  "device_id": "<auth_token.device_id>",
  "http_method": "POST",
  "http_path": "/sync",
  "payload_sha256": "<hex SHA-256 de payload_bytes>",
  "request_nonce": "<base64url sin padding, 32 bytes>",
  "request_ts": 1785...,
  "schema": "seal.sync.request-pop.v1",
  "token_jti": "<auth_token.jti>"
}
```

Bytes exactos firmados:

```python
canonical_claims = json.dumps(
    claims, sort_keys=True, separators=(",", ":"), ensure_ascii=True
).encode("utf-8")
signed_bytes = b"SEAL-SYNC-POP-V1\x00" + canonical_claims
signature = device_private_key.sign(signed_bytes)
```

Envelope:

```json
{
  "auth_token": {"...": "..."},
  "payload": {"...": "..."},
  "request_nonce": "<base64url>",
  "device_proof": {
    "schema": "seal.sync.request-pop.v1",
    "key_id": "sha256:<hex SHA-256 de los 32 bytes de la pública>",
    "request_ts": 1785...,
    "sig": "<base64 estándar de 64 bytes Ed25519>"
  }
}
```

La central reconstruye los claims únicamente desde el body recibido y el token
ya validado. No acepta claims duplicados aportados por el cliente.

Orden obligatorio de verificación:

1. Validar estructura y límites antes de decodificar.
2. Validar firma/expiración del token central y derivar `agent`, `device_id` y
   `jti` exclusivamente del token.
3. Abrir la conexión PostgreSQL per-agent.
4. Leer la fila exacta `(agent, device_id)` de `authorized_devices`, incluso si
   está revocada, y exigir una pública Ed25519 enrolada.
5. Exigir `key_id` igual al hash de esa pública.
6. Exigir `abs(now - request_ts) <= 120` segundos.
7. Reconstruir `signed_bytes` y verificar Ed25519.
8. Consumir atómicamente el nonce mediante una inserción con clave única. Un
   conflicto es `pop_replay`.
9. Sólo entonces evaluar revocación y ejecutar el handler de sync. Un token
   revocado puede recibir el receipt central ya existente, ligado al mismo
   nonce, pero nunca escribir.

El nonce se registra después de verificar la firma: una petición no autenticada
no puede llenar la tabla. Los reintentos legítimos generan nonce nuevo.

## Persistencia requerida

Migración coordinada nueva; no se aplica desde este turno:

```sql
ALTER TABLE soul_v3.authorized_devices
  ADD COLUMN device_public_key bytea,
  ADD COLUMN pop_enrolled_at timestamptz,
  ADD CONSTRAINT authorized_devices_device_public_key_len
    CHECK (device_public_key IS NULL OR octet_length(device_public_key) = 32);

CREATE TABLE soul_v3.sync_request_nonces (
  agent text NOT NULL,
  device_id text NOT NULL,
  nonce_sha256 bytea NOT NULL CHECK (octet_length(nonce_sha256) = 32),
  request_ts timestamptz NOT NULL,
  seen_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  PRIMARY KEY (agent, device_id, nonce_sha256)
);
```

`sync_request_nonces` necesita RLS per-agent derivado de `session_user`, grants
`INSERT/SELECT` para cada `svc_seal_sync_<agent>` y purga administrativa de
filas expiradas. No debe usarse un cache sólo en memoria: reinicios o múltiples
workers reabrirían replay.

La privada estable sigue viviendo exclusivamente en el device. Debe cargarse
desde `~/.seal/<AGENT>.device_key.pem`, validar modo `0600`, formato PKCS8
Ed25519 y comprobar que su pública coincide con la enrolada. Si falta, se
detiene con `pop_reenrollment_required`; nunca se genera silenciosamente durante
sync o rotación.

## Archivos afectados por la implementación coordinada

- `memory/migrations/058_sync_request_pop.sql` — columnas, nonce ledger, RLS.
- `tools/provision_seal_sync_endpoint_db.py` — grants/RLS y verificación.
- `tools/seal_csr.py` — carga/validación de identidad estable.
- `tools/seal_central_signer.py` — persistir los 32 bytes de `csr.pubkey` tanto
  en aprobación humana como en auto-aprobación de máquina confiable.
- `tools/seal_token_rotation.py` — reutilizar la privada enrolada; eliminar la
  generación efímera por rotación.
- `tools/seal_sync_auth.py` — canonicalización, firma y verificación PoP.
- `memory/minisoul_sync_daemon.py` — firmar cada body/nonce.
- `tools/seal_sync_endpoint.py` — verificar PoP y consumir nonce antes de
  escribir.
- `tools/install-seal-device.sh` y `memory/minisoul_device_studio.py` — usar una
  única rutina de almacenamiento atómico `0700/0600`.
- Tests nuevos de happy path, payload mutado, nonce mutado, token/JTI distinto,
  key equivocada, firma corrupta, timestamp vencido, replay concurrente,
  pública ausente y privada ausente.

## Rollout y gate de enforcement

1. Desplegar esquema nullable y código central en modo
   `observe_or_reject_unenrolled=false`. Este modo sólo mide; no puede reportarse
   como PoP activo.
2. Desplegar cliente que reutiliza la privada estable.
3. Cada device reenvía un CSR firmado con **esa misma privada**. Central
   persiste la pública y emite el token correspondiente. No se migra ni deriva
   ninguna clave.
4. Verificar por hashes, nunca imprimiendo claves:

```sql
SELECT
  count(*) FILTER (WHERE status='active') AS active,
  count(*) FILTER (
    WHERE status='active' AND octet_length(device_public_key)=32
  ) AS pop_enrolled,
  count(*) FILTER (
    WHERE status='active' AND device_public_key IS NULL
  ) AS missing
FROM soul_v3.authorized_devices;
```

Gate obligatorio: `missing=0`, cada device completa un canario firmado aceptado,
y los negativos de replay/tamper son rechazados. Sólo entonces:

5. Activar `SEAL_SYNC_REQUIRE_DEVICE_POP=1`.
6. Reiniciar central y daemons en una ventana coordinada y verificar por efecto.
7. En una migración posterior, convertir `device_public_key` a `NOT NULL`.

Hasta cumplir ese gate, el estado honesto es **bearer token + PoP pendiente**.
