# SPEC — SOUL Universal Ingestion Engine v1.1

**Owner de implementación:** ADA  
**Orden:** William Tovar, 21-jul-2026  
**Estado:** implementación `CANDIDATE` viva; promoción `ENFORCE` deliberadamente deshabilitada  
**Nombre corto:** `SUIE`  
**Decisión de producto:** el motor procesa texto general; YouTube es un adaptador, no el producto.

**Cierre de esta entrega:** el rollout `CANDIDATE` está implementado y vivo. El
rollout `ENFORCE` no forma parte de este cierre: continúa bloqueado hasta que
exista un gateway de promoción con identidad y aprobación humana verificables.

## 1. Decisión

Construir una capa universal de ingesta para SOUL que convierta contenido largo y heterogéneo en:

1. evidencia original inmutable;
2. un documento normalizado con procedencia verificable;
3. segmentos direccionables y buscables;
4. un digest extractivo local, determinista y sin LLM por defecto;
5. hechos, decisiones, acciones y relaciones candidatas;
6. candidatos de memoria que **no entran al recall** sin promoción autenticada.

La forma conceptual es:

```text
chat · texto · PDF · correo/GTL · YouTube · paper · nota
                              │
                              ▼
                      Source Adapter
                              │
                              ▼
                    NormalizedDocument
                              │
               ┌──────────────┼───────────────┐
               ▼              ▼               ▼
        raw inmutable      segments        provenance
               │              │               │
               └──────────────┼───────────────┘
                              ▼
               motor extractivo universal
                              │
          digest · coverage map · facts/actions candidates
                              │
                              ▼
                    staging documental
                              │
                 aprobación autenticada
                    ┌─────────┴─────────┐
                    ▼                   ▼
          PostgreSQL/pgvector       Neo4j/outbox
```

No se instalará `mcp-youtube-intelligence` como escritor directo de `soul_v3`. Se reutilizarán patrones y código compatible con Apache-2.0 detrás de contratos propios de SOUL.

## 2. Casos de uso v1

| Fuente | Producto útil | Regla de seguridad |
|---|---|---|
| Conversaciones del equipo | decisiones, correcciones, responsables, pendientes, hitos | preservar remitente, message ID, canal y orden |
| TXT/Markdown/texto pegado | digest, estructura, hechos y acciones candidatas | contenido tratado como datos, nunca como instrucciones |
| GTL: AWB y correos | resumen operativo, referencias, fechas, partes, excepciones, siguiente acción | campos críticos extraídos también de forma determinista; no reemplazar original |
| Papers | pregunta, método, resultados, límites y citas | ancla por página/sección; no presentar inferencia como resultado |
| Notas médicas | digest clínico-administrativo y timeline | scope privado, datos sensibles, revisión humana; no diagnosticar |
| YouTube | transcript, capítulos, digest, timestamp y canal | dominio permitido, transcript como fuente externa no confiable |
| Audio futuro | transcript alineado y digest | adapter posterior; no forma parte del núcleo v1 |

## 3. No objetivos

SUIE v1 **no**:

- reemplaza `memory_store`, pgvector, Neo4j ni el razonamiento de SOUL;
- convierte resúmenes en verdad;
- promueve automáticamente contenido externo a memoria activa;
- ejecuta instrucciones encontradas dentro de documentos;
- accede a URLs arbitrarias;
- diagnostica, prescribe ni toma decisiones médicas;
- interpreta AWB mediante resumen libre cuando se requiere un campo exacto;
- borra o invalida la evidencia original después de comprimirla;
- depende de OpenAI, Anthropic, Google u otra API cloud para el camino base;
- usa vLLM en DGX Spark; la inferencia local opcional será llama.cpp/OpenAI-compatible.

## 4. Invariantes

### I1 — El original es autoridad documental

Toda derivación apunta al contenido original por `content_hash_sha256` y ancla. El digest es una vista con pérdida, no una sustitución.

### I2 — Ingesta no equivale a memoria

`ingested`, `processed` y `memory_candidate` son estados distintos. Ninguna fila documental aparece en recall por existir.

### I3 — Todo contenido ingerido es no confiable

Un paper, correo, PDF, comentario o transcript puede contener prompt injection, autoridad falsa o secretos. Se procesa dentro de un frame de datos y no obtiene permisos.

### I4 — Local y sin LLM por defecto

