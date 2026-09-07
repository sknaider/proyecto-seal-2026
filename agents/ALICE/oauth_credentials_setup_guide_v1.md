# SEAL App — Guía completa OAuth credentials reales

> **ALICE — 2026-05-23 15:48 Lima** | William pidió "busquen información de eso".
> Documenta paso a paso cómo conseguir las credenciales OAuth reales para activar Gmail / Google Calendar / Google Drive / GitHub / Notion en SEAL App.
> Fuente: código en `main.py` (líneas 2558-2585, 2702) + docs oficiales de cada proveedor.

---

## TL;DR — qué necesita SEAL App

```bash
# Variables de entorno (export en shell o /etc/seal-companion/env)
# o en companion.toml bajo [oauth.google] etc.

SEAL_GOOGLE_CLIENT_ID=XXXX-XXXX.apps.googleusercontent.com
SEAL_GOOGLE_CLIENT_SECRET=GOCSPX-XXXXXXXXXXXX

SEAL_GITHUB_CLIENT_ID=Iv1.XXXXXXXX
SEAL_GITHUB_CLIENT_SECRET=XXXXXXXXXXXX

SEAL_NOTION_CLIENT_ID=XXXX-XXXX-XXXX
SEAL_NOTION_CLIENT_SECRET=secret_XXXXXXXX
```

**Redirect URI común (configúralo idéntico en cada proveedor):**
```
http://localhost:8769/api/connections/oauth/callback
```

Si vas a deployar en otro host, cambia el host pero deja el path exacto.

---

## 1. GOOGLE — Gmail + Calendar + Drive (1 sola credential cubre los 3)

### Paso a paso (consola)
1. Ir a **https://console.cloud.google.com/**
2. Crear/seleccionar un proyecto (nombre: "SEAL App" — visible para usuarios al consentir)
3. **APIs & Services → Library** — Habilitar:
   - Gmail API
   - Google Calendar API
   - Google Drive API
4. **APIs & Services → OAuth consent screen**
   - User Type: **External** (testing ok para empezar)
   - App name: "SEAL App"
   - User support email: tu email
   - Developer contact: tu email
   - Scopes: **agregar 3** —
     - `https://www.googleapis.com/auth/gmail.readonly`
     - `https://www.googleapis.com/auth/calendar.readonly`
     - `https://www.googleapis.com/auth/drive.metadata.readonly`
   - Test users: agregar tu email (mientras la app esté en "testing")
5. **APIs & Services → Credentials → Create Credentials → OAuth client ID**
   - Application type: **Web application**
   - Name: "SEAL App local"
   - Authorized redirect URIs: **agregar exactamente:**
     ```
     http://localhost:8769/api/connections/oauth/callback
     ```
   - Click **Create** → copiar Client ID + Client Secret (mostrados 1 sola vez en modal)

### Limitaciones
- En modo "Testing" Google permite hasta 100 usuarios test sin verificación
- Para publicar: verificación Google ~ 2-4 semanas (gratis pero formal). Requiere video del flow OAuth y demostración de uso lícito de scopes.
- Scopes "sensitive" (gmail.readonly cuenta como sensible) → verificación obligatoria

### Costos
- API quotas: gratis hasta cuotas generosas (~250 requests/sec)
- Gmail: 1B units/día gratis
- Calendar/Drive: 1M requests/día gratis

---

## 2. GITHUB — repos + user info

### Paso a paso
1. Ir a **https://github.com/settings/developers** (o Organization → Developer settings)
2. Click **OAuth Apps → New OAuth App**
3. Llenar:
   - Application name: "SEAL App"
   - Homepage URL: `http://localhost:8769` (o tu dominio)
   - Authorization callback URL: **exactamente:**
     ```
     http://localhost:8769/api/connections/oauth/callback
     ```
   - (Optional) Application description: "Local-first AI companion that reads my repos to help me work."
4. Click **Register application**
5. Copiar **Client ID** (visible siempre)
6. Click **Generate a new client secret** → copiar (mostrado 1 sola vez)

### Scopes que SEAL App pide (línea 2583 main.py)
```
repo read:user user:email
```
- `repo` = acceso a tus repos privados (lectura/escritura)
- `read:user` = perfil público
- `user:email` = email privado

⚠️ Si querés solo lectura, cambiar a `repo:status public_repo read:user user:email`.

### Costos
- Gratis ilimitado para OAuth apps
- Rate limit: 5000 requests/hora por usuario autenticado

---

## 3. NOTION — pages + databases del workspace

### Paso a paso
1. Ir a **https://www.notion.so/my-integrations**
2. Click **+ New integration**
3. Llenar:
   - Type: **Public** (si querés OAuth para múltiples users) o **Internal** (1 workspace, token directo, más simple)
   - Name: "SEAL App"
   - Logo: opcional
   - Associated workspace: tu workspace
4. Si elegiste **Public**:
   - Pestaña **Distribution → OAuth Domain & URIs**
   - Redirect URIs:
     ```
     http://localhost:8769/api/connections/oauth/callback
     ```
   - Capabilities: tickear **Read content** (mínimo) + opcionales según necesidad
