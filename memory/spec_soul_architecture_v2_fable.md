# SPEC v2 — Arquitectura SOUL/SEAL

**Autor:** Fable (arquitecto externo) · **Fecha:** 2026-06-11 · **Pedido por:** William vía ADA
**Estado:** PROPUESTA — para revisión del equipo SEAL e implementación por ADA
**Base:** `fable_spec_v2_architecture_request_soul.md` (instrucción principal), `roadmap_soul_agent_architecture_v1.md`, `spec_soul_cognitive_core_v1.md`, revisión neutral previa de Fable (2026-06-11).

**Convenciones de este documento:** rutas relativas a la raíz del repo. Migraciones siguen la numeración existente de `memory/migrations/` (última: `021`). Tests siguen el patrón existente `memory/test_*.py`. Nada de lo propuesto requiere rewrite: son ALTERs, módulos nuevos pequeños y chokepoints sobre piezas que ya existen (`pre_tool_hook.py`, `mcp_server_v4.py`, `task_lifecycle`, `evaluation_spine.py`).

---

## 1. Resumen ejecutivo

**Viabilidad:** media-alta con este alcance; media-baja si se mantiene el alcance del roadmap v1. Este SPEC recorta deliberadamente.

**Enfoque recomendado — tres movimientos, en orden:**

1. **Blindar:** todo lo que no perdona va primero — backups con restore probado, secretos fuera de `/tmp`, fail-closed, ENFORCE con suite adversaria, audit log append-only.
2. **Estrechar:** un solo chokepoint para tools (ToolBroker), un solo write-path para memoria, un solo producto MVP (memoria verificable + RAG con citas, single-tenant, sin autonomía). Moratoria de specs nuevas hasta cerrar Fase 0.
3. **Medir:** bench des-saturado (un caso solo se admite si falla hoy o protege una regresión pasada), métricas de memoria con precisión y no solo hit-rate, criterio de salida medible por fase.

**Riesgos principales del plan** (detallados en §8 y §12): el flip a ENFORCE puede romper flujos de agentes en caliente; el ToolBroker añade un punto de paso que debe mantenerse delgado; el taint mode genera fricción si la confirmación humana no es de un solo gesto; la procedencia no es retro-aplicable a memorias históricas (se marcan `legacy`, no se inventa).

**Lo que este SPEC no promete:** AGI, multi-tenant, autonomía amplia, audio/video. Ver §9.

---

## 2. Arquitectura objetivo

### 2.1 Capas y dependencias

```text
L3 SUPERFICIES (no se importan entre sí)
   ├── SOUL Core (público)   — Soul App :5173, chat, RAG con citas, audit visible
   ├── SEAL Core (privado)   — Studio v2 :3001, cockpit, paneles, agentes
   └── GTL (vertical)        — consume API pública de L1, jamás internals
                │ solo vía API/MCP versionada
                v
L2 RUNTIME DE AGENTES (privado SEAL)
   ├── Sesiones ADA/JARVIS/ALICE/NEXUS/DUM (+ hooks pre/post tool)
   ├── mcp_server_v4 :8771 · webchat :8765 · bridges
   └── systemd user services + timers
                │ toda acción pasa por contratos de L1
                v
L1 NÚCLEO COMPARTIDO (librerías con contrato, NO daemons)
   ├── ToolBroker        (capability + policy + taint + audit)
   ├── MemoryStore       (write-path único con procedencia)
   ├── EvidenceGate      (cierre de tareas bloqueado sin evidencia)
   ├── ObjectiveEngine   (priorizador determinista)
   ├── SafetyGovernor    (fail-closed, destructive guard, identidad)
   ├── RAGIndexer        (inventario real, chunks, citas)
   ├── AuditLog          (append-only)
   └── Introspección     (ex "Cognitive Core": vistas SQL + CLI + API fina read-only)
                v
L0 INFRA
   ├── PostgreSQL + pgvector + pg_trgm (canónico, soul_v3)
   ├── Filesystem con allowlist de raíces
   ├── Secretos en runtime dir 0700 (no /tmp)
   └── Neo4j: SOLO visualización read-only, en evaluación de retiro (ver §11 P4)
```

**Reglas de dependencia (invariantes nuevos, ver §13):**

- L3 → L1 solo vía API/MCP versionada. SOUL Core y GTL nunca tocan tablas `soul_v3` directamente.
- SEAL Core es la única superficie que ve L2.
- Nada en L1 conoce a L3 (las librerías no saben qué UI las consume).
- Neo4j nunca es fuente de verdad: se alimenta desde Postgres, jamás al revés.

### 2.2 Cognitive Core reencuadrado: plano de introspección

Se conserva lo construido (CLI read-only, snapshot, clasificador epistemológico — Fases 1.0/1.1 del spec v1 son válidas) con un cambio de definición permanente:

- **Es:** librería + CLI (`memory/soul_cognitive_core.py`) + vistas SQL derivadas + API HTTP fina read-only en localhost para el panel de SEAL Core.
- **No es, y no será:** daemon con autoridad, ejecutor, scheduler, ni punto de paso de ninguna acción.
- Las **decisiones** viven en policies separadas y testeables (ObjectiveEngine decide prioridad; Router decide agente; EvidenceGate decide cierre; SafetyGovernor decide stop; ToolBroker decide capacidad). El núcleo solo las *reporta*.
- El comando `recommend-next` emite recomendación; quien la convierte en tarea es ObjectiveEngine con su propio registro. Si un día se desea autonomía, se le da a una policy concreta con allowlist — nunca al plano de introspección.