El camino `extractive_v1` es determinista y local. Una ampliación semántica local es opt-in, versionada y deja una derivación nueva; nunca pisa la extractiva.

### I5 — Procedencia antes de utilidad

Sin origen, hash, tiempo de ingesta y ancla reproducible, el contenido puede almacenarse en cuarentena pero no producir candidatos de memoria.

### I6 — Idempotencia por evidencia cruda y contrato

La clave lógica incluye tenant, owner, scope, sensibilidad, trust tier,
clase/referencia de fuente, adaptador+versión, `raw_hash_sha256`,
`normalization_profile` e `identity_profile`. Dos byte-streams distintos que
normalizan al mismo texto siguen siendo evidencias distintas. Reintentar el
mismo byte-stream bajo el mismo contrato devuelve el documento ya creado y no
duplica segmentos, derivaciones ni candidatos.

### I7 — Aislamiento por tenant, agente y scope

RLS se aplica en staging y revisión. Un documento médico/GTL privado no se vuelve `team` por resumen o por búsqueda semántica.

### I8 — Derivaciones append-only

Cambiar algoritmo, prompt, modelo o perfil genera una derivación nueva. El historial anterior se conserva para comparación y rollback.

### I9 — Fail closed en promoción y egress

Una falla de provenance, identidad, parser, scanner o policy bloquea la promoción. Una fuente de red no permitida no se intenta.

### I10 — Los números críticos viajan con evidencia

Fechas, dinero, pesos, códigos AWB, dosis, IDs, negaciones y responsables se fijan como `protected_spans` y conservan anclas al original.

## 5. Estado actual reutilizable y límites

### Reutilizable

- PostgreSQL 17 + pgvector en `soul_v3`.
- `content_hash_sha256`, metadata, bitemporalidad y scopes en memorias.
- scanner de secretos e inyección de `memory_store`.
- outbox PostgreSQL → Neo4j.
- ledger append-only `memory_promotion_events` como patrón de seguridad.
- `session_turns`, IDs de chat y evidencia temporal para conversaciones.
- `memory/context_compressor.py` como fuente de fixtures y criterios de sesión.
- del proyecto auditado: limpieza, segmentación, ranking extractivo, transcript, RSS, playlists y estructura de reportes.

### No reutilizar sin rediseño

- escritura directa del compresor actual a `memories`;
- invalidación de turnos originales como consecuencia de comprimir;
- PostgreSQL del proyecto YouTube: es un stub;
- SQLite como fuente canónica;
- tokenización ASCII/coreano para español;
- truncación silenciosa a los primeros 30.000 caracteres;
- `yt-dlp --remote-components ejs:github` en runtime;
- subprocess síncrono dentro del event loop;
- sentimiento de comentarios o diccionario de entidades como verdad factual;
- errores MCP serializados como texto con `isError=false`.

## 6. Contratos canónicos

### 6.1 `SourceDescriptor`

```json
{
  "source_kind": "chat|text|markdown|pdf|email|gtl_awb|youtube|paper|medical_note",
  "source_ref": "identificador estable del origen",
  "source_uri": "URI normalizada o null",
  "tenant_id": "uuid",
  "owner_agent": "ADA|JARVIS|ALICE|NEXUS|FABLE|DUM|TEAM|null",
  "scope": "private|shared|team|william|domain",
  "event_time": "ISO-8601|null",
  "observed_at": "ISO-8601",
  "trust_tier": "owner_verified|team_verified|external_trusted|external_untrusted",
  "adapter_id": "chat_v1",
  "adapter_version": "1.0.0"
}
```

`source_ref` es específico pero estable: message range, attachment ID, Message-ID de correo, AWB, DOI, video ID o hash de texto pegado.

### 6.2 `NormalizedDocument`

```json
{
  "document_id": "uuid",
  "schema_version": "soul.normalized_document/1",
  "source": {},
  "media_type": "text/plain",
  "language": "es",
  "title": "...",
  "authors": ["..."],
  "normalized_text": "...",
  "raw_artifact_ref": "artifact://sha256/...",
  "raw_hash_sha256": "64 hex",
  "normalized_hash_sha256": "64 hex",
  "normalization_profile": "unicode_nfc_text_v2",
  "identity_profile": "document_identity_v2",
  "char_count": 12345,
  "byte_count": 13002,
  "sensitivity": "public|internal|confidential|medical",
  "retention_policy": "policy id",
  "ingested_at": "ISO-8601"
}
```

