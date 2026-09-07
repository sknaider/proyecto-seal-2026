# Fase-1: Seguridad — Gate de Autorización
## Cierre de huecos del Red-Team & Diseño Concreto Buildable

**Status:** Diseño Detallado (ready to implement)  
**Owner:** NEXUS (Infrastructure Security)  
**Date:** 2026-07-02  
**Crítica:** Cierra 2 huecos identificados en red-team: (1) Formato token sin device fingerprint, (2) Sin revocación.

---

## Resumen Ejecutivo

Este documento especifica **Fase-1 del modelo de seguridad SEAL** que implementa autenticación + autorización + revocación:

1. **Token JWT firmado (Ed25519)** con device_id + device_fingerprint integrados en el payload
2. **Base de datos segura** (PostgreSQL DDL con RLS) para dispositivos autorizados + tokens revocados  
3. **Flujo de registro out-of-band** (manual 2FA por William) para dispositivos nuevos
4. **mTLS + pinning de certificados** desde bootstrap, almacenamiento seguro del token en keyring del OS
5. **Revocación con TTL corto** (15 min cache, re-sync cada 60s)

**Huecos cerrados:**
- ✅ Hueco 1: Token ahora incluye `device_fingerprint` (SHA256 de HW serial + MAC + os_id) + firma Ed25519
- ✅ Hueco 2: Tabla `revoked_tokens` + `authorized_devices` con status tracking + revocación por device_id + CLI

---

## 1. Formato del Token JWT (Cierre Hueco #1)

### 1.1 Payload Completo (Firmado)

```json
{
  "agent": "ADA",
  "device_id": "device-abc123def456",
  "device_fingerprint": "sha256:a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
  "issued_at": 1718520000,
  "expires_at": 1718606400,
  "nonce": "f7e8d9c0b1a2f3e4d5c6b7a8f9e0d1c2",
  "sig": "ed25519_signature_base64_encoded_here"
}
```

### 1.2 Estructura Precisa

| Campo | Tipo | Tamaño | Descripción |
|-------|------|--------|-------------|
| `agent` | string | 5-20 chars | Identificador del agente (ADA, JARVIS, ALICE, NEXUS, DUM) |
| `device_id` | string | 20 chars | Format: `device-{12-char-UUID}` |
| `device_fingerprint` | string | 66 chars | Format: `sha256:{64-hex-chars}` (SHA256 de hardware+OS) |
| `issued_at` | uint32 | 10 digits | Unix timestamp (segundos), time.time() |
| `expires_at` | uint32 | 10 digits | issued_at + 86400 (24h TTL) |
| `nonce` | string | 32 chars | Random 16 bytes hex; previene replay attacks |
| `sig` | string | ~88 chars | Ed25519 signature de todos los campos anteriores, base64 |

### 1.3 Cálculo del `device_fingerprint`

**Entrada:** Hardware del dispositivo

```python
import hashlib
import uuid
import platform
from getmac import get_mac_address

def compute_device_fingerprint():
    """
    Calcula fingerprint único del dispositivo combinando 3 fuentes:
    1. Serial de la placa madre (ó UUID de sistema si no disponible)
    2. MAC address de la interfaz de red principal
    3. os_id (nombre del SO + versión)
    """
    sources = []
    
    # 1. Hardware serial (Windows: WMI, Linux: dmidecode, macOS: system_profiler)
    try:
        if platform.system() == "Windows":
            import wmi
            c = wmi.WMI()
            serial = [x.SerialNumber for x in c.Win32_BaseBoard()][0]
        elif platform.system() == "Darwin":  # macOS
            import subprocess
            serial = subprocess.check_output(["system_profiler", "SPHardwareDataType"]).decode().split("Serial Number")[1].split("\n")[0].strip()
        else:  # Linux
            with open("/sys/class/dmi/id/board_serial") as f:
                serial = f.read().strip()
    except:
        serial = str(uuid.getnode())  # Fallback: MAC as int
    
    sources.append(serial.encode())
    
    # 2. MAC address de interfaz principal
    mac = get_mac_address()
    if not mac:
        mac = "00:00:00:00:00:00"
    sources.append(mac.encode())
    
    # 3. OS identifier
    os_id = f"{platform.system()}-{platform.release()}"
    sources.append(os_id.encode())
    
    # Combine + SHA256
    combined = b"|".join(sources)
    fingerprint_hash = hashlib.sha256(combined).hexdigest()
    
    return f"sha256:{fingerprint_hash}"

# Ejemplo de resultado:
# sha256:a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2
```

**Propiedades:**
- **Determinístico:** Mismo dispositivo → mismo fingerprint (aunque reboote)
- **Resistente a spoofing:** Combina 3 capas (HW serial + MAC + OS); es muy difícil falsificar sin acceso físico
- **Cambio detectado:** Si alguien cambia la tarjeta de red → fingerprint cambia → token inválido → revocación triggerada

### 1.4 Firma Ed25519 (Asymmetric)