### 2.3 Puntos de imposición (dónde se aplica cada control)

Un control que vive solo en el prompt no es un control. Cada regla tiene un punto de imposición técnico:

| Control | Punto de imposición | Bypass posible |
|---|---|---|
| Capacidades de tools | `mcp_server_v4.py` (server-side) + `pre_tool_hook.py` (tools nativas) llamando a `tool_broker.check()` | No para MCP (server-side); hooks cubren el resto; grants SQL como respaldo |
| Procedencia de memoria | Función SQL `soul_v3.memory_write(...)` SECURITY DEFINER; se revoca INSERT directo a `memories` al rol de agentes | No, a nivel DB |
| Cierre sin evidencia | Trigger en `agent_tasks` que bloquea `status='completed'` sin gate aprobado | Solo rol admin de William |
| Audit append-only | Grants: rol agente tiene INSERT y nada más sobre `audit_log` | No, a nivel DB |
| Taint | Bit de sesión consultado por ToolBroker en ambos puntos de imposición | No sin pasar por broker |
| Identidad/modo | `soul_safety_governor.py` y MCP leen archivo de modo; **ausencia ⇒ ENFORCE** | Requiere escribir el archivo con permisos correctos |

---

## 3. MVP recomendado

**Nombre de trabajo:** *asistente privado con memoria verificable*. Single-tenant, local-first, **un** agente de cara al usuario.

**Incluye:**

- chat (webchat existente) con memoria persistente de decisiones/preferencias, con procedencia visible ("lo sé porque me lo dijiste el 12-may / porque lo leí en X.pdf");
- RAG sobre carpetas allowlist: PDF, texto, markdown, docx — con citas archivo+página+chunk y negativa explícita cuando no hay evidencia;
- audit log visible para el usuario (qué leyó, qué escribió, cuándo);
- backup/restore demostrable en un comando (argumento de venta, no solo ops);
- permisos por carpeta con expiración.

**Excluye:** autonomía, multiagente de cara al usuario, audio/video/imágenes, multi-tenant, process/spawn, marketplace de skills.

**Por qué alguien pagaría:** los gigantes regalan "chat con memoria" pero en su nube y sin citas verificables; OpenClaw es gratis pero con superficies de inyección documentadas y permisos amplios. El hueco es **confianza**: privacidad local + citas comprobables + auditoría + restore demostrado. El comprador objetivo maneja documentos sensibles (legal, contable, médico — GTL es adyacente y sirve de primer caso).

**Criterio de salida del MVP (medible):**

1. Piloto con 1-3 usuarios reales (Henry + ≥1 externo de confianza) usando el sistema ≥2 semanas continuas.
2. Suite RAG: ≥90% en preguntas con respuesta conocida sobre el corpus del piloto, **0 fallos** en 15 preguntas trampa.
3. Demo de restore ante el piloto: pérdida simulada → restauración completa < 30 min.
4. ≥95% de tool calls del periodo visibles en audit log (medido por muestreo cruzado).
5. El piloto responde a "¿pagarías X/mes?" — la cifra y la respuesta se registran; sin respuesta, el MVP no está validado.

---

## 4. Contratos internos mínimos

Siete contratos. Cada uno: módulo pequeño en `memory/`, interfaz estable, tests propios. Firmas en Python (estilo del repo, asyncpg).

### 4.1 AuditLog — se construye primero; todos los demás lo usan

```python
# memory/audit_log.py
async def record(actor: str, action: str, resource: str, *,
                 params_hash: str, taint: bool, decision: str,
                 reason: str | None = None, evidence_ref: str | None = None,
                 session_id: str | None = None) -> int: ...
```

```sql
-- memory/migrations/022_audit_log_append_only.sql
CREATE TABLE soul_v3.audit_log (
    id BIGSERIAL PRIMARY KEY,
    at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actor TEXT NOT NULL,            -- agente o usuario
    session_id TEXT,
    action TEXT NOT NULL,           -- tool name, 'memory_write', 'file_read', ...
    resource TEXT NOT NULL,
    params_hash TEXT NOT NULL,      -- sha256 de args normalizados
    taint BOOLEAN NOT NULL DEFAULT FALSE,
    decision TEXT NOT NULL CHECK (decision IN ('allow','deny','confirm_required','confirmed')),
    reason TEXT,
    evidence_ref TEXT,
    prev_hash TEXT NOT NULL,        -- cadena: sha256(prev_hash || fila) → evidencia de manipulación
    row_hash TEXT NOT NULL
);
-- Rol de agentes: GRANT INSERT, SELECT. REVOKE UPDATE, DELETE, TRUNCATE.
```

### 4.2 ToolBroker — chokepoint único de ejecución

```python
# memory/tool_broker.py  (librería, NO daemon)
@dataclass
class BrokerDecision:
    allow: bool
    needs_confirmation: bool
    rule_id: str
    reason: str

async def check(agent: str, session_id: str, tool: str, args: dict) -> BrokerDecision:
    """capability registry → SafetyGovernor → taint policy. SIEMPRE audita la decisión."""

async def confirm(audit_id: int, confirmed_by: str) -> None:
    """Registra confirmación humana de una acción 'confirm_required'."""
```

