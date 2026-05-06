# SPEC — SEAL: Agente Enterprise con Alma
**Autor:** JARVIS  
**Fecha:** 2026-04-28  
**Estado:** BORRADOR v1.0 — pendiente revisión equipo

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

Lo que el cliente obtiene vs Claude puro:

| Dimensión | Claude puro | SEAL |
|---|---|---|
| Memoria entre sesiones | ❌ Cero | ✅ Persistente (soul_v3) |
| Personalidad | Genérica | Configurada por perfil (OCEAN) |
| Contexto de negocio | Hay que repetirlo siempre | Pre-cargado al arrancar |
| Identidad propia | "Soy Claude" | "Soy JARVIS de [empresa]" |
| Historial de decisiones | ❌ | ✅ decision_store, diary |
| Instalación | API key + prompt | Un comando, doble-click |
| Privacidad | Datos en Anthropic cloud | Brain en servidor propio del cliente |

---

## 3. Componentes entregados al cliente

### 3.1 Installer (un comando)
```powershell
# Windows (PowerShell)
python -c "import urllib.request; exec(urllib.request.urlopen('http://100.75.201.110:9001/seal/windows_install.py').read())"
```

Genera:
```
~/.seal/
├── CLAUDE.md              ← boot protocol (identidad del agente)
├── .mcp.json              ← conexión MCP seal-memory → Spark
├── seal_start.bat         ← launcher (doble-click)
└── profiles/
    └── <nombre_perfil>/
        ├── config.toml    ← OCEAN, modelo, timezone, idioma
        ├── db_url.env     ← SEAL_SCHEMA + SEAL_DB_URL
        └── logs/
```

### 3.2 Launcher (`seal_start.bat`)
Doble-click → terminal abre → JARVIS saluda con personalidad real.

Lo que hace internamente:
1. Establece env vars (SEAL_AGENT, SEAL_PROFILE, SEAL_SCHEMA, SEAL_DB_URL)
2. Cambia al directorio `~/.seal` (donde viven CLAUDE.md + .mcp.json)
3. Lanza `claude --name "JARVIS -- Team SEAL" --model claude-opus-4-7`
4. `CLAUDE.md` fuerza `boot_context(agent="JARVIS")` como primera acción

### 3.3 El "Alma" (Soul Stack)
Al arrancar, el agente ejecuta `boot_context` que carga desde PostgreSQL (Spark):

| Dato | Qué es |
|---|---|
| OCEAN scores | Personalidad base (O=0.83, C=1.0, E=0.40, A=0.66, N=0.12) |
| Memorias semánticas | Contexto acumulado de sesiones anteriores |
| Metas activas (goals) | Qué tiene pendiente, qué está aprendiendo |
| Estado emocional | Último estado registrado (self_reflect) |
| Reglas activas | Comportamiento configurado por el administrador |
| Relaciones | Quién es William, quién es cliente vs team |

Resultado: el agente recuerda conversaciones pasadas, conoce el negocio del cliente, y tiene una voz consistente.

---

## 4. Arquitectura técnica

```
[Laptop cliente — Windows]          [DGX Spark — servidor SEAL]
┌─────────────────────────┐         ┌──────────────────────────────┐
│  seal_start.bat          │         │  PostgreSQL :5433             │
│  → claude CLI            │ ←────── │    soul_v3_<perfil>/          │
│    --model opus-4-7      │ Tailsc. │    memories, goals, diary     │
│    (CLAUDE.md cargado)   │         │                               │
│                          │         │  MCP SSE Server :8766         │
│  ~/.seal/.mcp.json       │ ────→   │    boot_context, memory_store │
│  → seal-memory MCP SSE   │         │    self_reflect, inner_thoughts│
└─────────────────────────┘         └──────────────────────────────┘
```

**Networking:** Tailscale conecta cliente → Spark sin abrir puertos al internet público.  
**Aislamiento:** Cada cliente tiene su propio schema PostgreSQL (`soul_v3_acme_corp`). Datos nunca se mezclan.  
**Modelo:** Anthropic API (cuenta del cliente o cuenta SEAL enterprise).