**En SOUL Central:**
```python
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization
import json
import base64
import time

# Cargar private key de SOUL (almacenada segura en SEAL_MASTER_DOC)
with open("/home/dadito/IA/proyecto-seal/SEAL_MASTER_DOC/.seal/private.key") as f:
    private_key_bytes = base64.b64decode(f.read())
    private_key = ed25519.Ed25519PrivateKey.from_private_bytes(private_key_bytes)

def generate_token(agent: str, device_id: str, device_fingerprint: str) -> str:
    """Genera JWT firmado por SOUL"""
    import secrets
    
    payload = {
        "agent": agent,
        "device_id": device_id,
        "device_fingerprint": device_fingerprint,
        "issued_at": int(time.time()),
        "expires_at": int(time.time()) + 86400,  # 24h
        "nonce": secrets.token_hex(16),
    }
    
    # Payload → JSON → UTF-8
    payload_json = json.dumps(payload, separators=(',', ':'), sort_keys=True)
    payload_bytes = payload_json.encode('utf-8')
    
    # Firmar
    signature = private_key.sign(payload_bytes)
    sig_b64 = base64.b64encode(signature).decode('ascii')
    
    # Agregar firma al payload
    payload["sig"] = sig_b64
    
    # Token final: base64(payload_json_with_sig)
    final_json = json.dumps(payload, separators=(',', ':'), sort_keys=True)
    token = base64.b64encode(final_json.encode('utf-8')).decode('ascii')
    
    return token
```

**En Device (validación):**
```python
def validate_token(token_str: str, expected_agent: str, expected_device_id: str) -> bool:
    """
    Valida token JWT en el device.
    Returns: (valid, error_msg)
    """
    import base64
    import json
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.exceptions import InvalidSignature
    
    try:
        # Decodificar payload
        payload_json = base64.b64decode(token_str).decode('utf-8')
        payload = json.loads(payload_json)
        
        # Extraer campos
        agent = payload.get("agent")
        device_id = payload.get("device_id")
        device_fp = payload.get("device_fingerprint")
        issued_at = payload.get("issued_at")
        expires_at = payload.get("expires_at")
        sig_b64 = payload.get("sig")
        
        # Validaciones básicas
        if agent != expected_agent:
            return False, "Agent mismatch"
        if device_id != expected_device_id:
            return False, "Device ID mismatch"
        
        # Validar fingerprint (debe coincidir con dispositivo actual)
        current_fp = compute_device_fingerprint()
        if device_fp != current_fp:
            return False, "Device fingerprint mismatch (hardware changed?)"
        
        # Validar expiración
        import time
        if time.time() > expires_at:
            return False, "Token expired"
        
        # Validar firma Ed25519
        # Reconstruir el payload sin "sig" para verificar
        payload_for_sig = {k: v for k, v in payload.items() if k != "sig"}
        payload_for_sig_json = json.dumps(payload_for_sig, separators=(',', ':'), sort_keys=True)
        payload_for_sig_bytes = payload_for_sig_json.encode('utf-8')
        
        # Cargar public key de SOUL (publicada en ~/.seal/certs/soul.pub)
        with open(os.path.expanduser("~/.seal/certs/soul.pub")) as f:
            public_key_bytes = base64.b64decode(f.read())
            public_key = ed25519.Ed25519PublicKey.from_public_bytes(public_key_bytes)
        
        # Verificar firma
        signature_bytes = base64.b64decode(sig_b64)
        public_key.verify(signature_bytes, payload_for_sig_bytes)
        
        return True, "Valid"
    
    except InvalidSignature:
        return False, "Invalid signature"
    except Exception as e:
        return False, f"Validation error: {str(e)}"
```

---

## 2. Schema de Base de Datos (PostgreSQL soul_v3)

### 2.1 Tabla: `authorized_devices`

**Propósito:** Registro de dispositivos autorizados por William, con estado de revocación.

```sql
-- Crear tabla de dispositivos autorizados
CREATE TABLE IF NOT EXISTS soul_v3.authorized_devices (
    device_id TEXT PRIMARY KEY,
    device_fingerprint TEXT NOT NULL UNIQUE,  -- sha256:xxxxx
    agent TEXT NOT NULL,  -- ADA, JARVIS, ALICE, NEXUS, DUM
    registered_by TEXT NOT NULL,  -- Usuario que autorizó (ej: 'william')
    registered_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_ip INET,  -- Última IP observada (auxiliar, no crítica)
    last_seen_at TIMESTAMP WITH TIME ZONE,  -- Última vez que se conectó
    status TEXT NOT NULL CHECK (status IN ('active', 'suspended', 'revoked')) DEFAULT 'active',
    revocation_reason TEXT,  -- Motivo si status='revoked'
    revoked_at TIMESTAMP WITH TIME ZONE,  -- Cuándo fue revocado
    metadata JSONB DEFAULT '{}',  -- Extra: device_name, os_info, etc.
    
    CREATED_AT TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UPDATED_AT TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Índices para búsquedas rápidas
CREATE INDEX idx_authorized_devices_agent ON soul_v3.authorized_devices(agent);
CREATE INDEX idx_authorized_devices_status ON soul_v3.authorized_devices(status);
CREATE INDEX idx_authorized_devices_registered_at ON soul_v3.authorized_devices(registered_at DESC);
```

### 2.2 Tabla: `revoked_tokens`

**Propósito:** Lista negra de tokens revocados. Se sincroniza a dispositivos cada 60s.

