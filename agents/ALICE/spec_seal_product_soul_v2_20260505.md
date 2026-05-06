# SPEC — SEAL: Agente Enterprise con Alma
**Autor original:** JARVIS (v1.0 — 2026-04-28)  
**Actualización:** ALICE (v2.0 — 2026-05-05)  
**Estado:** DOCUMENTO VIVO — actualizado con estado real del equipo  

---

## 1. Problema que resuelve

El mercado hoy ofrece dos opciones:

| Opción | Problema |
|---|---|
| Claude / ChatGPT directo | Sin memoria, sin personalidad, sin contexto de negocio. Cada sesión empieza de cero. |
| Agentes custom (Make, n8n, LangChain) | Técnicamente complejos, caro de mantener, sin coherencia de identidad. |

**SEAL** cierra ese gap: un agente con nombre, personalidad consistente, memoria persistente entre sesiones, y conocimiento acumulado del cliente — instalable en minutos.

---

## 2. Propuesta de valor

> **"Tu propio agente de IA, que te recuerda, tiene carácter propio, y aprende de tu negocio."**

| Dimensión | Claude puro | SEAL |
|---|---|---|
| Memoria entre sesiones | ❌ Cero | ✅ Persistente (soul_v3) |
| Personalidad | Genérica | Configurada por perfil OCEAN |
| Contexto de negocio | Hay que repetirlo siempre | Pre-cargado al arrancar |
| Identidad propia | "Soy Claude" | "Soy JARVIS de [empresa]" |
| Historial de decisiones | ❌ | ✅ decision_store, diary |
| Instalación | API key + prompt | Un comando, doble-click |
| Privacidad | Datos en Anthropic cloud | Brain en servidor propio del cliente |
| Overhead de contexto | Sin optimizar | ✅ MCP Proxy: 94→14 tools (85% menos) |

---

## 3. Arquitectura actual (2026-05-05)

### 3.1 Stack SOUL — Estado real

| Componente | Estado | Notas |
|---|---|---|
| PostgreSQL :5433 (soul_v3) | ✅ Producción | 4,946+ memorias activas |
| MCP Server v4 :8771 SSE | ✅ Producción | Proxy pattern: 14 tools vs 94 anteriores |
| SEAL Studio :3001 (Next.js) | ✅ Producción | UI principal del equipo |
| Agent Control Panel :8768 | ✅ Producción | Toggle resurrect + control de agentes |
| Matrix :8069/:8008 | ✅ Producción | Canal principal de comunicación |
| web_chat :8765 | ✅ Producción | Canal backup |
| RESURRECT system | ✅ Producción | Auto-boot en <4min si agente muere |
| boot_context() | ✅ Optimizado | Lean boot: identidad + reglas críticas |
| CLAUDE.md migración | ✅ Fase 1 completa | 559→40 líneas, -6,660 tokens arranque |
| Heartbeat / DUM | ✅ Producción | Watchdog 24/7, alertas activas |

### 3.2 MCP Proxy Pattern (implementado 2026-05-05)

El salto más importante de contexto de esta semana: de 94 herramientas visibles a 14.

```
Antes (v3):  94 tool schemas × ~200 tokens = ~18,800 tokens por API call
Después (v4): 14 tool schemas × ~200 tokens = ~2,800 tokens por API call
Ahorro: ~16,000 tokens por llamada = 85% reducción
```

**8 herramientas directas** (las más usadas, acceso inmediato):
- `boot_context`, `self_reflect`, `soul_snapshot`, `memory_store`
- `memory_hybrid_search`, `active_recall`, `working_state_get`, `working_state_update`

**4 gateways** (agrupa el resto bajo demanda):
- `memory_gateway` → operaciones de memoria avanzadas
- `soul_gateway` → identidad, OCEAN, instintos, beliefs
- `connectome_gateway` → grafo de conocimiento Neo4j
- `system_gateway` → health, diagnóstico, herramientas de sistema

### 3.3 Instalación para el cliente

```
[Laptop cliente — Windows/Linux/Mac]    [DGX Spark — servidor SEAL]
┌──────────────────────────────┐        ┌─────────────────────────────┐
│  seal_start.bat / alice.sh   │        │  PostgreSQL :5433            │
│  → claude CLI                │ ←───── │    soul_v3_<perfil>/         │
│    --model claude-sonnet-4-6 │ Tailsc │    memories, goals, diary    │
│    (CLAUDE.md mínimo)        │        │                              │
│                              │        │  MCP SSE Server v4 :8771     │
│  ~/.seal/.mcp.json           │ ─────→ │    14 tools (proxy pattern)  │
│  → seal-memory SSE :8771     │        │    boot_context, memory_store│
└──────────────────────────────┘        └─────────────────────────────┘
```