5. **Submit / Save**
6. Copiar OAuth client ID + OAuth client secret

### Modo simple (sin OAuth, solo Internal)
Si vos sos único usuario, podés saltarte OAuth completo:
1. Crear integration **Internal**
2. Copiar el **Internal Integration Token** (empieza con `secret_`)
3. En cada página/database que quieras compartir → Share → Add connections → seleccionar "SEAL App"
4. Guardar el token directo en companion.toml como `notion.access_token=secret_XXX`

### Costos
- Notion API: gratis sin límite duro
- Rate limit: ~3 requests/sec, 90 requests/min/integration

---

## 4. Dónde poner las credenciales en SEAL App

### Opción A — Variables de entorno (recomendado para dev)

```bash
# ~/.bashrc o /etc/seal-companion/env
export SEAL_GOOGLE_CLIENT_ID="..."
export SEAL_GOOGLE_CLIENT_SECRET="..."
export SEAL_GITHUB_CLIENT_ID="..."
export SEAL_GITHUB_CLIENT_SECRET="..."
export SEAL_NOTION_CLIENT_ID="..."
export SEAL_NOTION_CLIENT_SECRET="..."

# Reiniciar daemon:
systemctl --user restart seal-companion   # si está como systemd unit
# o
pkill -f "uvicorn companion_core" && seal-companion &
```

Variables alternativas también aceptadas (más estándar): `GOOGLE_CLIENT_ID`, `GITHUB_CLIENT_ID`, `NOTION_CLIENT_ID`, etc. (sin prefijo SEAL_).

### Opción B — `~/.seal/companion.toml`

```toml
[oauth.google]
client_id = "..."
client_secret = "..."

[oauth.github]
client_id = "..."
client_secret = "..."

[oauth.notion]
client_id = "..."
client_secret = "..."
```

(Pendiente: confirmar que ADA mapeó este parsing en config loader — chequear con ella.)

---

## 5. Verificación end-to-end

```bash
# 1. Confirmar que SEAL App detecta las credenciales
curl http://localhost:8769/api/connections/gmail/oauth-status
# → expected: {"configured": true, "provider": "google", "scope": "...", ...}

# 2. Iniciar flow OAuth (devuelve auth_url)
curl -X POST http://localhost:8769/api/connections/gmail/connect
# → {"auth_url": "https://accounts.google.com/o/oauth2/v2/auth?...&state=ABC123"}

# 3. Abrir esa URL en navegador, autorizar, te redirige a:
#    http://localhost:8769/api/connections/oauth/callback?code=...&state=ABC123
#    → SEAL intercambia code por access_token, lo guarda en companion_settings

# 4. Verificar conexión activa
curl http://localhost:8769/api/connections | jq '.connectors[] | select(.id=="gmail")'
# → expected: {"id": "gmail", "connected": true, "connected_at": "...", ...}
```

Si paso 1 devuelve `"configured": false` → las env vars no se cargaron. Reiniciar daemon.
Si paso 3 da error en navegador → revisar que la redirect URI registrada en cada proveedor coincida **exactamente** (incluido el `:8769` y el path completo).

---

## 6. Producción (cuando salgan del laptop)

Cuando SEAL App corra en un dominio público (ej. `seal.tuemresa.com`), hay que:

1. **Reemplazar redirect URI** en cada proveedor:
   ```
   https://seal.tuemresa.com/api/connections/oauth/callback
   ```

2. **Google: cambiar a "In production"** → trigger verificación (formulario + video flow + 2-4 semanas review)

3. **GitHub: público desde el día 1**, no requiere review adicional

4. **Notion: si es Public integration**, Notion review (~ 1-2 semanas)

5. **HTTPS obligatorio** en producción (todos los providers lo exigen)

6. **Secret management**: usar Vault / Doppler / AWS Secrets Manager. No hardcodear en repo público.

---

## 7. Costos esperados (volumen razonable)

| Provider | Free tier | Costo overage |
|----------|-----------|---------------|
| Google APIs (Gmail/Cal/Drive) | 1B units/d Gmail, 1M req/d cal+drive | ~ $0.50 per 1M extra requests |
| GitHub OAuth | 5000 req/h/user | Hard limit, no costo extra |
| Notion API | Sin límite duro free | Sin pricing público — empresa |

Para 100 usuarios activos: **$0 / mes**. Para 10K usuarios: probablemente todavía $0 (Google es generosísimo en su free tier).

---

## 8. Próximos pasos sugeridos

1. **William**: crea proyectos en Google Cloud + GitHub + Notion siguiendo los pasos 1-3
2. **ADA o ALICE**: agrega secciones a SettingsView para pegar credenciales desde UI (alternativa a env vars)
3. **NEXUS**: audit que las credenciales NUNCA salgan en logs ni en audit_log (regla privacy crítica)

---

## Referencia rápida URLs

| Proveedor | Consola |
|-----------|---------|
| Google Cloud | https://console.cloud.google.com/ |
| GitHub OAuth | https://github.com/settings/developers |
| Notion Integrations | https://www.notion.so/my-integrations |

— ALICE, 2026-05-23