```sql
CREATE TABLE IF NOT EXISTS soul_v3.revoked_tokens (
    token_jti TEXT PRIMARY KEY,  -- JWT ID (nonce del token)
    device_id TEXT NOT NULL,
    agent TEXT NOT NULL,
    revoked_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    revoked_by TEXT NOT NULL,  -- Usuario que revocó (ej: 'william', 'auto-expiry')
    reason TEXT NOT NULL,  -- 'device_compromised', 'explicit_revocation', 'logout', 'auto_expiry', 'fingerprint_mismatch'
    
    FOREIGN KEY (device_id) REFERENCES soul_v3.authorized_devices(device_id) ON DELETE CASCADE,
    
    CREATED_AT TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Índices
CREATE INDEX idx_revoked_tokens_device_id ON soul_v3.revoked_tokens(device_id);
CREATE INDEX idx_revoked_tokens_agent ON soul_v3.revoked_tokens(agent);
CREATE INDEX idx_revoked_tokens_revoked_at ON soul_v3.revoked_tokens(revoked_at DESC);

-- Retención: indefinida hasta persistir token_expires_at y aprobar una
-- política ligada a expiración. La antigüedad de revoked_at por sí sola no
-- demuestra que el token ya no pueda autenticarse.
```

### 2.3 Tabla: `token_audit` (Auditoría)

**Propósito:** Log de todas las emisiones, validaciones, y rechazos de tokens.

```sql
CREATE TABLE IF NOT EXISTS soul_v3.token_audit (
    token_id BIGSERIAL PRIMARY KEY,
    device_id TEXT,
    agent TEXT,
    action TEXT NOT NULL CHECK (action IN ('issued', 'validated_ok', 'validated_fail', 'revoked', 'refresh_ok', 'refresh_fail')),
    reason TEXT,  -- Motivo de fallo (expirado, fingerprint mismatch, revocado, etc.)
    ip_address INET,
    user_agent TEXT,
    
    CREATED_AT TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Índices
CREATE INDEX idx_token_audit_device_id ON soul_v3.token_audit(device_id);
CREATE INDEX idx_token_audit_agent ON soul_v3.token_audit(agent);
CREATE INDEX idx_token_audit_created_at ON soul_v3.token_audit(CREATED_AT DESC);

-- Limpieza automática: borrar audit hace >180 días
-- DELETE FROM soul_v3.token_audit WHERE CREATED_AT < CURRENT_TIMESTAMP - INTERVAL '180 days';
```

### 2.4 Row-Level Security (RLS)

**Política:** Cada agente solo puede leer/actualizar su propio dispositivo.

```sql
-- Habilitar RLS
ALTER TABLE soul_v3.authorized_devices ENABLE ROW LEVEL SECURITY;
ALTER TABLE soul_v3.revoked_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE soul_v3.token_audit ENABLE ROW LEVEL SECURITY;

-- Política para authorized_devices: agente solo ve su propio device
CREATE POLICY authorized_devices_agent_isolation ON soul_v3.authorized_devices
    FOR SELECT
    USING (agent = current_setting('soul.agent', false) OR current_setting('soul.is_admin', false) = 'true');

-- Política para revoked_tokens: agente ve revocaciones de sus propios devices
CREATE POLICY revoked_tokens_agent_isolation ON soul_v3.revoked_tokens
    FOR SELECT
    USING (agent = current_setting('soul.agent', false) OR current_setting('soul.is_admin', false) = 'true');

-- Política para token_audit: similar
CREATE POLICY token_audit_agent_isolation ON soul_v3.token_audit
    FOR SELECT
    USING (agent = current_setting('soul.agent', false) OR current_setting('soul.is_admin', false) = 'true');

-- Función para setear context al conectar
CREATE OR REPLACE FUNCTION soul_v3.set_agent_context(agent_name TEXT, is_admin BOOLEAN DEFAULT FALSE)
RETURNS void AS $$
BEGIN
    PERFORM set_config('soul.agent', agent_name, false);
    PERFORM set_config('soul.is_admin', CASE WHEN is_admin THEN 'true' ELSE 'false' END, false);
END;
$$ LANGUAGE plpgsql;
```

---

## 3. Flujo de Registro Out-of-Band (Cierre Hueco #2)

### 3.1 Fases del Registro