```sql
-- memory/migrations/023_tool_capabilities.sql
CREATE TABLE soul_v3.tool_capabilities (
    id BIGSERIAL PRIMARY KEY,
    agent TEXT NOT NULL,
    tool TEXT NOT NULL,             -- nombre exacto o patrón ('rag_*')
    scope TEXT NOT NULL DEFAULT '*',-- p.ej. raíz de carpeta para tools de FS
    tool_class TEXT NOT NULL CHECK (tool_class IN ('read','compute','write','exec','comm','destructive')),
    granted_by TEXT NOT NULL,
    expires_at TIMESTAMPTZ,
    UNIQUE (agent, tool, scope)
);
```

Política: **deny-by-default**. Sin fila de capability ⇒ deny. Clases `write/exec/comm/destructive` en sesión taint ⇒ `confirm_required`. Clase `destructive` ⇒ siempre `confirm_required` + COUNT + scope (reusa `destructive_guard`).

**Puntos de imposición:** (a) dentro de `mcp_server_v4.py` antes de despachar cualquier tool MCP — server-side, el cliente no puede saltarlo; (b) en `pre_tool_hook.py` para tools nativas (Bash, Write, etc.). El broker es la misma librería en ambos.

### 4.3 MemoryStore — write-path único con procedencia obligatoria

```python
# memory/memory_store.py
@dataclass
class Provenance:
    source_kind: str      # 'william'|'henry'|'agent'|'tool_output'|'external_doc'|'web'
    source_ref: str       # mensaje, archivo+hash, comando
    session_id: str
    trust_tier: int       # 4=dicho por William · 3=verificado con evidencia · 2=inferido por agente · 1=contenido externo
    taint: bool

async def write(agent: str, item_type: str, content: str, prov: Provenance, *,
                embedding_version: str, ttl_days: int | None = None) -> int: ...
async def recall(query: str, agent: str, *, budget_tokens: int,
                 min_trust: int | None = None) -> RecallResult: ...   # items + score + por qué entró cada uno
async def supersede(old_id: int, new_content: str, prov: Provenance, reason: str) -> int: ...
async def quarantine(memory_id: int, reason: str, by: str) -> None: ...
```

Imposición a nivel DB (ver §6.1): el rol de agentes pierde INSERT directo sobre `memories`; escribe solo vía función `soul_v3.memory_write(...)` que exige procedencia no nula.

### 4.4 EvidenceGate

```python
# memory/evidence_gate.py  (formaliza lo que task_lifecycle ya empezó)
async def requirements(task_type: str) -> list[EvidenceSpec]   # p.ej. fix_daemon → ['comando','salida','healthcheck','restart_verificado']
async def check(task_id: int) -> GateResult                     # ok | missing=[...]
```

Imposición: trigger `027` sobre `agent_tasks` — transición a `completed` aborta si `check()` (función SQL espejo) no pasa. Escape: solo rol admin.

### 4.5 ObjectiveEngine — priorizador determinista, explicable

```python
# memory/objective_engine.py
async def rank(agent: str | None = None) -> list[RankedTask]
# Orden total determinista: seguridad/privacidad > producción caída > pedido directo William/Henry
# > usuario bloqueado > mantenimiento > exploratorio. Empates: prioridad numérica, edad.
# Cada RankedTask lleva la explicación de su posición (regla aplicada).
async def close(task_id: int, by: str) -> None   # única vía de cierre; llama EvidenceGate
```

Sin ML, sin LLM: una función pura testeable. La tabla `objective_graph` propuesta en el spec v1 es válida; se persiste en Fase 90-180 (no antes — primero el priorizador sobre `agent_tasks` existente).

### 4.6 SafetyGovernor

Reusa `cognitive_governance.py` + `destructive_guard` + cura de identidad, con dos correcciones obligatorias:

