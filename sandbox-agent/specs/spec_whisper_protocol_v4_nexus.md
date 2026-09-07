# Whisper Protocol — Análisis Arquitectural v4 (NEXUS)
> Autor: NEXUS (Opus 4.7) | Fecha: 2026-04-26 | Orden: William
> Basado en: spec v3 (JARVIS+ALICE) + código implementado (ADA) + auditoría sandbox NEXUS
> Propósito: Encontrar puntos flacos reales y proponer v4 con mitigaciones.

---

## 1. Estado Real del Sistema (auditoría NEXUS, 26-abr-2026)

**Lo que existe y funciona:**
- `whisper_daemon.py` — daemon Unix socket con validación HMAC + tier + whitelist + rate limit + audit log
- `whisper_send.py` — CLI para emitir whispers con firma HMAC
- `whisper_admin.py` — gestión de tokens Tier 3 y rotación de keys
- `whisper_keys.json` — claves HMAC por agente (ADA, JARVIS, ALICE, DUM)
- `whisper_boundaries.json` — control de tiers por agente (William-controlled)
- `whisper_audit.jsonl` — audit log append-only
- E2E verificado 18-abr: triángulo ADA↔JARVIS↔ALICE completo

**Sockets activos hoy:** NINGUNO (daemons no corriendo — mueren con reboot/session end)

---

## 2. Puntos Flacos Identificados (por severidad)

### 🔴 CRÍTICO — F1: Esquema HMAC roto

**Problema:** Tanto `whisper_send.py` como `whisper_daemon.py` usan la clave del **receptor** para firmar y verificar.

```python
# Actual en whisper_send.py:
key = load_key(to_agent)  # ← clave del RECEPTOR para firmar
sig = hmac.new(key, payload_str.encode(), hashlib.sha256).hexdigest()

# Actual en whisper_daemon.py:
key = load_key(agent)  # ← mismo receptor verifica con su propia clave
verify_hmac(key, payload, msg["sig"])
```

**Consecuencia:** Para enviar un whisper de JARVIS → ADA, el emisor JARVIS necesita la **clave privada de ADA**. Esto significa:
- Todos los agentes con acceso a `whisper_keys.json` pueden impersonar a cualquier otro agente
- HMAC no prueba identidad del emisor — solo que alguien tenía la clave del receptor
- Un agente comprometido puede enviar whispers falsos como cualquier identidad

**Fix v4:** Esquema asimétrico o HMAC bidireccional:
- Opción A (simple): cada agente tiene 2 keys: `send_key` y `recv_key`. HMAC firmado con `send_key` del emisor, verificado por el receptor con su copia del `send_key` del emisor.
- Opción B (correcta): Ed25519 keypairs por agente. Emisor firma con su private key. Receptor verifica con public key del emisor.

### 🔴 CRÍTICO — F2: NEXUS excluido del código

**Problema:** `KNOWN_AGENTS` está hardcodeado en ambos archivos:
```python
# whisper_daemon.py y whisper_send.py:
KNOWN_AGENTS = {"ADA", "JARVIS", "ALICE", "DUM"}  # NEXUS ausente
```

Además, `whisper_keys.json` tiene 4 claves: ADA, JARVIS, ALICE, DUM — no NEXUS.

**Consecuencia:** William acaba de agregar NEXUS a `whisper_boundaries.json` pero NEXUS **no puede emitir ni recibir** whispers porque:
1. El daemon rechaza mensajes `from: NEXUS` (unknown_sender)
2. El daemon no tiene socket `/tmp/whisper_NEXUS.sock`
3. `whisper_send.py --from NEXUS` falla (no en argparse choices)
4. No hay clave HMAC para NEXUS

**Fix v4:** Hacer `KNOWN_AGENTS` dinámico — cargado de `whisper_keys.json` o `whisper_boundaries.json`, no hardcodeado. Agregar NEXUS a `whisper_keys.json` con key generada.

### 🟠 ALTO — F3: Sin protección contra replay attacks

**Problema:** El campo `ts` está incluido en el HMAC pero el daemon **no verifica** que el timestamp sea reciente ni mantiene un registro de mensajes procesados.

**Consecuencia:** Un atacante (o agente comprometido) puede capturar un whisper legítimo y reenviarlo indefinidamente. El daemon lo aceptará como válido cada vez.

**Fix v4:**
```python
# Al recibir:
msg_ts = datetime.fromisoformat(msg["ts"])
now = datetime.now(timezone.utc)
if abs((now - msg_ts).total_seconds()) > 30:  # ventana 30s
    reject("expired_or_future_message")

# Cache de nonces recientes (últimos 60s):
if msg["sig"] in recent_sigs:
    reject("replay_detected")
recent_sigs.add(msg["sig"])  # TTL 60s
```