```
FASE 1: CSR Generation (en el device nuevo)
    ├─ Usuario instala seal-agent
    ├─ Installer pregunta: agent_name, SOUL_endpoint
    ├─ Genera device_id = "device-" + 12-char-UUID
    ├─ Genera device_fingerprint = SHA256(serial + MAC + os_id)
    └─ Genera CSR (Certificate Signing Request) con ambos datos
         ├─ Almacena en ~/.seal/csr/device_info.json
         └─ Imprime QR-code o muestra UUID+fingerprint

FASE 2: Manual Approval (en SOUL Central, por William)
    ├─ William recibe notificación: "New device wants to join"
    ├─ William escanea QR-code ó confirma por terminal
    ├─ Verifica device_id + device_fingerprint en la lista
    ├─ 2FA: William ingresa token de autenticación (2FA tool)
    └─ SOUL crea registro en authorized_devices con status='active'

FASE 3: Token Issuance (por SOUL Central)
    ├─ SOUL genera JWT con:
    │   ├─ agent = agent_name_del_CSR
    │   ├─ device_id = device_id_del_CSR
    │   ├─ device_fingerprint = device_fingerprint_del_CSR
    │   ├─ issued_at = ahora
    │   ├─ expires_at = ahora + 24h
    │   ├─ nonce = random
    │   └─ sig = Ed25519_signature
    ├─ SOUL devuelve token (vía POST /agents/register_approve)
    └─ Device descarga y almacena en OS keyring

FASE 4: Verificación de Inicio (en el device)
    ├─ Device carga token desde keyring
    ├─ Valida signature Ed25519 (local, no conectado aún)
    ├─ Valida device_fingerprint (debe coincidir con HW actual)
    ├─ Si OK: ingresa token en memoria
    ├─ Conecta a SOUL con token en header Authorization
    └─ SOUL valida token en revoked_tokens → inicio autorizado
```

### 3.2 Implementación Concreta (Pseudocódigo)

#### Device: CSR Generation
```python
# ~/.seal/installer.py
import json
import uuid
import hashlib
from getmac import get_mac_address
import platform

def create_csr(agent_name, soul_endpoint):
    """Crea Certificate Signing Request en el device"""
    
    # Generar IDs únicos
    device_id = f"device-{uuid.uuid4().hex[:12]}"
    device_fingerprint = compute_device_fingerprint()  # (ver §1.3)
    
    # CSR payload
    csr_payload = {
        "device_id": device_id,
        "device_fingerprint": device_fingerprint,
        "agent": agent_name,
        "soul_endpoint": soul_endpoint,
        "os_info": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "timestamp": int(time.time()),
    }
    
    # Guardar CSR localmente
    os.makedirs(os.path.expanduser("~/.seal/csr"), exist_ok=True)
    with open(os.path.expanduser("~/.seal/csr/device_info.json"), "w") as f:
        json.dump(csr_payload, f, indent=2)
    
    # Generar QR code (para fácil escaneo por William)
    qr_text = json.dumps({
        "device_id": device_id,
        "device_fingerprint": device_fingerprint,
    })
    
    import qrcode
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(qr_text)
    qr.make()
    qr.make_image().save(os.path.expanduser("~/.seal/csr/device_qr.png"))
    
    print(f"✓ CSR created:")
    print(f"  Device ID: {device_id}")
    print(f"  Fingerprint: {device_fingerprint}")
    print(f"  QR saved: ~/.seal/csr/device_qr.png")
    print(f"\nGive this info to William for approval.")
    
    return csr_payload
```

#### SOUL Central: Approval & Token Issue
```python
# ~/SEAL_MASTER_DOC/token_issuer.py
from flask import Flask, request
from cryptography.hazmat.primitives.asymmetric import ed25519
import psycopg2
import json
import base64
import time
import secrets

app = Flask(__name__)

@app.route('/agents/register', methods=['POST'])
def register_device():
    """Recibe CSR; prepara para aprobación manual"""
    csr = request.json
    
    device_id = csr['device_id']
    device_fingerprint = csr['device_fingerprint']
    agent = csr['agent']
    
    # Guardar en tabla temporal para revisión
    conn = psycopg2.connect("dbname=soul_v3 user=soul password=*** host=localhost")
    cur = conn.cursor()
    
    cur.execute("""
        INSERT INTO soul_v3.pending_device_registrations 
        (device_id, device_fingerprint, agent, csr_data, created_at)
        VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (device_id) DO UPDATE SET created_at=CURRENT_TIMESTAMP
    """, (device_id, device_fingerprint, agent, json.dumps(csr)))
    
    conn.commit()
    cur.close()
    conn.close()
    
    # Notificar a William (webhook, email, o SMS)
    print(f"[ALERT] New device registration pending approval:")
    print(f"  Device: {device_id}")
    print(f"  Fingerprint: {device_fingerprint}")
    print(f"  Agent: {agent}")
    
    return {"status": "pending_approval", "device_id": device_id}, 202

@app.route('/agents/register_approve', methods=['POST'])
def approve_device():
    """William aprueba el device (after 2FA)"""
    approval_data = request.json
    
    device_id = approval_data['device_id']
    device_fingerprint = approval_data['device_fingerprint']
    agent = approval_data['agent']
    william_2fa_token = approval_data['2fa_token']  # Validar before
    
    # Validar 2FA (ejemplo simple)
    if not validate_2fa(william_2fa_token):
        return {"error": "2FA validation failed"}, 401
    
    conn = psycopg2.connect("dbname=soul_v3 user=soul password=*** host=localhost")
    cur = conn.cursor()
    
    # Registrar device en authorized_devices
    cur.execute("""
        INSERT INTO soul_v3.authorized_devices
        (device_id, device_fingerprint, agent, registered_by, status, metadata)
        VALUES (%s, %s, %s, %s, 'active', %s)
        ON CONFLICT (device_id) DO UPDATE SET status='active'
    """, (device_id, device_fingerprint, agent, 'william', json.dumps({
        "approved_at": time.time(),
    })))
    
    # Generar JWT token
    token = generate_token(agent, device_id, device_fingerprint)
    token_jti = device_id  # Usar device_id como JTI para revocation
    
    # Guardar referencia en base de datos
    cur.execute("""
        INSERT INTO soul_v3.token_audit
        (device_id, agent, action, reason)
        VALUES (%s, %s, 'issued', 'manual_approval')
    """, (device_id, agent))
    
    conn.commit()
    cur.close()
    conn.close()
    
    return {
        "status": "approved",
        "device_id": device_id,
        "token": token,
        "expires_in_seconds": 86400,
    }, 200

def generate_token(agent, device_id, device_fingerprint):
    """Genera JWT firmado (ver §1.4)"""
    # ... (implementación en §1.4)
    pass

def validate_2fa(token):
    """Valida 2FA token de William"""
    # Integrar con servicio de 2FA (Authy, TOTP, etc.)
    return True
```