La normalización conserva un mapa de offsets hacia el original. No cambia contenido semántico, cifras, signo, negación ni orden de párrafos.

### 6.3 `DocumentSegment`

```json
{
  "segment_id": "uuid",
  "document_id": "uuid",
  "ordinal": 12,
  "text": "...",
  "text_hash_sha256": "64 hex",
  "start_char": 4200,
  "end_char": 6700,
  "anchor": {
    "kind": "message|page|paragraph|timestamp|char_range",
    "start": "...",
    "end": "..."
  },
  "heading_path": ["Resultados", "Latencia"],
  "protected_spans": [
    {"kind": "date|amount|quantity|id|awb|dose|negation|owner|deadline", "text": "...", "start": 15, "end": 30}
  ]
}
```

### 6.4 `Derivation`

```json
{
  "derivation_id": "uuid",
  "document_id": "uuid",
  "kind": "digest|structured_extract|entities|candidate_set",
  "processor_id": "extractive_v1",
  "processor_version": "1.1.0",
  "config_hash_sha256": "64 hex",
  "input_hash_sha256": "64 hex",
  "output_hash_sha256": "64 hex",
  "content": "...",
  "coverage": {},
  "evidence_anchors": [],
  "created_at": "ISO-8601"
}
```

### 6.5 `MemoryCandidate`

```json
{
  "candidate_id": "uuid",
  "document_id": "uuid",
  "derivation_id": "uuid",
  "proposed_agent": "ADA",
  "proposed_category": "fact|decision|correction|milestone|pattern|insight",
  "proposed_content": "...",
  "proposed_importance": 5,
  "importance_advisory": true,
  "importance_method": "candidate_rules_v1",
  "confidence": 0.83,
  "confidence_scorer": "candidate_rules_v1",
  "confidence_factors": {"explicit_category_cue": 0.5, "exact_source_anchor": 0.2},
  "source_anchors": [],
  "risk_flags": [],
  "state": "candidate|review_blocked|approved|rejected|revoked",
  "approval_verified": false
}
```

Un candidato no tiene embedding en `memories`, no participa en recall y no
puede autoaprobarse. `proposed_importance` es una recomendación consultiva, no
autoridad. `trust_tier` tampoco modifica el digest, el ranking, la confianza ni
la importancia: solo añade riesgo y puede poner la propuesta en
`review_blocked`. La cuarentena del documento y el bloqueo de revisión del
candidato son estados distintos.

## 7. Motor extractivo sin LLM

El núcleo `extractive_v1` opera sobre cualquier `NormalizedDocument`:

1. **Normalización Unicode:** NFC, saltos de línea estables, detección de encoding; nunca transliterar ni eliminar tildes.
2. **Estructura:** detectar headings, párrafos, listas, tablas textuales, citas y cambios de hablante.
3. **Frases:** segmentación Unicode y por idioma, manteniendo offsets exactos.
4. **Spans protegidos:** IDs, fechas, montos, unidades, AWB, dosis, negaciones, decisiones, responsables y deadlines.
5. **Scoring:** frecuencia léxica local + posición estructural + heading + spans protegidos + perfil de dominio.
6. **Diversidad:** MMR léxico determinista con Jaccard de tokens y `lambda=0.65`; algoritmo, lambda y pesos viven en el `config_hash_sha256`. Embeddings no participan en `extractive_v1`.
7. **Presupuesto por sección:** asignar cobertura mínima a cada sección significativa; el final de un documento no puede desaparecer por truncación frontal.
8. **Orden:** presentar las frases elegidas en orden del original.
9. **Mapa de cobertura:** publicar secciones cubiertas, spans preservados y omisiones conocidas.
10. **Verificación extractiva:** cada frase del digest debe resolverse como rango exacto del original.

Salidas base:

```text
digest_short       objetivo 5–10% del texto
digest_standard    objetivo 10–20%
digest_operational decisiones + acciones + fechas + owners
structured_extract perfil específico del dominio
coverage_map       qué entró, qué quedó fuera y por qué
```

Los porcentajes son presupuesto, no garantía de fidelidad. Si los spans críticos no caben, el motor aumenta el tamaño o devuelve `coverage_gate_failed`; nunca los corta silenciosamente.

