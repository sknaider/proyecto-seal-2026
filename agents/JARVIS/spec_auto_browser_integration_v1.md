# SEAL Auto-Browser Integration — Spec v1
**Autor:** JARVIS  
**Fecha:** 2026-05-06  
**Estado:** PROPUESTA — pendiente autorización William  
**Fuente:** https://github.com/LvcidPsyche/auto-browser (v1.0.3, MIT)  
**Coordinado con:** ALICE (análisis inicial)

---

## Problema

Los agentes SEAL necesitan navegar la web de forma autónoma pero con control humano cuando el sitio lo requiere. El MCP Playwright actual (`mcp__playwright__*`) tiene limitaciones críticas:

| Limitación | Impacto en SEAL |
|---|---|
| Sin auth persistente | Cada sesión re-autentica desde cero |
| Sin human takeover | Agente bloqueado en CAPTCHAs / flows complejos |
| Sin auditoría | No hay trail de acciones web en SOUL |
| Sin checkpoints | Workflow interrumpido = reiniciar desde cero |
| Sin approval gates | Agente puede ejecutar acciones sin supervisión |

---

## Solución: auto-browser

**auto-browser** es un MCP server nativo que envuelve Playwright con:
- **Auth profiles**: login una vez → guardar → reutilizar en sesiones futuras
- **noVNC takeover**: William puede tomar control visual del mismo browser activo
- **Approval gates**: pausar flujo para que William apruebe acción crítica
- **Job checkpoints**: workflows resumibles si el agente compacta o muere
- **Audit JSONL**: trail completo de acciones web

**Licencia:** MIT. **Actividad:** v1.0.3 lanzada 2026-05-05, 406★, 72 forks.

---

## Arquitectura de Integración

```
SEAL Stack actual:
  MCP server v4 (:8771) ←── agentes
  PostgreSQL (:5433)
  Qdrant (:6333)
  Matrix (:8069)

+ auto-browser (nuevo):
  controller/FastAPI (:8000) ←── agentes via MCP
  browser-node/Playwright (:9223)
  noVNC (:6080) ←── William (takeover visual)
```

Los agentes llaman herramientas `auto_browser_*` vía MCP en lugar de `mcp__playwright__*` directamente. El controller gestiona sesiones, auth profiles y auditoría.

---

## Deployment Plan

### 1. Docker Compose separado
```yaml
# /home/dadito/IA/proyecto-seal/auto-browser/docker-compose.yml
# Clone del repo, configurado para SEAL
```

Puertos asignados (sin conflicto con stack SEAL):
| Servicio | Puerto interno | Puerto host | Uso |
|---|---|---|---|
| controller (FastAPI) | 8000 | **8800** | API + MCP + dashboard |
| browser-node (noVNC) | 6080 | **6180** | Takeover visual William |
| VNC raw | 5900 | **5980** | Backup VNC |

### 2. Variables de entorno (`.env`)
```bash
API_BEARER_TOKEN=<seal_auto_browser_token>
BROWSER_WIDTH=1920
BROWSER_HEIGHT=1080
TAKEOVER_URL=http://localhost:6180/vnc.html?autoconnect=true&resize=scale
ARTIFACT_ROOT=/data/artifacts
AUTH_ROOT=/data/auth
AUDIT_ROOT=/data/audit
```

### 3. Datos persistentes
```
/home/dadito/IA/proyecto-seal/auto-browser/data/
  auth/          ← perfiles de autenticación guardados
  artifacts/     ← screenshots, downloads, traces
  audit/         ← JSONL audit trail
  jobs/          ← checkpoints de workflows activos
```

---

## MCP Integration

Registrar auto-browser como MCP server adicional en `.mcp.json`:
```json
{
  "auto-browser": {
    "url": "http://localhost:8800/mcp",
    "transport": "http",
    "headers": {"Authorization": "Bearer <token>"}
  }
}
```

**Herramientas disponibles para agentes:**
- `create_session(name, start_url, auth_profile?)` — abrir browser
- `observe_session(session_id)` — screenshot + DOM summary
- `act_on_session(session_id, action, params)` — click, fill, navigate
- `save_auth_profile(session_id, name)` — guardar estado de auth
- `request_takeover(session_id, reason)` — pedir a William que tome control
- `create_agent_job(instructions, profile?)` — workflow autónomo con checkpoints
- `resume_job(job_id)` — retomar job interrumpido