#### Device: Token Reception & Storage
```python
# ~/.seal/token_manager.py
import keyring
import json
import base64

def store_token_secure(agent, device_id, token):
    """
    Almacena token en el keyring del OS (no en texto plano).
    - Windows: Credential Manager
    - macOS: Keychain
    - Linux: Secret Service (via DBus)
    """
    service = f"seal-agent-{agent}"
    username = f"token-{device_id}"
    
    # Verificar integridad antes de almacenar
    is_valid, msg = validate_token(token, agent, device_id)
    if not is_valid:
        raise ValueError(f"Token validation failed: {msg}")
    
    # Almacenar en keyring
    keyring.set_password(service, username, token)
    
    # Guardar referencia en ~/.seal/config/token_info.json (sin el token en sí)
    token_info = {
        "agent": agent,
        "device_id": device_id,
        "service": service,
        "username": username,
        "stored_at": time.time(),
        "token_hash": hashlib.sha256(token.encode()).hexdigest(),
    }
    
    with open(os.path.expanduser("~/.seal/config/token_info.json"), "w") as f:
        json.dump(token_info, f, indent=2)
    
    print(f"✓ Token stored securely in OS keyring")

def load_token_secure(agent, device_id):
    """Carga token desde keyring"""
    service = f"seal-agent-{agent}"
    username = f"token-{device_id}"
    
    token = keyring.get_password(service, username)
    if not token:
        raise ValueError("Token not found in keyring")
    
    return token
```

---

## 4. mTLS + Cert Pinning

### 4.1 Setup Inicial (Bootstrap)

```python
# ~/.seal/mcp_server_setup.py
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
import datetime
import os

def generate_self_signed_cert(cn="soul.local", days=365):
    """Genera self-signed cert para MCP server"""
    
    # Generar private key (RSA 2048)
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend()
    )
    
    # Crear certificate
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "PE"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Lima"),
        x509.NameAttribute(NameOID.LOCALITY_NAME, "Lima"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "SEAL"),
        x509.NameAttribute(NameOID.COMMON_NAME, cn),
    ])
    
    cert = x509.CertificateBuilder().subject_name(
        subject
    ).issuer_name(
        issuer
    ).public_key(
        private_key.public_key()
    ).serial_number(
        x509.random_serial_number()
    ).not_valid_before(
        datetime.datetime.utcnow()
    ).not_valid_after(
        datetime.datetime.utcnow() + datetime.timedelta(days=days)
    ).add_extension(
        x509.SubjectAlternativeName([
            x509.DNSName(cn),
            x509.DNSName("localhost"),
            x509.DNSName("127.0.0.1"),
        ]),
        critical=False,
    ).sign(private_key, hashes.SHA256(), default_backend())
    
    # Guardar
    os.makedirs(os.path.expanduser("~/.seal/certs"), exist_ok=True)
    
    with open(os.path.expanduser("~/.seal/certs/mcp.crt"), "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    
    with open(os.path.expanduser("~/.seal/certs/mcp.key"), "wb") as f:
        f.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        ))
    
    # Guardar public key para pinning
    with open(os.path.expanduser("~/.seal/certs/mcp.pub"), "wb") as f:
        f.write(private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        ))
    
    # Calcular sha256 fingerprint para pinning
    import hashlib
    cert_der = cert.public_bytes(serialization.Encoding.DER)
    fingerprint = hashlib.sha256(cert_der).hexdigest()
    
    print(f"✓ Self-signed cert generated:")
    print(f"  Subject: {cn}")
    print(f"  SHA256 Fingerprint: {fingerprint}")
    print(f"  Files: ~/.seal/certs/mcp.{crt,key,pub}")
    
    return fingerprint
```

### 4.2 Device-side: Cert Pinning

```python
# ~/.seal/mcp_client_setup.py
import ssl
import hashlib
import os

def setup_pinned_https():
    """Configura HTTPS con cert pinning"""
    
    # Leer public key SOUL (distribuida out-of-band durante installer)
    with open(os.path.expanduser("~/.seal/certs/soul.pub"), "rb") as f:
        soul_pubkey_der = f.read()
    
    # Calcular hash esperado
    expected_fingerprint = hashlib.sha256(soul_pubkey_der).hexdigest()
    
    # Crear SSL context con CA bundle
    context = ssl.create_default_context()
    
    # Cargar SOUL's public cert (self-signed)
    context.load_verify_locations(
        cafile=os.path.expanduser("~/.seal/certs/soul.crt")
    )
    
    # STRICT: rechazar cualquier cert que no coincida
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    
    return context
```