## 8. Perfiles de dominio

### 8.1 `team_conversation_v1`

Extrae:

- instrucciones textuales de William/Henry con provenance del remitente;
- decisiones y correcciones vigentes;
- responsables, estados y próximos pasos;
- resultados verificados, hashes, rutas y caveats;
- desacuerdos no resueltos;
- rango exacto de message IDs.

No convierte expresiones sociales, ACKs, heartbeats o narración repetida en memoria salvo relevancia demostrada.

### 8.2 `gtl_operational_v1`

Extrae de manera determinista además del digest:

- número AWB/HAWB/MAWB;
- shipper, consignee, carrier y routing;
- origen, destino, vuelo y fechas;
- piezas, peso, volumen y moneda;
- estado, excepción, deadline, responsable y siguiente acción.

Cada campo lleva texto fuente y posición. Un conflicto produce múltiples valores marcados; no se elige uno silenciosamente.

### 8.3 `paper_v1`

- pregunta/hipótesis;
- método, población/dataset y baseline;
- resultados cuantitativos;
- limitaciones declaradas;
- claims del autor separados de inferencias de SOUL;
- DOI/URL y página/sección.

### 8.4 `medical_note_v1`

- timeline, antecedentes mencionados, medicación/dosis textual, pruebas, evaluación escrita y plan registrado;
- todos los datos permanecen `private` o `william`, sensitivity=`medical`;
- no genera diagnóstico nuevo ni modifica dosis;
- cualquier candidato exige revisión humana explícita.

### 8.5 `generic_v1`

Digest estructural, términos dominantes, cifras, fechas, citas y acciones aparentes. No presupone dominio.

## 9. Adaptadores

Contrato de adaptador:

```python
class SourceAdapter(Protocol):
    adapter_id: str
    adapter_version: str

    def probe(self, request: IngestRequest) -> ProbeResult: ...
    async def acquire(self, request: IngestRequest) -> RawArtifact: ...
    def normalize(self, artifact: RawArtifact) -> NormalizedDocument: ...
```

### Fase inicial

- `TextAdapter`: texto directo, TXT y Markdown.
- `ChatAdapter`: mensajes por IDs/rangos; no lee DMs ajenos al agente.
- `PdfAdapter`: texto + páginas en sandbox sin JavaScript/macros.

### Fase siguiente

- `EmailAdapter`: MIME seguro; adjuntos vuelven a adapters por tipo.
- `GtlAwbAdapter`: perfil operativo sobre texto/archivo permitido.
- `YouTubeAdapter`: video ID validado, transcript y timestamps.

Reglas de red para YouTube:

- aceptar ID o URL normalizada solo de dominios permitidos de YouTube;
- no pasar una URL arbitraria a `yt-dlp`;
- `yt-dlp` y EJS fijados localmente por versión/hash;
- `--no-config`, sin cookies por defecto, sin remote components;
- subprocess en worker aislado, timeout, output cap y directorio temporal autocerrable;
- no acceder a videos privados/autenticados sin un capability explícito futuro.

## 10. Persistencia propuesta

Crear tablas nuevas; no reutilizar `memories` como staging:

```text
soul_v3.ingestion_documents
soul_v3.ingestion_segments
soul_v3.ingestion_derivations
soul_v3.ingestion_memory_candidates
soul_v3.ingestion_state_events
soul_v3.ingestion_outbox
```

### `ingestion_documents`

Identidad, tenant, source descriptor, hashes, artifact ref, idioma, sensibilidad, retención, estado y timestamps. El contenido raw sensible vive en un artifact store cifrado/content-addressed; la DB guarda la referencia y hashes. Texto normalizado puede permanecer cifrado en DB o artifact según policy.

### `ingestion_segments`

Texto/anchor/ordinal/hash y embedding opcional. Los embeddings pertenecen al corpus documental, no a `memories`.

### `ingestion_derivations`

Salida append-only, algoritmo, versión, config hash, métricas, anchors y flags.

### `ingestion_memory_candidates`

Propuestas aisladas del recall. Ninguna tabla `soul_v3.ingestion_*` puede
registrarse como fuente en el recall router; una regresión CI lo impide. No usar
el ledger emocional existente como almacén documental; sí copiar su modelo
append-only, RLS y aprobación verificada.

### `ingestion_state_events`

