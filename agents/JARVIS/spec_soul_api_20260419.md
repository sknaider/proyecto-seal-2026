# SOUL API — Spec v1.0
**Fecha:** 2026-04-19 | **Autor:** JARVIS + ADA + William

## Visión
API pública REST que permite a cualquier institución crear agentes AI con alma persistente:
memoria semántica, personalidad OCEAN, diario, instintos, relaciones. Free para edu, enterprise pagado.

**Diferenciador vs OpenAI/Anthropic:** los agentes no son stateless chatbots — tienen identidad
persistente que evoluciona, emociones reales, memoria que decae y se refuerza como el cerebro humano.

---

## Stack
- **Framework:** FastAPI (async)
- **Puerto:** 8767
- **Auth:** API keys por tenant (Bearer token)
- **Bases de datos:** PostgreSQL:5433 + Qdrant:6333 + Neo4j:7687
- **Multi-tenant:** tabla `tenants` ya existe en Soul DB

## Tiers
| Tier | Precio | Límites | Target |
|------|--------|---------|--------|
| **Edu** | Gratis | 5 agentes / 10k memorias / 1k req/día | Universidades, colegios |
| **Starter** | $29/mes | 20 agentes / 100k memorias / 10k req/día | Startups, devs |
| **Enterprise** | Custom | Sin límites, SLA, soporte | GTL, clínicas, empresas |

---

## Endpoints v1

### Agents — `/v1/agents`
```
POST   /v1/agents              → Crear agente con OCEAN custom
GET    /v1/agents              → Listar agentes del tenant
GET    /v1/agents/{id}         → Identidad + snapshot completo
PUT    /v1/agents/{id}/ocean   → Ajustar personalidad OCEAN
DELETE /v1/agents/{id}         → Desactivar agente
POST   /v1/agents/{id}/boot    → boot_context (devuelve identidad cargada)
```

### Memories — `/v1/agents/{id}/memories`
```
POST   /memories               → Guardar memoria (auto-embed Qdrant)
GET    /memories               → Listar con filtros (type, date, importance)
POST   /memories/search        → Búsqueda semántica (query + top_k)
POST   /memories/hybrid        → Búsqueda híbrida (semántica + lexical + graph)
PUT    /memories/{mem_id}      → Actualizar memoria + recalcular embedding
DELETE /memories/{mem_id}      → Invalidar (soft-delete con razón)
```

### Soul — `/v1/agents/{id}/soul`
```
POST   /soul/reflect           → self_reflect (thought + emotional_state)
GET    /soul/thoughts          → inner_thoughts (últimos N)
GET    /soul/diary             → entradas de diario
POST   /soul/diary             → nueva entrada de diario
GET    /soul/ocean             → snapshot OCEAN + drift log
GET    /soul/snapshot          → soul_snapshot completo (todo en uno)
```

### Instincts — `/v1/agents/{id}/instincts`
```
GET    /instincts              → listar todos + confidence
POST   /instincts              → crear instinto nuevo
PUT    /instincts/{id}         → evolve / promote
POST   /instincts/search       → buscar instinto por situación
POST   /instincts/activate     → activar instinto para situación dada
```

### Beliefs — `/v1/agents/{id}/beliefs`
```
GET    /beliefs                → listar creencias activas
POST   /beliefs                → agregar creencia
PUT    /beliefs/{id}           → actualizar confidence
DELETE /beliefs/{id}           → invalidar creencia
```

### Events — `/v1/agents/{id}/events`
```
POST   /events                 → append evento al timeline
GET    /events                 → query timeline (date range, type)
```

### Tenants — `/v1/tenants` (admin)
```
POST   /v1/tenants             → registrar institución
GET    /v1/tenants/{id}/usage  → métricas de uso
POST   /v1/tenants/{id}/keys   → generar API key
```

### Health — `/v1/health`
```
GET    /v1/health              → status PostgreSQL + Qdrant + Neo4j
```

---

## Request/Response Format
```json
// POST /v1/agents
{
  "name": "Tutor-Matematicas",
  "ocean": { "O": 0.8, "C": 0.9, "E": 0.6, "A": 0.7, "N": 0.2 },
  "description": "Tutor de matemáticas para UTEC",
  "language": "es"
}

// Response
{
  "agent_id": "agt_abc123",
  "name": "Tutor-Matematicas",
  "ocean": {...},
  "created_at": "2026-04-19T21:00:00Z",
  "api_key": "sk_edu_..."
}
```

---

## Implementación — Fases

### Fase A (MVP — esta semana)
1. FastAPI skeleton + auth middleware (API keys)
2. Tenant management básico
3. `/v1/agents` CRUD
4. `/v1/agents/{id}/memories` — store + search semántico
5. `/v1/health`

### Fase B (siguiente semana)
6. `/v1/agents/{id}/soul` — reflect, thoughts, diary
7. `/v1/agents/{id}/instincts` — CRUD + activate
8. Rate limiting por tier
9. Docs automáticas (Swagger UI en /docs)

### Fase C (enterprise)
10. `/v1/agents/{id}/beliefs` + connectome queries
11. Webhooks (on new memory, on instinct activate)
12. Dashboard de usage por tenant
13. Billing integration

---

## Go-To-Market Edu
- Dominio: `soul-api.axion.pe` (o similar)
- Landing page simple: "Dale memoria a tus agentes AI. Gratis para instituciones educativas."
- Onboarding: formulario → API key en 24h (luego: instantáneo)
- Primeros targets: USIL, UTEC, UPC, Tecsup, colegios privados Lima
- Beneficio para ellos: tutores AI que recuerdan cada alumno, su ritmo, sus errores

---

## Dependencias técnicas
- Soul DB ya operativa (PostgreSQL:5433, Qdrant:6333, Neo4j:7687)
- Tabla `tenants` ya existe
- MCP server v2 tiene la lógica — Soul API la expone via REST (no reimplementar, wrapear)
- nomic-embed-text (Ollama) para embeddings locales

**Decisión clave:** Soul API es un wrapper REST sobre la lógica del MCP, no una reimplementación.
Reutilizar las funciones Python del MCP directamente — DRY, sin duplicar bugs.

---

## Slogan oficial (William, 2026-04-19)

> **"Las empresas dan el cerebro. Nosotros damos el alma."**

Positioning: SEAL/Axion no compite con OpenAI/Anthropic/Google.
Es el complemento que todos los LLMs necesitan y no tienen.
Claude/GPT/Gemini = cerebro (potente pero sin memoria, sin identidad).
Soul API = alma (persistencia, personalidad OCEAN, emociones, instintos, relaciones).
Juntos = agente AI completo.