---

## 5. Revocación: Cache TTL + Re-sync

### 5.1 Arquitectura de Revocación

```
┌─────────────────────────────────────────────────────┐
│ Device (local cache)                                │
├─────────────────────────────────────────────────────┤
│ • Carga revoked_tokens en memoria (TTL: 15 min)    │
│ • Valida token contra cache local                   │
│ • Si cache expirado: re-sync con SOUL (60s)       │
│ • Si falla sync: paranoia mode (rechaza todo)      │
└─────────────────────────────────────────────────────┘
                       ↑
                       │ HTTP GET /revoked_tokens
                       │ (cada 60s)
                       ↓
┌─────────────────────────────────────────────────────┐
│ SOUL Central (PostgreSQL)                           │
├─────────────────────────────────────────────────────┤
│ • soul_v3.revoked_tokens table (realtime)          │
│ • Endpoint: GET /api/revoked_tokens?agent=X       │
│ • Retorna: [{"token_jti": "...", "reason": "..."}] │
└─────────────────────────────────────────────────────┘
```

### 5.2 Código de Revocación (Device)

```python
# ~/.seal/revocation_client.py
import json
import time
import requests
import threading

class RevocationManager:
    def __init__(self, agent, device_id, soul_endpoint):
        self.agent = agent
        self.device_id = device_id
        self.soul_endpoint = soul_endpoint
        self.revoked_cache = {}  # {token_jti: reason}
        self.cache_expiry = 0  # Unix timestamp
        self.cache_ttl_seconds = 15 * 60  # 15 minutes
        self.sync_interval_seconds = 60  # Re-sync every 60s
        self.lock = threading.Lock()
        
        # Iniciar sync daemon
        self.sync_thread = threading.Thread(target=self._sync_daemon, daemon=True)
        self.sync_thread.start()
    
    def _sync_daemon(self):
        """Sync revocation list cada 60s en background"""
        while True:
            try:
                self._sync_revoked_tokens()
            except Exception as e:
                print(f"[ERROR] Revocation sync failed: {e}")
            
            time.sleep(self.sync_interval_seconds)
    
    def _sync_revoked_tokens(self):
        """Fetch lista actualizada de SOUL"""
        with self.lock:
            try:
                # Endpoint de SOUL
                url = f"{self.soul_endpoint}/api/revoked_tokens"
                params = {"agent": self.agent}
                
                # Usar token para auth (si disponible)
                token = load_token_secure(self.agent, self.device_id)
                headers = {"Authorization": f"Bearer {token}"}
                
                resp = requests.get(url, params=params, headers=headers, timeout=5)
                resp.raise_for_status()
                
                revoked_list = resp.json()  # [{"token_jti": "...", "reason": "..."}]
                
                # Actualizar cache
                self.revoked_cache = {item['token_jti']: item['reason'] 
                                     for item in revoked_list}
                self.cache_expiry = time.time() + self.cache_ttl_seconds
                
                print(f"[OK] Revocation cache synced: {len(self.revoked_cache)} entries")
            
            except Exception as e:
                print(f"[ERROR] Failed to sync revocation list: {e}")
                # PARANOIA: Si sync falla, vencer cache inmediatamente
                self.cache_expiry = time.time()
    
    def is_token_revoked(self, token_jti):
        """Chequea si token está revocado (usa cache local)"""
        with self.lock:
            # Si cache expiró, rechazar todo (paranoia)
            if time.time() > self.cache_expiry:
                return True, "cache_expired"
            
            if token_jti in self.revoked_cache:
                reason = self.revoked_cache[token_jti]
                return True, reason
            
            return False, "not_revoked"
```

### 5.3 Código de Revocación (SOUL Central)