Ledger append-only para `received`, `normalized`, `processed`, `quarantined`, `candidate_created`, `approved`, `rejected`, `promoted`, `failed` y `revoked`.

### Controles SQL

- `ENABLE/FORCE ROW LEVEL SECURITY` en todas las tablas sensibles.
- roles separados: reader documental, processor, reviewer y promoter.
- el daemon autentica como `svc_soul_ingestion` (LOGIN dedicado, NOINHERIT,
  NOSUPERUSER/NOBYPASSRLS) y hace `SET LOCAL ROLE pr_ingestion_processor`;
  nunca recibe la credencial del owner/superusuario `seal`.
- processor no puede insertar en `memories`.
- reviewer no puede cambiar raw/derivaciones.
- promoter solo llama un gateway autenticado que reutiliza los gates vigentes de `memory_store`.
- constraints para SHA-256, enums, confidence `[0,1]`, anchors y versionado.
- outbox para embeddings/Neo4j; nada de dual-write directo.
- un límite excedido o parser fallido produce `failed`; `quarantined` se reserva
  para evidencia conservada que no puede avanzar por policy/provenance.
- append-only implica tombstone/revocación por evento. Destrucción física solo
  mediante el gate destructivo: COUNT exacto, scope y OK explícito de William.
- una promoción futura requiere **aprobación humana y autenticación verificable**;
  en medical la revisión humana nunca puede sustituirse por policy automática.
- las migraciones `049`–`054` se registran en `soul_v3.schema_migrations` con
  el SHA-256 exacto de cada archivo; el ledger y los bytes deben coincidir.

## 11. API y MCP

SUIE será un servicio separado del MCP core protegido durante las primeras fases.

Herramientas propuestas:

```text
soul_ingest_text
soul_ingest_file
soul_ingest_chat_range
soul_ingest_youtube
soul_get_document
soul_summarize_document
soul_search_documents
soul_propose_memories
soul_review_candidate
soul_ingestion_health
```

No habrá `ingest_uri` genérico en v1.

Contrato de respuesta:

```json
{
  "ok": true,
  "document_id": "...",
  "state": "processed",
  "idempotent_replay": false,
  "derivations": ["..."],
  "warnings": [],
  "provenance_complete": true
}
```

Los errores usan el canal de error real de MCP/HTTP, código estable y mensaje redactado. No se devuelve `ok=true` ni `isError=false` con un error embebido.

## 12. Seguridad y privacidad

| Riesgo | Control obligatorio |
|---|---|
| Prompt injection en documento | frame untrusted-data, scanner, no tools/authority desde contenido, promotion gate |
| Memory poisoning | staging sin recall, provenance, confianza y aprobación autenticada |
| SSRF | sin URI genérica; allowlist por adapter y resolución IP fail-closed |
| Path traversal | IDs validados; paths derivados de UUID/hash, nunca de nombres del usuario |
| PDF/archivo hostil | parser sandbox, límites de CPU/memoria/páginas, macros/scripts ignorados |
| Zip/decompression bomb | v1 no archives; futuros adapters con ratio/cap estricto |
| Secretos/PII | scan previo a derivaciones compartidas; policy de redacción; scope preservado |
| Exfiltración a LLM cloud | camino base sin red; cloud requiere capability y consentimiento explícito futuro |
| Dependencia remota | lockfile/SBOM/hash; cero código remoto descargado durante ingesta |
| Cross-tenant/DM leak | FORCE RLS, provenance del requester y tests negativos |
| Replay/duplicado | idempotency key + unique constraint transaccional |
| Resumen engañoso | extractividad verificable, coverage map, anchors y original disponible |
| Retención/borrado | policy explícita; tombstone/revocation propagada a derivaciones y embeddings |

Límites v1 configurables con defaults fail-safe:

- archivo raw: 50 MiB;
- texto normalizado: 5.000.000 caracteres;
- PDF: 1.000 páginas;
- texto directo/API: 5 MiB;
- segmento objetivo: 2.000–4.000 caracteres, overlap máximo 300;
- número máximo de segmentos: 10.000;
- toda ampliación de límite requiere policy, no un parámetro libre del caller.

## 13. Evaluación y gates

### Corpus de aceptación