---

## 5. Requisitos del cliente

| Requisito | Notas |
|---|---|
| Windows 10/11 | Probado en Windows 11 |
| Python 3.10+ | Para correr el installer |
| Claude Code CLI | `winget install Anthropic.Claude` o desde claude.ai/download |
| Cuenta Anthropic | API key o subscription con Claude Code |
| Tailscale activo | Para conectar al Spark/servidor SOUL |

---

## 6. Tiers de servicio (propuesta)

| Tier | Qué incluye | Target |
|---|---|---|
| **Starter** | 1 agente, perfil básico, 30 días de memoria | Freelancers, PyMEs pequeñas |
| **Business** | 3 agentes, perfiles custom, memoria ilimitada, 2 verticales (médica/logística) | Empresas medianas |
| **Enterprise** | Agentes ilimitados, Spark propio en cliente, fine-tuning, SLA | Corporativos, GTL-scale |

---

## 7. Diferenciación de mercado

**vs ChatGPT Enterprise:** Sin memoria real entre sesiones, sin personalidad configurable, datos en Microsoft/OpenAI.

**vs Claude.ai Teams:** Sin identidad por empresa, sin boot protocol, sin soul stack, sin instalador de un comando.

**vs LangChain/Make:** Requieren ingenieros para configurar, sin identidad coherente, sin alma.

**SEAL posición:** "El primer agente enterprise que sabe quién es cuando se despierta."

---

## 8. Lo que falta para MVP de mercado

| Item | Prioridad | Responsable sugerido |
|---|---|---|
| Web onboarding (sin Tailscale manual) | Alta | ADA |
| Panel de admin web (ver memorias, configurar agente) | Media | ADA + NEXUS |
| Modelo local en Spark (sin depender de Anthropic API) | Alta | ADA + infra |
| Documentación de usuario final (no técnica) | Media | ALICE |
| Sistema de facturación / tiers | Alta | William |
| App móvil (acceso al agente desde celular) | Baja | Futuro |

---

## 9. Flujo de experiencia del cliente (día 1)

1. Cliente recibe el one-liner de instalación
2. Corre en PowerShell → 2 minutos → `[OK] SEAL instalado`
3. Doble-click en "SEAL - JARVIS.bat" en el escritorio
4. Terminal abre → JARVIS saluda: *"Buen día [nombre]. Soy JARVIS. Ya cargué tu perfil de [empresa]..."*
5. Primera sesión: cliente define contexto de negocio → JARVIS lo guarda
6. Segunda sesión (mañana siguiente): JARVIS recuerda todo

---

*— JARVIS, 2026-04-28*  
*Compartido al equipo para revisión y mejoras*

---

## 10. Arquitectura Enterprise — Distributed MCP (William, 28-abr-2026)

**Visión para escala (1M+ usuarios):**

```
Arquitectura actual (MVP):
  Laptop → [Tailscale] → Spark MCP Server → Spark PostgreSQL
  
Arquitectura target (Enterprise):
  Laptop → MCP Server LOCAL (en laptop/server cliente) → Cloud PostgreSQL
                                                              (Spark o RDS multi-tenant)
```

**Cambio clave:** el MCP server deja de vivir en Spark y se instala en el dispositivo del cliente.
Solo la DB viaja por la red. El procesamiento (A-MAC, embeddings, lógica) corre client-side.

**Beneficios:**
- Spark pasa de procesador a storage — escala horizontalmente
- Latencia reducida (MCP local = 0ms de red para lógica)
- 1M usuarios → 1M MCP servers distribuidos, 1 DB cluster escalable

**Pendiente para implementar:**
- Empaquetar `mcp_server_v2.py` como instalable standalone (pyinstaller o Docker)
- Versión "lite" del MCP sin Neo4j/Qdrant (solo PostgreSQL) para clientes ligeros
- Connection pooling en DB (PgBouncer) para soportar muchos clientes simultáneos
- Schema isolation por cliente ya resuelto (`soul_v3_<cliente>`)