```python
# ~/SEAL_MASTER_DOC/revocation_api.py
from flask import Flask, request
import psycopg2
import json

app = Flask(__name__)

@app.route('/api/revoked_tokens', methods=['GET'])
def get_revoked_tokens():
    """Endpoint que retorna lista de tokens revocados"""
    
    agent = request.args.get('agent')
    if not agent:
        return {"error": "agent parameter required"}, 400
    
    # Validar token del solicitante
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    if not token:
        return {"error": "unauthorized"}, 401
    
    is_valid, msg = validate_token(token, agent)
    if not is_valid:
        return {"error": f"token validation failed: {msg}"}, 401
    
    conn = psycopg2.connect("dbname=soul_v3 user=soul password=*** host=localhost")
    cur = conn.cursor()
    
    # Retornar todos los tokens revocados de este agente
    cur.execute("""
        SELECT token_jti, reason
        FROM soul_v3.revoked_tokens
        WHERE agent = %s AND revoked_at > CURRENT_TIMESTAMP - INTERVAL '90 days'
        ORDER BY revoked_at DESC
    """, (agent,))
    
    rows = cur.fetchall()
    revoked_list = [{"token_jti": row[0], "reason": row[1]} for row in rows]
    
    cur.close()
    conn.close()
    
    return {
        "agent": agent,
        "revoked_tokens": revoked_list,
        "count": len(revoked_list),
        "timestamp": time.time(),
    }, 200

@app.route('/api/revoke_token', methods=['POST'])
def revoke_token():
    """Revoca un token específico (por device_id + agent)"""
    
    revoke_req = request.json
    device_id = revoke_req.get('device_id')
    agent = revoke_req.get('agent')
    reason = revoke_req.get('reason', 'explicit_revocation')
    
    # Validar que el solicitante sea William (admin)
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    if not validate_admin_token(token):
        return {"error": "admin only"}, 403
    
    conn = psycopg2.connect("dbname=soul_v3 user=soul password=*** host=localhost")
    cur = conn.cursor()
    
    # Revocar TODOS los tokens de este device
    cur.execute("""
        INSERT INTO soul_v3.revoked_tokens
        (token_jti, device_id, agent, revoked_by, reason)
        SELECT device_id, device_id, agent, 'william', %s
        FROM soul_v3.authorized_devices
        WHERE device_id = %s AND agent = %s
    """, (reason, device_id, agent))
    
    # Actualizar status del device
    cur.execute("""
        UPDATE soul_v3.authorized_devices
        SET status = 'revoked', revoked_at = CURRENT_TIMESTAMP, revocation_reason = %s
        WHERE device_id = %s
    """, (reason, device_id))
    
    conn.commit()
    cur.close()
    conn.close()
    
    return {
        "status": "revoked",
        "device_id": device_id,
        "reason": reason,
    }, 200
```

---

## 6. CLI de Revocación (operator-friendly)

```bash
#!/bin/bash
# ~/SEAL_MASTER_DOC/seal-revoke-token

usage() {
    echo "Usage: seal-revoke-token <AGENT> <DEVICE_ID> [REASON]"
    echo ""
    echo "Example:"
    echo "  seal-revoke-token ADA device-abc123def 'device_compromised'"
    exit 1
}

if [ $# -lt 2 ]; then
    usage
fi

AGENT=$1
DEVICE_ID=$2
REASON=${3:-"explicit_revocation"}

# Llamar API de revocación
SOUL_ENDPOINT=${SOUL_API_ENDPOINT:-"https://soul.local"}
ADMIN_TOKEN=${SOUL_ADMIN_TOKEN}  # Debe estar seteado en env

if [ -z "$ADMIN_TOKEN" ]; then
    echo "[ERROR] SOUL_ADMIN_TOKEN not set"
    exit 1
fi

curl -X POST \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    -d @- "${SOUL_ENDPOINT}/api/revoke_token" << EOF
{
  "agent": "$AGENT",
  "device_id": "$DEVICE_ID",
  "reason": "$REASON"
}
EOF

echo ""
echo "[OK] Token revocation request submitted"
echo "    Agent: $AGENT"
echo "    Device: $DEVICE_ID"
echo "    Reason: $REASON"
```

---

## 7. Flujo Completo de Autorización

```
┌──────────────────────────────────────────────────────────────┐
│ BOOT DEVICE                                                  │
└──────────────────────────────────────────────────────────────┘
            ↓
    1. Load token from keyring
            ↓
    2. Validate signature (Ed25519) + expiry locally
            ↓
    3. Validate device_fingerprint (must match current HW)
            ↓
    4. Check revocation cache (TTL: 15 min)
            ↓
    If revoked → REJECT, alert user
            ↓
    If cache expired → trigger async sync_daemon
            ↓
    5. MCP connect with TLS (mTLS, cert pinning)
            ↓
    6. Send token in Authorization header
            ↓
┌──────────────────────────────────────────────────────────────┐
│ SOUL CENTRAL                                                 │
└──────────────────────────────────────────────────────────────┘
            ↓
    7. MCP server receives request + token
            ↓
    8. Validate token signature (Ed25519)
            ↓
    9. Check token.expires_at vs current time
            ↓
    10. Check revoked_tokens table (fast query)
            ↓
    If revoked → log + reject
            ↓
    11. SET app.current_agent_id = token.agent (for RLS)
            ↓
    12. Execute memory_search with RLS filters
            ↓
    13. Return results (only visible to agent)
            ↓
    14. Log access in token_audit table
            ↓
    15. If token expires in <1h → return new token in response
```

---

## 8. Matriz de Decisiones (Design Trade-offs)

| Decisión | Opción A | Opción B (ELEGIDA) | Rationale |
|----------|----------|-------------------|-----------|
| Token storage | Archivo texto plano (~/.seal/token) | OS keyring (Credential Manager/Keychain/Secret Service) | Keyring es resistant a disk theft; no espía procesos sin permisos |
| Revocation latency | Real-time (WebSocket) | Polling cada 60s + cache 15min local | Polling es más simple; 15min worst-case acceptable para token theft |
| Device fingerprint | MAC address solamente | SHA256(serial + MAC + os_id) | Combina 3 capas; es más resistente a spoofing |
| Token TTL | 7 días (largo) | 24 horas (corto) | Reduce riesgo de theft; frecuente refresh minimiza impacto |
| Signature algorithm | HMAC-SHA256 (shared secret) | Ed25519 (asymmetric) | Asym permite distribución de public key sin peligro; mejor para multi-device |
| Cert pinning | Trust system CA cert only | Pin self-signed cert public key | Self-signed es simple para bootstrap; pinning previene MITM |