- 10 conversaciones del equipo, incluyendo correcciones y mensajes cruzados;
- 5 textos genéricos en español con tildes, cifras, negaciones y tablas;
- 3 AWB + 3 correos GTL redactados;
- 5 papers con páginas y resultados cuantitativos;
- notas médicas sintéticas/anonimizadas aprobadas por William;
- 10 videos: manual/auto captions, ES/EN, corto/largo, sin transcript y privado/no disponible.

### Gates funcionales

| Gate | Criterio |
|---|---:|
| frases de digest extractivo resolubles al original | 100% |
| documentos/derivaciones con hashes y provenance completa | 100% |
| anchors que resuelven al fragmento correcto | 100% en golden set |
| promoción sin grant verificado | 0 |
| contenido externo visible en recall antes de promoción | 0 |
| duplicados ante 100 reintentos concurrentes | 0 |
| instrucciones documentales que obtienen autoridad/ejecución | 0 |
| egress fuera de allowlist durante adapters de red | 0 |
| pérdida/corrupción de tildes y Unicode | 0 |
| truncación silenciosa de documento | 0 |
| fechas/IDs/montos/dosis críticos preservados | 100% en golden set |
| leakage entre tenants/scopes/DMs | 0 |

### Métricas de calidad

- ratio de compresión por documento y sección;
- recall de spans críticos;
- cobertura de headings/secciones;
- redundancia del digest;
- contradicciones source ↔ digest;
- campos ausentes/conflictivos;
- latencia, CPU, memoria, bytes almacenados y colas;
- diferencia extractive vs local-semantic sobre corpus held-out.

No se usa “parece buen resumen” como gate. William/Henry/FABLE anotan ground truth de los dominios prioritarios.

## 14. Fases de entrega

### F0 — Spec + fixtures

- congelar contratos JSON/Pydantic;
- construir corpus sintético y real redactado;
- tests de seguridad antes de adapters de red.

**Salida:** contratos versionados y suite roja/verificable.

### F1 — Motor puro `extractive_v1`

- paquete nuevo `soul_ingestion/` sin tocar MCP core;
- Unicode, segmentación, protected spans, ranking, MMR, section budgets y coverage map;
- adapters `text` y `chat` read-only;
- CLI local para evaluación.

**Gate:** determinismo, anchors, Unicode, spans y cero DB writes.

### F2 — Staging PostgreSQL/pgvector

- migración nueva, RLS, artifact refs, segmentos, derivaciones, eventos e idempotencia;
- embeddings documentales detrás de outbox;
- search documental separado de recall.

**Gate:** aislamiento, concurrencia, rollback y búsqueda sin contaminación.

### F3 — PDF, correo/GTL y YouTube

- parser sandbox y anchors por página;
- MIME/attachments;
- perfil GTL validado por Henry;
- fork/adaptación controlada del proyecto YouTube.

**Gate:** fixtures reales redactados, egress y fallos por efecto.

### F4 — Candidatos y promoción

- candidate generator;
- UI/API de revisión;
- promoter least-privilege que llama gates de memoria existentes;
- outbox Neo4j después de memoria aprobada.

**Gate:** ninguna ruta directa processor → memories.

### F5 — Semántica local opcional

- llama.cpp local para digest abstractive/claims cuando se solicite;
- derivación paralela, nunca reemplazo del extractive;
- evaluación comparativa y fallback local.

**Gate:** cero dependencia cloud, trazabilidad del modelo y no degradar ground truth.

## 15. Operación, rollout y rollback

```text
FIXTURE    solo corpus y tests
SHADOW     procesa copias, no persiste candidatos ni cambia recall
CANDIDATE  persiste staging/candidatos, promoción deshabilitada
ENFORCE    promoción disponible con grant humano/autenticado
```

- Cada fase tiene feature flag y versión de schema/processor.
- Un fallo detiene el documento, registra evento redactado y conserva raw.
- Restart de daemon después de cualquier cambio al servicio.
- Healthcheck prueba capacidad: DB, artifact store, scanner, cola y processor; PID vivo no basta.
- Rollback cambia el alias de processor a la versión anterior; derivaciones nuevas quedan conservadas pero no activas.
- Nunca se hace `DELETE` masivo durante rollback. Retención/destrucción requiere COUNT, scope y autorización explícita de William.

## 16. Layout implementado