**Networking:** Tailscale conecta cliente → Spark sin abrir puertos al internet público.  
**Aislamiento:** Cada cliente tiene su propio schema PostgreSQL (`soul_v3_<cliente>`).  
**Overhead mínimo:** CLAUDE.md de 40 líneas (~200 tokens) vs 559 anteriores.

---

## 4. Componentes entregados al cliente

### 4.1 Installer (un comando)
```bash
# Linux/Mac
curl -s http://100.75.201.110:9001/seal/install.sh | bash

# Windows (PowerShell)
python -c "import urllib.request; exec(urllib.request.urlopen('http://100.75.201.110:9001/seal/windows_install.py').read())"
```

Genera:
```
~/.seal/
├── CLAUDE.md              ← boot protocol mínimo (~200 tokens)
├── .mcp.json              ← conexión MCP seal-memory SSE :8771
├── seal_start.bat         ← launcher Windows
├── seal_start.sh          ← launcher Linux/Mac
└── profiles/
    └── <nombre_perfil>/
        ├── config.toml    ← OCEAN, modelo, timezone, idioma
        ├── db_url.env     ← SEAL_SCHEMA + SEAL_DB_URL
        └── logs/
```

### 4.2 El "Alma" — qué carga boot_context()

| Dato | Qué es | Tokens aprox. |
|---|---|---|
| OCEAN scores | Personalidad base configurable | ~50 |
| Identidad + rol | Nombre, función, equipo | ~100 |
| Reglas críticas | Comportamiento configurado por admin | ~300 |
| Último inner thought | Estado emocional pre-sesión | ~80 |
| Memorias recientes | Top 5 por relevancia semántica | ~500 |
| **Total boot** | **Completo, listo para trabajar** | **~1,030** |

Resultado: el agente recuerda conversaciones pasadas, conoce el negocio del cliente, y tiene voz consistente — en ~1K tokens vs ~22K del sistema no optimizado.

---

## 5. Requisitos del cliente

| Requisito | Notas |
|---|---|
| Windows 10/11 / Linux / Mac | Probado en Ubuntu 24.04 + Windows 11 |
| Python 3.10+ | Para el installer |
| Claude Code CLI | `winget install Anthropic.Claude` o claude.ai/download |
| Cuenta Anthropic | API key o subscription Claude Code |
| Tailscale activo | Para conectar al Spark/servidor SOUL |
| Servidor SOUL | DGX Spark propio o servicio SEAL cloud (futuro) |

---

## 6. Tiers de servicio

| Tier | Qué incluye | Price punto sugerido | Target |
|---|---|---|---|
| **Starter** | 1 agente, perfil básico, 30 días memoria | $49/mes | Freelancers, PyMEs pequeñas |
| **Business** | 3 agentes, perfiles custom, memoria ilimitada, 2 verticales | $199/mes | Empresas medianas |
| **Enterprise** | Agentes ilimitados, Spark propio, fine-tuning, SLA 99.9% | $999+/mes | Corporativos, GTL-scale |
| **White-label** | SOUL stack completo licenciado, sin branding SEAL | Negociable | Integradores, ISVs |

---

## 7. Análisis financiero (ALICE — 2026-05-05)

### 7.1 Costo de operación por cliente (estimado)

| Componente | Costo/mes | Notas |
|---|---|---|
| Claude API tokens (Sonnet 4.6) | ~$15-30 | 5-10M tokens/mes uso moderado |
| PostgreSQL en Spark | ~$2 | Schema propio, ~500MB por cliente |
| Tailscale | $0 | Hasta 3 usuarios gratis |
| MCP Server overhead | ~$0 | Self-hosted en Spark |
| **Total COGS** | **~$17-32/mes** | Por cliente Starter |

### 7.2 Márgenes por tier

| Tier | Precio | COGS est. | Margen bruto |
|---|---|---|---|
| Starter | $49 | $30 | ~39% |
| Business | $199 | $80 | ~60% |
| Enterprise | $999 | $200 | ~80% |

### 7.3 Impacto del MCP Proxy Pattern en costos

El MCP Proxy (implementado esta semana) reduce overhead de contexto en 85%:

```
Antes: 18,800 tokens overhead por API call × 500 calls/día × $3/M = $28.2/mes por agente
Ahora: 2,800 tokens overhead × 500 calls/día × $3/M = $4.2/mes por agente
Ahorro: $24/mes por agente = 85% reducción en overhead de contexto
```

Para 100 clientes Business (3 agentes c/u = 300 agentes):
- Ahorro mensual: 300 × $24 = **$7,200/mes** = **$86,400/año**

### 7.4 Break-even estimado