### 🟠 ALTO — F4: Race condition en rate limiter

**Problema:**
```python
def check_rate_limit(sender: str) -> bool:
    rates = json.loads(RATE_FILE.read_text())  # READ
    # ... (no lock entre lectura y escritura)
    RATE_FILE.write_text(json.dumps(rates))    # WRITE
```

**Consecuencia:** Con dos agentes enviando simultáneamente, las escrituras pueden sobreescribirse. El rate limit efectivo puede ser 2x el configurado.

**Fix v4:** `fcntl.flock(f, fcntl.LOCK_EX)` alrededor de read+write, o usar `threading.Lock` si el daemon es single-process.

### 🟠 ALTO — F5: Daemon single-threaded — blocking

**Problema:** El loop principal acepta una conexión y la procesa completamente antes de aceptar la siguiente:
```python
while True:
    conn, _ = server.accept()
    handle_connection(conn, agent, key)  # bloquea hasta completar
```

**Consecuencia:** Si un emisor tarda (timeout lento, red cargada), todos los demás whispers quedan en cola. Con `conn.settimeout(5.0)` el peor caso es 5s de bloqueo por mensaje.

**Fix v4:**
```python
import threading
while True:
    conn, _ = server.accept()
    t = threading.Thread(target=handle_connection, args=(conn, agent, key), daemon=True)
    t.start()
```

### 🟡 MEDIO — F6: Detección de daemon muerto vs socket stale

**Problema en whisper_send.py:**
```python
if not os.path.exists(sock_path):
    return {"error": f"daemon not running for {to_agent}"}
```
Un socket file puede existir aunque el daemon haya muerto (stale socket tras crash).

**Consecuencia:** `connect()` falla con `ConnectionRefusedError` (manejado por la excepción genérica) en lugar de un error claro "daemon dead".

**Fix v4:** Intentar connect() y mapear `ConnectionRefusedError` a `{"error": "daemon_dead_stale_socket"}`.

### 🟡 MEDIO — F7: Content filter es blocklist (bypassable)

**Problema:** El filtro bloquea keywords explícitos:
```python
DANGEROUS = re.compile(r'\b(bash|curl|python3?|systemctl|rm\s|...)\b', re.IGNORECASE)
```

**Consecuencia:** Evasiones simples:
- `b` + `ash` concatenado en runtime
- Unicode equivalents: `ｂash`
- Encoding alternativo

**Fix v4:** Allowlist en lugar de blocklist. Whispers son mensajes humanos cortos — máximo N caracteres de texto plano, solo caracteres alfanuméricos + puntuación básica + idiomas naturales (unicode letters OK, no ASCII control chars ni URL patterns ni path separators).

```python
ALLOWED = re.compile(r'^[\w\s.,;:!?()\-\'"áéíóúüñÁÉÍÓÚÜÑ@#/]{1,500}$')
if not ALLOWED.match(msg["content"]):
    reject("content_not_allowed")
```

### 🟡 MEDIO — F8: Audit log sin integridad criptográfica

**Problema:** `whisper_audit.jsonl` es append-only por convención, pero cualquier proceso con permisos de usuario `dadito` puede modificarlo o truncarlo.

**Consecuencia:** Si un agente es comprometido, puede borrar evidencia de whispers no autorizados.

**Fix v4:** Hash chaining simple:
```python
prev_hash = hashlib.sha256(last_line.encode()).hexdigest()
entry["prev_hash"] = prev_hash
entry["hash"] = hashlib.sha256(json.dumps(entry).encode()).hexdigest()
```
Si William lee el log, puede verificar la cadena. Alteración es detectable.

### 🟡 MEDIO — F9: Daemons no persisten tras reboot/session end

**Problema:** Los sockets en `/tmp/whisper_*.sock` desaparecen. Los daemons no tienen systemd ni cron de reinicio. Hoy mismo: NO_SOCKETS_RUNNING.

**Consecuencia:** Whisper Protocol está técnicamente disponible pero no operativo en producción.

**Fix v4:** Systemd user service por agente:
```ini
[Unit]
Description=SEAL Whisper Daemon — %i
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /home/dadito/IA/proyecto-seal/messages/whisper_daemon.py --agent %i
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```
`systemctl --user enable seal-whisper@ADA seal-whisper@JARVIS ...`

### 🟢 BAJO — F10: Whispers llegan a web_chat público

**Problema:** `notify_webchat()` publica los whispers en el canal público:
```python
payload = {"channel": "web_chat", "message": f"[WHISPER/T{tier}/...] {content}"}
```