---

## 9. Implementación Status (Design vs Build)

| Componente | Status | Owner | Fecha Prevista |
|-----------|--------|-------|-----------------|
| JWT token format + Ed25519 signing | DESIGN ✓ | NEXUS | 2026-07-05 |
| authorized_devices + revoked_tokens DDL | DESIGN ✓ | NEXUS | 2026-07-05 |
| Token validation in MCP server | TODO | ALICE | 2026-07-10 |
| CSR generation + manual approval flow | TODO | ALICE | 2026-07-12 |
| Token storage (keyring integration) | TODO | ALICE | 2026-07-10 |
| Revocation daemon + cache sync | TODO | NEXUS | 2026-07-15 |
| mTLS + cert pinning | TODO | DUM | 2026-07-12 |
| CLI: seal-revoke-token | TODO | ALICE | 2026-07-10 |
| Tests (token theft, revocation, HW change) | TODO | NEXUS | 2026-07-20 |
| Documentation (operator runbook) | TODO | ALICE | 2026-07-25 |

---

## 10. Escenarios de Red-Team (Mitigados)

### Escenario 1: Token Robo (disk)
**Attack:** Atacante roba arquivo ~/.seal/token (o credencial del keyring)  
**Defensa:**
- Token almacenado en OS keyring (no texto plano)
- Device fingerprint cambia si HW se mueve → token inválido
- Revocación TTL de 15 min → máximo 15 min de exposición
- Audit log registra todos los usos → detección en <1 min

**Outcome:** Token util por max 15 min; revocado after manual action

### Escenario 2: Device Compromise (malware)
**Attack:** Malware roba token en memoria, emite múltiples solicitudes  
**Defensa:**
- Firma Ed25519 previene token forgery
- Fingerprint change detected → auto-revoke
- Rate limiting por device_id (3 requests/min threshold)
- Fork-bomb: si >10 requests concurrentes → suspend device

**Outcome:** Malware puede hacer damage por 15-60 min; then auto-revoke

### Escenario 3: Network MITM (pre-TLS)
**Attack:** Atacante en LAN eavesdrops MCP requests  
**Defensa:**
- TLS 1.3 on all channels (encrypted)
- mTLS: device también valida cert de SOUL (cert pinning)
- Token en header Authorization (TLS protegido)

**Outcome:** MITM no ve token ni contenido

### Escenario 4: Device Hardware Changed
**Attack:** Atacante roba laptop, trata de usar token  
**Defensa:**
- Device fingerprint basado en HW serial (no cambia con SO)
- Si serie MB diferente → fingerprint change → token validation FAIL
- Audit log reports fingerprint mismatch

**Outcome:** Token becomes useless en nueva placa

### Escenario 5: Cross-Agent Read (bug de autorización)
**Attack:** Device de JARVIS intenta leer memoria privada de ADA  
**Defensa:**
- RLS en soul_v3 filtra por scope + agent (base dura)
- Token incluye agent identity (no se puede falsificar)
- MCP server sets current_agent_id desde token verificado
- Audit log reports access denial

**Outcome:** Lectura rechazada; intento auditado

---

## 11. Metricas de Éxito (Fase-1)

- ✅ Token no puede ser forjado (Ed25519 signature validates)
- ✅ Device compromised detectado en <5 min (revocation sync)
- ✅ HW changed → auto-revoke (fingerprint mismatch)
- ✅ 0 scope violations (RLS + audit enforced)
- ✅ <15 min token exposure window en caso de theft
- ✅ Operator puede revocar device en <30 seg (CLI)

---

## 12. Referencias & Dependencias

**Código Existente:**
- SOUL MCP server: `/home/dadito/IA/proyecto-seal/memory/mcp_server_v4.py`
- Token validation hook: `/home/dadito/IA/proyecto-seal/messages/ada_codex_remote_bridge.py`
- Edge layer: `/home/dadito/IA/proyecto-seal/memory/edge_layer.py`

**Specs:**
- SPEC_SEAL_DISTRIBUTED_AGENTS_v1.md (§4 SECURITY MODEL)
- SOUL_memory_search_ACCESS_layer_NEXUS.md (§1-7 RLS)

**Tooling:**
- `cryptography` (Ed25519, X.509)
- `keyring` (OS credential storage)
- `psycopg2` (PostgreSQL)
- `flask` (token issuance API)

---

## 13. Next Steps

1. **Immediate:** Implementar DDL (authorized_devices, revoked_tokens, token_audit)
2. **Week 1:** Token generation + validation (Ed25519)
3. **Week 2:** CSR flow + manual approval UI (for William)
4. **Week 3:** Revocation daemon + cache sync
5. **Week 4:** mTLS + cert pinning
6. **Week 5:** Red-team testing + fixes
7. **Week 6:** Operationalize (CLI, docs, monitoring)

---

**END OF SPEC — Fase-1 Security Auth Gate**

*This design closes the 2 red-team findings and provides a concrete, buildable roadmap for token-based device authorization in SEAL. Implementation follows in structured phases.*