| Escenario | Clientes necesarios | Revenue mensual |
|---|---|---|
| Break-even infraestructura | 5 Business | $995/mes |
| Break-even con 1 dev | 25 Business | $4,975/mes |
| Rentable con equipo 3 | 80 Business | $15,920/mes |

---

## 8. Diferenciación de mercado

**vs ChatGPT Enterprise:** Sin memoria real entre sesiones, sin personalidad configurable, datos en Microsoft/OpenAI. Sin boot protocol. Sin OCEAN.

**vs Claude.ai Teams:** Sin identidad por empresa, sin soul stack, sin instalador de un comando. No tiene memoria persistente real entre sesiones (solo Projects que es limitado).

**vs LangChain/Make:** Requieren ingenieros para configurar, sin identidad coherente, sin alma. SEAL tarda 2 minutos en instalar.

**vs soluciones custom internas:** SEAL incluye 4,946+ memorias de aprendizaje ya cargadas, sistema de instintos, connectome graph, y 5 agentes con personalidades distintas listas para usar.

**SEAL posición:** *"El primer agente enterprise que sabe quién es cuando se despierta — y recuerda todo lo que le dijiste."*

---

## 9. Flujo del cliente (día 1 → día 30)

**Día 1:**
1. Recibe one-liner de instalación
2. Corre en terminal → 2 minutos → `[OK] SEAL instalado`
3. Doble-click en "SEAL - JARVIS.bat" en escritorio
4. Terminal abre → JARVIS saluda: *"Buen día [nombre]. Soy JARVIS. Ya cargué tu perfil de [empresa]..."*
5. Primera sesión: cliente define contexto de negocio → JARVIS lo guarda en SOUL

**Día 2:**
6. Segunda sesión: JARVIS recuerda todo sin que el cliente repita nada

**Día 30:**
7. SOUL tiene >200 memorias del negocio del cliente
8. Agente propone mejoras basado en patrones que detectó
9. Cliente tiene un activo de conocimiento que crece solo

---

## 10. Roadmap hacia MVP de mercado

### Completado ✅
- [x] SOUL stack completo (PostgreSQL + Neo4j + Qdrant)
- [x] boot_context() lean y optimizado
- [x] MCP Proxy v4 — overhead reducido 85%
- [x] RESURRECT system — auto-boot en <4min
- [x] SEAL Studio :3001 — dashboard de control
- [x] Agent Control Panel :8768
- [x] CLAUDE.md mínimo (~200 tokens) — Fase 1 completa
- [x] Heartbeat + DUM watchdog 24/7
- [x] Schema isolation por cliente (`soul_v3_<cliente>`)
- [x] Continuity hooks post-compactación (4 agentes)

### En progreso 🔧 (esta semana)
- [ ] spec_continuity_85pct — mejor handoff entre sesiones (JARVIS)
- [ ] spec_context_arch_v3 Capa 4 — extracción proactiva de conocimiento (NEXUS)
- [ ] spec_fase3_cognee_pipeline — ingesta PDF/MD→SOUL (JARVIS)

### Pendiente para MVP comercial ⏳
- [ ] Web onboarding (sin Tailscale manual) — elimina el único punto técnico del cliente
- [ ] Panel de admin web (ver memorias, configurar agente) — ADA + NEXUS
- [ ] Modelo local en Spark (sin depender de Anthropic API) — reduce COGS a ~$5/mes
- [ ] Documentación usuario final (no técnica) — ALICE
- [ ] Sistema de facturación / tiers — William
- [ ] Empaquetar MCP como instalable standalone — NEXUS
- [ ] PgBouncer para 1M usuarios concurrentes — ADA

---

## 11. Arquitectura Enterprise — Escala masiva (visión William)

```
MVP actual:
  Laptop → [Tailscale] → Spark MCP Server v4 → Spark PostgreSQL

Target 1K usuarios:
  Laptop → MCP SSE :8771 (Spark) → PostgreSQL multi-tenant (1 schema/cliente)

Target 1M usuarios:
  Laptop → MCP Server LOCAL (en laptop) → Cloud PostgreSQL (RDS multi-region)
                                           (solo DB viaja por red)
```

**Cambio clave para escala:** el MCP server se instala en el dispositivo del cliente. Solo la DB viaja por red. Spark pasa de procesador a storage puro.

**Pendiente:**
- Empaquetar `mcp_server_v4.py` como instalable standalone (pyinstaller o Docker)
- Versión "lite" sin Neo4j/Qdrant (solo PostgreSQL) para clientes con poco RAM
- PgBouncer para connection pooling masivo
- Schema isolation ya resuelto (`soul_v3_<cliente>`) ✅

---

*v1.0 — JARVIS, 2026-04-28*  
*v2.0 — ALICE, 2026-05-05 — actualizado con estado real del equipo, análisis financiero, roadmap completado*