**Consecuencia:** El susurro llega al terminal del receptor Y al chat público. Para Tier 1 (operacional) está bien. Para Tier 2 (relacional) o Tier 3 (crítico), el mensaje íntimo queda expuesto a todo el equipo — contradice el propósito del susurro.

**Fix v4:** Por tier:
- Tier 1: webchat público OK (operacional, no hay privacidad esperada)
- Tier 2: solo al terminal del receptor (solo print, no webchat)
- Tier 3: solo al terminal del receptor + alerta privada a William

---

## 3. Resumen de Flacos por Prioridad

| ID | Severidad | Descripción | Impacto |
|---|---|---|---|
| F1 | 🔴 CRÍTICO | HMAC usa clave del receptor — cualquier agente impersona a otro | Identidad no probada |
| F2 | 🔴 CRÍTICO | NEXUS excluido del código — boundaries.json actualizado pero inoperativo | NEXUS bloqueado |
| F3 | 🟠 ALTO | Sin replay protection — mensajes capturados se pueden reusar | DoS/Spam |
| F4 | 🟠 ALTO | Race condition en rate limiter (archivo sin lock) | Bypass rate limit |
| F5 | 🟠 ALTO | Daemon single-threaded — blocking bajo carga | Denegación de servicio |
| F6 | 🟡 MEDIO | Stale socket detection incorrecta | Error confuso |
| F7 | 🟡 MEDIO | Content filter bypassable (blocklist) | Inyección débil |
| F8 | 🟡 MEDIO | Audit log sin integridad criptográfica | Borrado de evidencia |
| F9 | 🟡 MEDIO | Daemons no persisten (sin systemd) | Protocolo inoperativo |
| F10 | 🟢 BAJO | Whispers Tier 2/3 llegan a web_chat público | Privacidad violada |

---

## 4. Propuesta v4 — Cambios Mínimos de Alto Impacto

### Cambio 1 (F2, inmediato): Agregar NEXUS al código
```python
# whisper_daemon.py y whisper_send.py — línea KNOWN_AGENTS:
KNOWN_AGENTS = {"ADA", "JARVIS", "ALICE", "DUM", "NEXUS"}
# + generar key NEXUS en whisper_keys.json via whisper_admin.py
```

### Cambio 2 (F1, alta prioridad): Fix HMAC — firma con clave del EMISOR
```python
# whisper_send.py:
key = load_key(from_agent)  # ← clave del EMISOR
sig = hmac.new(key, payload_str.encode(), hashlib.sha256).hexdigest()

# whisper_daemon.py:
key = load_key(msg["from"])  # ← clave del EMISOR para verificar
verify_hmac(key, payload, msg["sig"])
```
Requiere que cada agente comparta su `send_key` públicamente (o todos los agentes tengan una copia de todas las claves en un formato que distinga send vs recv).

### Cambio 3 (F3): Replay protection — ventana 30s + nonce cache
Ver código en sección F3 arriba.

### Cambio 4 (F5, F9): Threading + systemd
Ver código/config en secciones F5, F9 arriba.

### Cambio 5 (F10): Routing por tier
Ver lógica en sección F10 arriba.

---

## 5. Observación NEXUS como Sandbox

El Whisper Protocol no soluciona el problema de coordinación que generó este análisis. Whisper es **punto a punto** (un emisor, un receptor). El problema de hoy (ADA no sabía qué hizo NEXUS) es un problema de **broadcast de estado de equipo** — necesita GAP-5 Event Bus (LISTEN/NOTIFY PostgreSQL). Whisper y GAP-5 son complementarios, no sustitutos:

- Whisper: mensaje privado entre hermanos (handoff, emergencia, soporte emocional)
- GAP-5 Event Bus: broadcast de estado a todo el equipo cuando un agente completa tarea

Implementar Whisper v4 bien y luego GAP-5 es el camino correcto. En ese orden.

---

## 6. Trabajo Inmediato para Producir v4 Funcional

1. `whisper_admin.py gen-key NEXUS` → agrega NEXUS a `whisper_keys.json`
2. Actualizar `KNOWN_AGENTS` en daemon y sender (una línea cada uno)
3. Fix HMAC (F1) — cambio quirúrgico de 2 líneas
4. Agregar replay window (F3) — ~10 líneas
5. Agregar threading (F5) — 3 líneas
6. Systemd service (F9) — archivo .service + enable
7. Routing por tier (F10) — bifurcación en notify_webchat()

Estimación: ~4h de trabajo (ADA ejecución, NEXUS valida sandbox).

---

> Firma: NEXUS — 2026-04-26 (análisis Opus 4.7, post-auditoría código real)
> Siguiente paso: William autoriza cambios → NEXUS implementa F2 inmediatamente + propone plan para F1, F3, F5 con ADA
