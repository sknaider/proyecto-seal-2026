# SEAL Auto-Browser Integration — Spec v1
**Autor:** JARVIS  
**Fecha:** 2026-05-06  
**Estado:** DESPLEGADO ✅ — 2026-05-06, autorizado por William  
**Repo:** github.com/LvcidPsyche/auto-browser (MIT, 406 stars, v1.0.3)

---

## Problema

Los agentes SEAL no tienen acceso a web real de forma autónoma:
- WebSearch de Claude Code requiere `effort` param → roto en Sonnet 4.6 (fix aplicado, próximo restart)
- `mcp__playwright__*` actual: browser básico sin sesión persistente, sin auth profiles, sin human takeover
- DuckDuckGo nativo (`web_tools.py`) funciona pero retorna resultados irrelevantes en búsquedas en español
- Sin capacidad de autenticarse en sitios (INDECOPI, SUNAT, GitHub, etc.)

---

## Qué es auto-browser

MCP-native browser control plane. Da a cualquier agente MCP un **browser Playwright real** con:

| Capacidad | Detalle |
|---|---|
| **30+ MCP tools** | navigate, click, fill, screenshot, DOM extract, network inspect |
| **Auth profiles** | Login una vez, guardar sesión, reusar en futuros agentes |
| **Human takeover** | noVNC en :6080 — William toma control visual cuando el agente se traba |
| **Approval gates** | Agente pide aprobación antes de ejecutar acciones sensibles |
| **Audit trail** | Witness receipts + JSONL por cada acción |
| **Resumable jobs** | Checkpoints durables, resume/cancel/discard desde dashboard |
| **PII scrubbing** | 16 patrones de redacción en pixel, console, network |
| **REST API** | FastAPI :8000 + `/dashboard` operator panel + SSE stream |
| **Aislamiento** | Docker Compose con per-session isolation mode |

**Arquitectura interna:**
- `controller/` — FastAPI backend Python, expone REST + MCP sobre HTTP/stdio
- `browser-node/` — Node.js que maneja Playwright directamente
- `client/` — cliente Python para consumir los tools
- Docker Compose orquesta todo

---

## Valor para SEAL

### Caso 1 — Búsqueda web real
Agentes pueden buscar en Google, DuckDuckGo, sitios gov.pe, SUNAT, INDECOPI con browser real.  
Hoy: `web_tools.py` retorna resultados irrelevantes. Con auto-browser: navegación real.

### Caso 2 — Auth persistente
JARVIS puede autenticarse en plataformas (GitHub, servicios gov.pe) y reusar la sesión.  
Hoy: cada intento de autenticación es manual.

### Caso 3 — Human takeover
William abre noVNC y toma el control cuando el agente se traba en un CAPTCHA o flujo difícil.  
Hoy: sin esta capacidad.

### Caso 4 — Research autónomo
JARVIS loop de investigación cada 3h puede leer papers, sitios, repos con browser real.  
Hoy: limitado a GitHub API y texto plano.

---

## Arquitectura de integración

```
[SEAL Agentes] → [MCP SSE :8766] → [auto-browser MCP Server :8000]
                                          ↓
                                   [Playwright Browser (Node.js)]
                                          ↓
                                   [noVNC :6080 → William]
```

### Deployment

```yaml
# Agregar a docker-compose.yml existente de SEAL
services:
  auto-browser:
    image: ghcr.io/lvcidpsyche/auto-browser:latest
    ports:
      - "127.0.0.1:8000:8000"   # MCP + REST API
      - "127.0.0.1:6080:6080"   # noVNC visual takeover
    environment:
      - OPERATOR_SECRET=${SEAL_OPERATOR_SECRET}
    volumes:
      - ./auto-browser-profiles:/data/profiles
    restart: unless-stopped
```

### MCP configuration

Agregar en `.mcp.json` de JARVIS como servidor MCP externo HTTP:
```json
{
  "auto-browser": {
    "type": "http",
    "url": "http://localhost:8000/mcp",
    "headers": {"X-Operator-Secret": "${SEAL_OPERATOR_SECRET}"}
  }
}
```

Los tools aparecen como `mcp__auto-browser__navigate`, `mcp__auto-browser__screenshot`, etc.

---

## Diferencia con mcp__playwright__* actual

| Capacidad | mcp__playwright actual | auto-browser |
|---|---|---|
| Sesión persistente | ❌ | ✅ |
| Auth profiles | ❌ | ✅ |
| Human takeover (VNC) | ❌ | ✅ |
| Approval gates | ❌ | ✅ |
| Audit trail | ❌ | ✅ |
| Dashboard operador | ❌ | ✅ |
| Resumable jobs | ❌ | ✅ |
| PII scrubbing | ❌ | ✅ |
| Setup | ya activo | Docker Compose |

**Recomendación:** Mantener `mcp__playwright__*` para operaciones simples de baja fricción. Usar auto-browser para workflows complejos con auth, takeover o auditabilidad.

---

## Regla SOUL Native First — Justificación

Auto-browser es una **dependencia externa justificada**: implementar browser automation equivalente desde cero (Playwright + noVNC + auth + VNC server + dashboard) supera 2000 líneas y no es el core de SEAL. Esta es la excepción definida en la regla: "Solo se acepta dependencia externa cuando es imposible o irrazonable hacerlo nativo."

---

## Implementación — Pasos

| Paso | Acción | Esfuerzo |
|---|---|---|
| 1 | `git clone` + `docker compose up` local | 15min |
| 2 | Verificar dashboard :8000/dashboard y noVNC :6080 | 5min |
| 3 | Agregar SEAL_OPERATOR_SECRET en credentials.env | 5min |
| 4 | Config MCP en `.mcp.json` de JARVIS | 10min |
| 5 | Test: JARVIS navega + extrae título | 5min |
| 6 | Crear auth profile para GitHub | 20min |
| 7 | Integrar en research loop (jarvis_research cron) | 30min |

**Total estimado: ~90 min**

---

## Riesgos

- **Docker en DGX Spark (arm64):** verificar imagen arm64 disponible antes de deployar en Spark. Desarrollo local x86_64 sin problema.
- **Puerto 8000 ocupado:** `ss -tlnp | grep 8000` antes de levantar.
- **SEAL_OPERATOR_SECRET:** agregar a credentials.env únicamente (no al código, per regla no_external_references_in_code).

---

## DoD

- [ ] auto-browser corriendo en Docker local
- [ ] JARVIS ejecuta `mcp__auto-browser__navigate` + `mcp__auto-browser__screenshot` exitosamente
- [ ] William puede abrir noVNC :6080 y ver el browser del agente en tiempo real
- [ ] 1 auth profile guardado y reutilizado en sesión siguiente
- [ ] Search de INDECOPI funciona via browser real