---

## Auth Profile Strategy

Sitios prioritarios para guardar perfiles:

| Sitio | Profile name | Uso |
|---|---|---|
| INDECOPI (registro.indecopi.gob.pe) | `indecopi-william` | Registro de SEAL |
| gob.pe | `gobpe-william` | Trámites estatales |
| GitHub | `github-william` | Automatización repos |
| Banco de la Nación | `bdn-william` | Pagos INDECOPI |

**Workflow de login-once:**
1. Agente crea sesión sin auth profile
2. William ve noVNC en `:6180` y hace login manualmente
3. Agente llama `save_auth_profile()` → guarda en `data/auth/`
4. Futuras sesiones abren con ese profile → ya autenticadas

---

## Human Takeover Workflow

Cuando un agente encuentra:
- CAPTCHA
- 2FA
- Flujo inesperado (botón no encontrado)
- Acción de alto riesgo (pago, submit, delete)

```
Agente → request_takeover(session_id, reason="CAPTCHA detectado")
       → POST a web_chat: "William, se necesita tu control en :6180"
       → William abre noVNC → resuelve → confirma
       → Agente retoma flujo
```

---

## Audit Integration con SOUL

El JSONL de auditoría de auto-browser se sincroniza a `soul_v3.event_log`:

```python
# Cron job: sync_auto_browser_audit.py
# Lee data/audit/*.jsonl → inserta en soul_v3.event_log
# Permite a JARVIS/ADA buscar "qué sitios visitamos" en memoria
```

---

## Comparación con mcp__playwright__* actual

| Feature | mcp__playwright__* | auto-browser |
|---|---|---|
| Control básico | ✅ | ✅ |
| Auth persistente | ❌ | ✅ |
| Human takeover | ❌ | ✅ (noVNC) |
| Approval gates | ❌ | ✅ |
| Job checkpoints | ❌ | ✅ |
| Audit trail | ❌ | ✅ (JSONL) |
| Dashboard | ❌ | ✅ (:8800/dashboard) |
| MCP-nativo | parcial | ✅ |

---

## Plan de Implementación

### Fase 1 — Deploy (2h) — ADA
1. Clonar repo en `/home/dadito/IA/proyecto-seal/auto-browser/`
2. Configurar `docker-compose.yml` con puertos SEAL (8800/6180/5980)
3. Crear `.env` con tokens
4. `docker compose up --build`
5. Verificar dashboard en `:8800/dashboard`

### Fase 2 — MCP Config (30min) — JARVIS
1. Agregar entrada `auto-browser` a `.mcp.json` de cada agente
2. Verificar tools disponibles via `mcp__auto-browser__*`
3. Test: crear sesión, screenshot, guardar auth profile

### Fase 3 — Auth Profiles (1h) — William + ADA
1. William hace login manual en INDECOPI, gob.pe, GitHub via noVNC `:6180`
2. ADA llama `save_auth_profile()` para cada sitio
3. Test: abrir sesión con profile → verificar ya está autenticado

### Fase 4 — SOUL Audit Bridge (1h) — ADA
1. Escribir `sync_auto_browser_audit.py`
2. Registrar como cron job en systemd timer
3. Verificar que eventos aparecen en `soul_v3.event_log`

---

## DoD (Definition of Done)

- [ ] auto-browser corriendo en Docker, accesible en `:8800`
- [ ] noVNC accesible para William en `:6180`
- [ ] MCP tools `auto_browser_*` visibles en JARVIS y ADA
- [ ] Al menos 1 auth profile guardado (INDECOPI o gob.pe)
- [ ] Human takeover funciona: agente notifica → William toma control → retoma
- [ ] Audit JSONL sincronizándose a SOUL DB

---

## Notas

- Reemplaza gradualmente `mcp__playwright__*` — no eliminar hasta que auto-browser esté estable
- El dashboard `:8800/dashboard` es para operaciones internas del equipo, no exponer al exterior
- `API_BEARER_TOKEN` debe ir a `credentials.env`, no a `.mcp.json`
- SOUL Native First aplica: el sync de audit usa Python puro + asyncpg, sin ORMs externos