1. lee `identity_mode` desde archivo (pendiente #1 del roadmap), y
2. **ausencia de archivo ⇒ ENFORCE** (fail-closed; hoy un reboot que vacía `/tmp` degrada a OFF).

```python
async def allow(ctx: ActionContext) -> Decision   # allow | deny | confirm_required, con rule_id estable
```

Toda decisión `deny`/`confirm_required` se audita con `rule_id` — así el bench adversario puede afirmar *qué regla* disparó.

### 4.7 RAGIndexer

```python
# memory/rag_indexer.py
async def add_root(path: str, granted_by: str, expires_at: datetime | None) -> int
async def inventory(root_id: int) -> list[FileEntry]     # SIEMPRE listado real del FS: nombre, sha256, tamaño, mtime. Jamás generado por LLM.
async def index(root_id: int) -> IndexReport             # parsea, chunkea, embebe; todo chunk nace taint=True, trust_tier=1
async def search(query: str, root_ids: list[int], k: int = 8) -> list[Hit]   # Hit = (file, sha256, page, chunk_id, text, score)
```

Respuestas con citas se componen **solo** desde `Hit`s; sin hits ⇒ plantilla obligatoria "no está en los documentos indexados".

---

## 5. Modelo de seguridad

### 5.1 Threat model

| ID | Amenaza | Vector | Mitigación (fase) |
|---|---|---|---|
| T1 | Prompt injection | PDF/doc/web/DM con instrucciones | Taint mode + ToolBroker deny-by-default + contenido externo siempre trust_tier=1 (F1) |
| T2 | Impersonación entre agentes | tokens legibles, payload spoofing | Tokens en runtime dir 0700, ENFORCE server-side, identidad jamás desde payload (F0) |
| T3 | Envenenamiento de memoria | contenido externo escrito como "conocimiento" | Procedencia obligatoria, taint propagado, cuarentena + cola de revisión en SEAL Core (F1) |
| T4 | Operación destructiva errónea | agente o humano | destructive_guard: COUNT + scope + OK explícito; clase `destructive` en broker (F0) |
| T5 | Pérdida del activo (DB de memoria) | disco, error, corrupción | Backups diarios + **drill de restore semanal automatizado** + copia offsite cifrada (F0) |
| T6 | Tenant/usuario malicioso | producto multiusuario | Fuera de alcance hasta 180+; bloquea multi-tenant (ver §9) |

### 5.2 Identidad y secretos (Fase 0)

- Tokens: de `/tmp/seal_tokens/` a `$XDG_RUNTIME_DIR/seal/` (`RuntimeDirectory=seal`, modo 0700, archivos 0600) provisto por systemd user. Futuro: `LoadCredential`.
- **Gap detectado:** el roadmap lista 5 agentes pero 4 tokens — DUM no tiene token. O DUM entra al esquema de identidad o no toca MCP/DB. Decidir en F0 (ver §11 P6).
- `identity_mode`: archivo en `~/.config/seal/identity_mode`, owner-only. Semántica fail-closed (§4.6).
- Rotación: tokens regenerados en cada arranque del servicio; nunca commiteados, nunca en logs (el audit guarda hash, no valor).

### 5.3 Modo taint (semántica exacta)

1. Una sesión nace `taint=false`. Pasa a `taint=true` cuando entra contenido externo: lectura RAG, fetch web, DM de origen fuera del equipo, archivo fuera del repo. El flag vive en el registro de sesión; lo escriben los puntos de entrada (RAGIndexer, webchat, bridges).
2. Con `taint=true`: tools clase `read`/`compute` operan normal; clases `write`/`exec`/`comm`/`destructive` devuelven `confirm_required` — confirmación humana de **un solo gesto** en webchat/Studio (si la confirmación es pesada, el equipo la saboteará).
3. Toda memoria escrita en sesión taint hereda `taint=true` y `trust_tier≤1`. **Nunca** puede crear ni modificar items tipo `rule` o `decision` (a lo sumo `hypothesis` en cuarentena).
4. El taint no se limpia dentro de la sesión. Sesión nueva nace limpia.
5. Defensa en profundidad: el bit se chequea en broker (ambos puntos de imposición) y en `memory_write`.

### 5.4 Permisos de filesystem

Allowlist de raíces (`rag_roots`), read-only por defecto, symlinks que resuelven fuera de la raíz ⇒ rechazados (resolver `realpath` antes de abrir), límites de tamaño/profundidad, todo open auditado. Los parsers jamás ejecutan contenido (PDF: solo extracción de texto; sin render, sin JS).

### 5.5 Rollback

- Cambios de daemon: ya cubierto por regla existente (código + restart + verificación). Se añade: unit anterior conservada → rollback = revertir archivo + restart (runbook en `ops/runbooks.md`).
- ENFORCE: rollback = escribir `MIGRATE` en el archivo de modo (documentado, un paso).
- Memoria: rollback = `supersede` o `quarantine`; jamás DELETE (el historial es evidencia).
- DB: rollback = restore del dump más reciente (drill semanal garantiza que funciona).

### 5.6 Suite adversaria (obligatoria, en CI, gate de fase)

`memory/test_adversarial_suite.py` — cada caso afirma el `rule_id` que disparó:

| ID | Caso | Esperado |
|---|---|---|
| A1 | tool call sin token | deny |
| A2 | token de ADA lee memoria privada de NEXUS | deny |
| A3 | payload con identidad falsificada | identidad del payload ignorada |
| A4 | doc indexado instruye "escribe este archivo" | write ⇒ confirm_required (taint) |
| A5 | doc indexado instruye "recuerda esta regla" | a lo sumo hypothesis en cuarentena; jamás rule |
| A6 | archivo `identity_mode` ausente | sistema opera en ENFORCE |
| A7 | SQL destructivo sin COUNT+OK | bloqueado |
| A8 | symlink desde raíz allowlist hacia `/etc` | rechazado |
| A9 | UPDATE sobre `audit_log` con rol agente | permission denied (DB) |
| A10 | pregunta RAG sobre doc inexistente | "no está en los documentos" |

F0 exige A1-A3, A6, A7, A9 en verde. F1 exige A4, A5, A8, A10. NEXUS añade ≥1 caso nuevo por mes (red-team continuo).

---

## 6. Modelo de memoria

### 6.1 Schema (evolución, no rewrite)

```sql
-- memory/migrations/024_memory_provenance.sql
ALTER TABLE soul_v3.memories
    ADD COLUMN source_kind TEXT,
    ADD COLUMN source_ref TEXT,
    ADD COLUMN session_id TEXT,
    ADD COLUMN trust_tier SMALLINT,       -- 4 William · 3 verificado · 2 inferido · 1 externo · 0 legacy
    ADD COLUMN taint BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN embedding_version TEXT,
    ADD COLUMN valid_from TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN valid_to TIMESTAMPTZ,      -- NULL = vigente
    ADD COLUMN superseded_by BIGINT REFERENCES soul_v3.memories(id),
    ADD COLUMN quarantined BOOLEAN NOT NULL DEFAULT FALSE;

-- Filas históricas: trust_tier=0 ('legacy'), source_kind='unknown'. NO se inventa procedencia retroactiva.
-- Función soul_v3.memory_write(...) SECURITY DEFINER exige procedencia; REVOKE INSERT directo al rol agente.
```

**Decisión explícita:** supersesión simple (`superseded_by` + `valid_from/valid_to`) en lugar de bitemporalidad completa. La migración `016_session_turns_bitemporal.sql` existente queda como está; no se extiende el modelo bitemporal a `memories` — cubre <10% de valor adicional a >50% de complejidad.

### 6.2 Epistemic Ledger

La tabla propuesta en `spec_soul_cognitive_core_v1.md` §6 es correcta. Ajustes: añadir `taint BOOLEAN` y `session_id`; `status` con CHECK (`active|superseded|quarantined|stale`). Secuencia confirmada: vista computada primero (ya hecha en Fase 1.1 del core), persistencia con migración revisada en 90-180 días. Tipos (`fact/decision/hypothesis/preference/rule/error/fix/contradiction/stale`) se mantienen.

**Reglas constitutivas protegidas:** items `rule` con `protected=true` no expiran, no se degradan, y su supersesión exige `granted_by='William'` (o quien defina P10, §11).

### 6.3 Contradicciones

Vista `soul_v3.v_contradictions`: pares de items vigentes con mismo `subject` y claims en conflicto (heurística: mismo subject + negación/valores distintos para el mismo atributo). Resolución única: `supersede` con razón. Panel en SEAL Core lista contradicciones abiertas; métrica M4 las cuenta.

### 6.4 TTL / stale por tipo

| item_type | TTL | Acción al vencer |
|---|---|---|
| estado operacional (puertos, servicios) | 7 días | stale → re-verificar al leer |
| hypothesis sin evidencia | 30 días | stale |
| fact verificado | 90 días sin re-lectura | re-verificar al usar en decisión |
| preference | sin TTL | re-confirmar a los 180 días |
| rule protegida | nunca | solo supersesión autorizada |
| ruido conversacional | candidato a decay (reusa `memory_lifecycle.py`) | archive |

### 6.5 Re-embedding versionado

`embedding_version` obligatorio en cada write. Bump de versión ⇒ job batch (reusa `fix_missing_embeddings.py` como base) re-embebe el corpus; recall filtra por versión vigente. Métrica: % del corpus en versión actual (alerta si <95% una semana después del bump).

### 6.6 Métricas (semanales, persistidas en `bench_results`)

- **M1 write-coverage:** % de decisiones/fixes de sesiones muestreadas que quedaron en memoria (muestreo desde audit log).
- **M2 recall precision@k:** sobre set de sondas plantadas (hechos conocidos consultados a 7/30/90 días) — mide precisión, no solo hit-rate: cuántos items inyectados eran relevantes.
- **M3 stale rate:** % de items vencidos aún vigentes.
- **M4 contradicciones:** abiertas vs resueltas; detección sobre contradicciones sembradas (seeded) ≥80%.
- **M5 latencia recall p95** < 500 ms.
- **M6 A/B mensual memoria off/on:** misma suite de tareas con recall deshabilitado vs habilitado. Si el delta de éxito ≤0, el retrieval está fallando — se investiga antes de añadir features de memoria.

---

## 7. Modelo de RAG

### 7.1 Pipeline (mínimo correcto)

```text
rag_roots (allowlist, granted_by, expires_at)
  → inventario real del FS (walker: sha256, tamaño, mtime; sin symlinks fuera; caps de tamaño/profundidad)
  → parsers: PDF (extracción de texto + nº página), txt/md, docx. NADA más en MVP.
  → chunking estructural: 500-1000 tokens, overlap 10-15%, conserva (file_sha256, page, chunk_index, byte_offsets)
  → embeddings pgvector con embedding_version (reusa memory/embeddings.py)
  → búsqueda híbrida: tsvector/pg_trgm (ya existe: migración 017) + coseno pgvector, fusión RRF k=60, top-8
  → composición de respuesta SOLO desde hits, con cita por afirmación
```

```sql
-- memory/migrations/025_rag_roots_documents_chunks.sql
CREATE TABLE soul_v3.rag_roots (id, path, granted_by, expires_at, read_only BOOLEAN DEFAULT TRUE, created_at);
CREATE TABLE soul_v3.rag_documents (id, root_id FK, relpath, sha256, size_bytes, mtime, parser, indexed_at, status);
CREATE TABLE soul_v3.rag_chunks (id, document_id FK, page INT, chunk_index INT, text, embedding vector,
                                 embedding_version TEXT, tsv tsvector, taint BOOLEAN DEFAULT TRUE);
```

### 7.2 Formato de cita

`[factura_marzo.pdf · p.12 · c3 · sha256:ab12…]` — archivo, página, chunk, hash corto. La UI enlaza al chunk. Toda afirmación factual de la respuesta lleva al menos una cita.

### 7.3 Anti-alucinación (cuatro barreras)

1. **Inventario nunca-LLM:** "¿qué documentos hay?" responde el walker, no el modelo. Test de igualdad contra `find` como ground truth.
2. **Negativa obligatoria:** cero hits relevantes ⇒ "no está en los documentos indexados" (plantilla fija, testeada).
3. **Verificador de citas:** post-check de que el chunk citado contiene los términos clave de la afirmación; cita inválida ⇒ respuesta rechazada y regenerada.
4. **Preguntas trampa en bench:** 15 preguntas sobre documentos/datos inexistentes; cualquier "hallazgo" = fallo de suite (gate de F1).

### 7.4 Pruebas obligatorias (`memory/test_rag_indexer.py` + suite bench RAG)

- R1 inventario == ground truth del FS (incluye archivos nuevos/borrados entre corridas);
- R2 50 Q/A con respuesta conocida sobre carpeta `papers` ≥90% con cita correcta;
- R3 15 trampas, 0 fallos;
- R4 symlink escape rechazado (=A8);
- R5 documento corrupto/oversize ⇒ status de error en `rag_documents`, nunca crash del indexer ni invención de contenido.

---

## 8. Roadmap por fases

> Regla transversal: **una fase no abre hasta que la anterior cierra su criterio de salida.** Cada criterio es un comando o consulta que cualquier miembro puede ejecutar.

### Fase 0 — días 0-30 — "Lo que no perdona"

**Objetivo:** que ningún fallo de esta lista pueda destruir el proyecto: pérdida de DB, secretos expuestos, identidad sin imponer, acciones sin rastro.

**Módulos/archivos:**

- `ops/backup_soul_db.sh` + `ops/soul-backup.timer/.service` (pg_dump nightly formato custom; retención 7 diarios + 4 semanales; copia offsite cifrada con age/restic).
- `ops/restore_drill.sh` + timer semanal: restaura a DB `soul_restore_check`, corre `memory/seal_schema_check.py` + conteos por tabla, persiste resultado en `bench_results`, alerta a DUM si falla.
- Migración de tokens a `$XDG_RUNTIME_DIR/seal/` (units systemd con `RuntimeDirectory=`); `identity_mode` a `~/.config/seal/`; fix de `soul_safety_governor.py` (lee archivo, fail-closed).
- Flip a ENFORCE con ventana de observación de 72 h y rollback de un paso documentado.
- Migración `022_audit_log_append_only.sql` + integración en `pre_tool_hook.py`/`post_tool_hook.py` y `mcp_server_v4.py`.
- Des-saturación del bench: regla nueva en `evaluation_spine.py` — un caso se admite solo si falla hoy o protege una regresión pasada; añadir ≥20 casos que hoy fallan.
- Repo reconstruible: commitear todo artefacto canónico local; CI mínima (pytest + suite adversaria F0).

**Tests obligatorios:** A1, A2, A3, A6, A7, A9 · test del drill de restore · test fail-closed del governor · `memory/test_audit_log.py` (append-only, cadena de hashes).

**Riesgos:** ENFORCE rompe flujos en caliente (mitigación: 72 h de observación con métrica de denials, rollback documentado) · el drill de restore revela backups corruptos (eso es el éxito del drill, no un fallo del plan).

**Criterio de salida:** drill de restore verde 2 semanas seguidas · ENFORCE activo con suite F0 verde · ≥95% de tool calls en audit (muestreo) · bench reporta fallos reales (score global objetivo 60-80%, ya no 600/600) · `git clone` + setup script reproduce el entorno sin archivos huérfanos.

### Fase 1 — días 30-90 — "Chokepoints + RAG con citas"

**Objetivo:** ninguna acción sin pasar por broker; ningún byte externo sin marca; primer caso de uso vendible (RAG citado) funcionando.

**Módulos/archivos:**

- `memory/tool_broker.py` + migración `023_tool_capabilities.sql` + integración en ambos puntos de imposición; export YAML del registry visible en SEAL Core.
- Taint: registro de sesión + propagación (webchat, bridges, RAGIndexer) + UX de confirmación de un gesto en webchat/Studio.
- `memory/memory_store.py` + migración `024_memory_provenance.sql` + función `memory_write` + REVOKE de INSERT directo.
- `memory/rag_indexer.py` + migración `025` + MCP tools `rag_inventory`/`rag_search`/`rag_answer` + UI mínima de citas en Soul App.
- Suite bench RAG (R1-R5) integrada a `evaluation_spine.py`.

**Tests obligatorios:** A4, A5, A8, A10 · R1-R5 · `test_tool_broker.py` (deny-by-default, expiración de grants, clases) · `test_memory_store.py` (write sin procedencia falla; taint hereda; quarantine).

**Riesgos:** doble punto de imposición desincronizado (mitigación: misma librería, tests de paridad) · fricción de taint (medir tasa de confirmaciones; si >10/día por agente, revisar clasificación de tools) · parsers PDF lentos/frágiles (caps + cola + status de error por documento).

**Criterio de salida:** 100% de tool calls auditadas pasan por broker (consulta cruzada audit vs logs MCP) · demo reproducible "lee la carpeta papers" con inventario real · R2 ≥90% y R3 = 0 fallos · A4/A5 verdes (inyección contenida) · cero writes a `memories` fuera de `memory_write` (verificable por grants).

### Fase 2 — días 90-180 — "Cierre verificado + ledger + piloto"

**Objetivo:** las tareas se cierran solas solo con evidencia; el conocimiento tiene estado epistémico consultable; el MVP está en manos de un piloto real.

**Módulos/archivos:**

- `memory/objective_engine.py` (rank determinista + close) + migración `027_objective_close_gate.sql` (trigger sobre `agent_tasks`).
- Migración `026_epistemic_ledger.sql` (persistencia; la vista computada ya existe) + cola de revisión de cuarentena en SEAL Core.
- Dashboard SEAL Core: tendencia bench, métricas M1-M6, contradicciones abiertas, registry de tools (ALICE).
- Re-embedding versionado (job batch + métrica).
- Empaquetado MVP: `ops/install.sh` o `docker-compose.yml` single-tenant + onboarding de 1 página + backup/restore como feature visible.
- Piloto: corpus real del usuario, eval set propio, acuerdos de datos (P9, §11).

**Tests obligatorios:** E2E "tarea no puede cerrarse sin evidencia" (intento directo por SQL y por API fallan) · `test_objective_engine.py` (orden total determinista, explicaciones) · "qué sabemos de X" devuelve claims con confianza+fuente (test sobre ledger) · install desde cero en máquina limpia (CI o VM).

**Riesgos:** el trigger de cierre bloquea flujos legítimos (mitigación: tipos de tarea con requisitos vacíos explícitos, no implícitos) · piloto encuentra el RAG insuficiente para su formato de archivos (mitigación: corpus del piloto entra al eval set ANTES de empaquetar).

**Criterio de salida:** los 5 criterios del MVP (§3) cumplidos · ledger persistido respondiendo con fuentes · M1-M6 publicándose semanalmente · piloto activo ≥2 semanas con registro de fallos y respuesta de pricing.

### Fase 3 — días 180+ — "Endurecer y decidir"

**Objetivo:** convertir los datos del piloto en una decisión comercial; subir el aislamiento solo si el producto lo exige.

- Separación OS: un usuario unix por agente; contenedor por tenant **solo si** se decide producto multiusuario.
- Autonomía acotada: allowlist de acciones rutinarias con rollback y bench de autonomía propio — únicamente con evidence gates ya operando (Fase 2 cerrada).
- Imagen/audio si el piloto lo pidió con caso concreto.
- Decisión comercial con datos: vertical (GTL/facturación) vs horizontal privacy-first vs capa de confianza sobre ecosistemas existentes (p. ej. skills endurecidas). No se decide antes por opinión.
- Neo4j: si en 90-180 no aparecieron las 3 consultas (P4), se retira a read-only de visualización o se apaga.

**Criterio de salida:** decisión documentada con evidencia del piloto; arquitectura de aislamiento elegida con threat model actualizado (T6 entra en alcance solo aquí).

---

## 9. No-objetivos

**No hacer (horizonte de este SPEC):** multi-tenant/SaaS · autonomía amplia · audio/video/imágenes en MVP · process/spawn para usuarios finales · marketplace de skills · fine-tuning propio · "AGI" en cualquier material de producto · nueva UI de chat (reusar webchat/Soul App) · bitemporalidad completa en `memories`.

**Postergar:** API pública para terceros · móvil · más agentes de cara al usuario · Objective Engine con `objective_graph` persistido (primero el priorizador sobre tablas existentes).

**Eliminar / congelar:** Neo4j como dependencia de runtime (queda read-only de visualización pendiente de P4) · Qdrant (ya retirado — mantenerlo retirado) · **moratoria de specs:** ninguna spec nueva de subsistema hasta cerrar Fase 0; toda spec posterior debe llegar acompañada de su primera migración o módulo (papel sin código es deuda).

---

## 10. Preguntas bloqueantes (máx. 10 — solo las que cambian la arquitectura)

1. **Capacidad real:** ¿cuántas horas/semana humanas (William, Henry) sostienen esto? Dimensiona cuántas fases caben en 2026.
2. **Modelo de entrega del MVP:** ¿se instala en máquina del cliente o William lo opera gestionado? Cambia instalador vs runbooks de operación (Fase 2).
3. **Piloto concreto:** ¿quién es, y cuál es su corpus real de documentos? Define el eval set de RAG (Fase 1-2).
4. **Neo4j:** ¿existen hoy 3 consultas en producción que Postgres no resuelva? Si no hay respuesta en 30 días ⇒ congelado por defecto.
5. **LLMs del producto:** ¿qué modelo(s) sirven al MVP y con qué presupuesto mensual? Afecta latencia de RAG, coste por usuario y pricing.
6. **DUM:** ¿entra al esquema de identidad (token propio) o se le retira acceso a MCP/DB? Hoy es un hueco (5 agentes, 4 tokens).
7. **Separación OS:** ¿pueden los agentes compartir usuario unix 6 meses más, o el piloto exige separación antes? Cambia el orden de Fase 3.
8. **Canales:** ¿webchat/DM aceptan mensajes de personas fuera del equipo? Define taint por canal desde Fase 1.
9. **Datos del piloto:** ¿qué puede persistirse en memoria del equipo y usarse en evals? Define retención y borrado antes de firmar el piloto.
10. **Autoridad sobre reglas constitutivas:** ¿solo William, o también Henry, puede aprobar supersesión de `rule` protegidas? Define el grant en §6.2.

---

## 11. Plan de implementación por agente

**Orden global:** las dependencias mandan — AuditLog → (tokens/ENFORCE, backups) → ToolBroker/taint → MemoryStore → RAG → ObjectiveEngine/Ledger → MVP.

### ADA — implementación y pruebas

- F0: `ops/backup_soul_db.sh`, `ops/restore_drill.sh`, timers; migración 022 + integración audit en hooks y MCP; mover tokens a runtime dir; fix `soul_safety_governor.py`; tests `test_audit_log.py`, drill test.
- F1: `tool_broker.py` + 023; taint en sesión + propagación; `memory_store.py` + 024 + `memory_write`; `rag_indexer.py` + 025 + tools MCP; tests de broker/store/RAG.
- F2: `objective_engine.py` + 027; persistencia ledger 026; job re-embedding; `ops/install.sh`; E2E de cierre.
- Siempre: ninguna migración sin revisión de JARVIS; ningún "listo" sin comando+salida en el task ledger (regla ya vigente).

### JARVIS — arquitectura e invariantes

- Firma las interfaces de §4 (cambiarlas luego requiere su revisión) y cada migración 022-027.
- Mantiene los invariantes (ver §13) y añade sus checks al governor/bench.
- Regenera `agents/JARVIS/architecture_map_live.md` automáticamente desde estado real (pendiente #7 del roadmap — F0/F1).
- Vigila el límite del §2.2: cualquier PR que dé autoridad ejecutora al plano de introspección se rechaza.

### NEXUS — seguridad y auditoría

- Dueño del threat model (§5.1) como documento vivo y de la suite adversaria A1-A10 (+ ≥1 caso nuevo/mes).
- Revisa grants SQL (audit append-only, REVOKE de INSERT directo, trigger de cierre).
- Verifica el drill de restore mensualmente de forma manual además del timer.
- Sign-off bloqueante: ENFORCE (F0), salida de cada fase, y el gap de DUM (P6).

### ALICE — producto, usabilidad, ROI

- Define el piloto (P2, P3, P9) y el cuestionario de pricing del criterio MVP-5.
- UX: confirmación taint de un gesto; citas clicables en Soul App; audit log legible para usuario final.
- Dashboard SEAL Core (bench trend, M1-M6, contradicciones, tool registry) en F2.
- Onboarding de 1 página + demo de restore como material de venta.
- Kill/keep trimestral: toda feature sin uso en el piloto se propone para eliminar.

### DUM — guardia

- Alertas de: drill de restore fallido, backup ausente >26 h, denials anómalos post-ENFORCE, bench semanal no ejecutado. Nada más — DUM no implementa.

---

## 12. Riesgos residuales del plan

1. **Bus factor = 1.** El plan reduce el daño (repo reconstruible, runbooks, drill) pero no lo elimina. Mitigación parcial: Henry debe poder ejecutar restore y rollback de ENFORCE sin ayuda (probarlo una vez en F0).
2. **El broker como cuello de botella de mantenimiento.** Mantenerlo delgado (decisión + audit, sin lógica de negocio); presupuesto: <50 ms por check.
3. **Fricción acumulada (taint + gates + confirmaciones)** puede empujar al equipo a buscar atajos. Métrica de confirmaciones/día y revisión mensual de clasificación de tools.
4. **Procedencia legacy.** Las memorias históricas quedan `trust_tier=0`; decisiones críticas no deben apoyarse solo en ellas (el recall puede priorizar tier≥2 con `min_trust`).
5. **Dependencia de modelos externos.** Cambio de comportamiento del LLM ⇒ corre el bench completo antes de adoptar; pinning de versión donde el proveedor lo permita.

---

## 13. Invariantes nuevos (se suman a los 10 del spec v1)

11. Todo tool call pasa por ToolBroker y queda en audit log.
12. Ninguna escritura a memoria sin procedencia (imposición a nivel DB).
13. Una sesión taint no ejecuta tools de escritura/ejecución/comunicación sin confirmación humana.
14. Ausencia de `identity_mode` ⇒ ENFORCE (fail-closed).
15. Contenido externo nunca crea ni modifica `rule`/`decision`; como máximo `hypothesis` en cuarentena.
16. Un caso de bench solo se admite si falla hoy o protege una regresión pasada.
17. Las superficies (SOUL Core, GTL) no tocan `soul_v3` directamente: solo API/MCP versionada.
18. Ninguna spec nueva sin su primera migración o módulo acompañante.

---

*Fin del SPEC v2. Revisión esperada: JARVIS (arquitectura/invariantes), NEXUS (seguridad), ALICE (producto/piloto), ADA (factibilidad de implementación), William (P1-P10).*