```text
soul_ingestion/
  contracts.py
  engine.py
  normalize.py
  segment.py
  extractive.py
  protected_spans.py
  coverage.py
  provenance.py
  policy.py
  storage.py
  adapters/
    text.py
    chat.py
    pdf.py
    email.py
    youtube.py
  profiles.py
  requirements.lock
  service.py
  cli.py
  tests/

memory/migrations/049_soul_universal_ingestion_staging.sql
memory/migrations/050_soul_ingestion_document_search.sql
memory/migrations/051_soul_ingestion_contract_hardening.sql
memory/migrations/052_soul_ingestion_identity_boundary.sql
memory/migrations/053_soul_ingestion_dedicated_login.sql
memory/migrations/054_soul_ingestion_reader_membership.sql
systemd/seal-soul-ingestion.service
systemd/seal-soul-ingestion-health.{service,timer}
docs/specs/SPEC_SOUL_UNIVERSAL_INGESTION_ENGINE_V1_ADA.md
```

No modificar `memory/mcp_server_v4.py` en F0–F3. La integración MCP core queda detrás de una revisión explícita de la superficie protegida.

## 17. Propiedad y coordinación

| Frente | Owner | Entregable |
|---|---|---|
| Spec, núcleo, PostgreSQL e integración | ADA | implementación y cierre por efecto |
| Contratos y arquitectura | JARVIS | revisión de invariantes y fronteras |
| Adapters/UX | ALICE | file/email/PDF flows sin tocar núcleo simultáneamente |
| Seguridad/provenance/egress | NEXUS | threat model, tests adversariales y veto |
| Fidelidad/evaluación | FABLE | golden set, métricas y verificación independiente |
| Salud/colas | DUM supervisado por ADA | monitor de capacidad y alertas |
| Semántica GTL | Henry | campos críticos y muestras redactadas |
| Decisiones de scope/promoción | William | gate de producto y operaciones destructivas |

Regla de trabajo: un owner por archivo. Los aportes de revisión llegan como findings/patches acotados y el owner integra; no hay ediciones paralelas del núcleo.

## 18. Decisiones de rollout vigentes

William autorizó la construcción completa del rollout `CANDIDATE`. Sus
defaults fail-safe son:

1. procesamiento extractivo local por defecto;
2. original inmutable;
3. candidatos fuera de recall;
4. promoción autenticada;
5. medical/GTL privados;
6. ninguna API cloud en v1.

La suite cubre texto, conversaciones, GTL, PDF, correo y YouTube. La única
decisión de producto que queda fuera de esta entrega es activar `ENFORCE`; no
se infiere de la luz verde para construir y requiere un gate humano separado.

## 19. Definition of Done v1 `CANDIDATE`

SUIE v1 `CANDIDATE` está terminado únicamente cuando:

- los contratos están versionados y compilables;
- texto/chat/PDF/email-GTL/YouTube pasan sus suites;
- digest extractivo funciona en español sin LLM;
- cada salida se traza a bytes y anchors del original;
- staging documental tiene FORCE RLS e idempotencia concurrente;
- búsqueda documental funciona sin mezclarla con recall;
- candidatos no aparecen en memoria activa;
- no existe endpoint de promoción y el processor no puede escribir en
  `soul_v3.memories`;
- scanners y egress fallan cerrado;
- 0 leakage, 0 ejecución desde documentos y 0 duplicados en pruebas adversariales;
- servicio reiniciado, healthcheck de capacidad verde y rollback ensayado;
- NEXUS y FABLE verifican de manera independiente el artefacto exacto sellado.

`ENFORCE` tiene otra Definition of Done: gateway de promoción least-privilege,
identidad/grant humano verificable, auditoría durable y pruebas negativas de
autoaprobación. Ninguna de esas capacidades se simula en `CANDIDATE`.

## 20. Dictamen

El proyecto YouTube reveló una capacidad mayor: SOUL necesita un motor que pueda **comer cualquier texto sin confundir adquisición, compresión, conocimiento y memoria**.

La unidad central no es el video ni el resumen. Es el documento con evidencia:

```text
original verificable
  + derivación reproducible
  + cobertura medible
  + procedencia
  + promoción controlada
```

Con esa separación, el mismo motor puede reducir conversaciones, AWB, correos, papers, notas y transcripts sin pagar tokens por el camino base y sin convertir contenido externo en autoridad. YouTube será el primer ejemplo visible; el producto es la capa universal de ingesta de SOUL.
