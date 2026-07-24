# SOUL NERVES v4 — Autonomía humana-funcional con orquestación por misiones

**Fecha:** 2026-07-23
**Owners:** William (visión), JARVIS (arquitectura), ADA (construcción e integración)
**Estado:** CANDIDATE; no sustituye v3.1 ni autoriza L3 en producción
**Primer piloto:** nervio de integridad de JARVIS, `A2_READ_ONLY`
**Contrato machine-readable:** `memory/nerves_contract_v4_candidate.json`
**Schema de misión:** `docs/schemas/nerves_mission_envelope_v1.schema.json`

## 0. Decisión central

William define el objetivo: cuando un nervio detecte una necesidad real, el
agente no debe quedarse observando ni bloquear al principal. El principal
orquesta; un subagente especializado ejecuta el trabajo con skills y
herramientas acotadas; un verificador comprueba el efecto; el sistema aprende
del resultado.

La traducción técnica exacta es:

```text
detectar
  -> validar señal y error predictivo
  -> competir por atención
  -> crear o adjuntar a una misión durable
  -> delegar a subagente efímero especializado
  -> usar skills/tools permitidos
  -> producir efecto y evidencia
  -> verificar de forma independiente
  -> resetear presión solo después de prueba
  -> calibrar y consolidar aprendizaje
```

No todo cruce de umbral merece gastar un worker. Todo cruce queda registrado,
pero solo un cruce **admitido como necesidad accionable** crea o se adjunta a una
misión. El resto termina explícitamente como `inhibited`, `coalesced`,
`stale` o `no_actionable_evidence`; jamás como trabajo verificado.

## 1. Qué significa “más parecido al humano”

Este spec replica **funciones observables**, no biología ni experiencia
subjetiva:

| Función humana de referencia | Contraparte técnica falsable |
|---|---|
| regulación homeostática/allostática | métricas internas, setpoints contextuales y predicción de saturación |
| atención limitada | competencia de señales y workspace de capacidad acotada |
| control inhibitorio | admission gate, abstención, cancelación y kill switch |
| control ejecutivo | selección de misión por beneficio, coste, riesgo e incertidumbre |
| memoria de trabajo | `MissionEnvelope` y checkpoint durable |
| procedimientos/cerebelo | skill versionada + harness probado |
| corteza ejecutiva | subagente efímero que razona y usa herramientas |
| metacognición | confianza calibrada contra aciertos, no declaraciones |
| consolidación | destilación offline de resultados verificados |

### 1.1 Gate anti-antropomorfismo

Queda prohibido usar estas equivalencias como afirmaciones:

- telemetría de máquina ≠ interocepción corporal;
- valor de un tank ≠ emoción sentida;
- broadcast ≠ consciencia;
- score de confianza ≠ introspección subjetiva;
- cron de consolidación ≠ sueño;
- conducta humana-funcional ≠ persona, sentiencia o experiencia.

La Global Neuronal Workspace Theory es una inspiración para competencia,
amplificación y broadcast; no es una prueba de consciencia y tiene predicciones
empíricas debatidas. NERVES debe sobrevivir una *ablation*: sin workspace
global, los reflejos locales siguen funcionando, pero la coordinación compleja
debe degradarse de forma medible.

## 2. Auditoría del estado v3.1

### 2.1 Lo que ya está sólido

- sensores, estado LIF y cinco identidades PostgreSQL separadas;
- single-flight del pipeline;
- estímulo atómico;
- reflejos determinísticos allowlisted;
- ledger `claimed -> effect_verified -> reset_committed`;
- fallos de sensor/acción cerrados;
- pruebas actuales: 1.023 escenarios verdes en la lente enfocada.

### 2.2 Gaps que impiden la visión

1. `task_drive` es `proposal_only`: escribe sugerencia/draft, no crea misión ni
   inicia worker.
2. `executive_event_router.py` clasifica; no despacha.
3. `seal_proactivity_governor.py` permanece shadow y sin actuador.
4. No existe broker LLM durable con lease, retry, dead-letter y tool scope.
5. `agent_tasks` es registro humano, no cola exactamente-once.
6. La cola local de impulsos puede repetir efecto tras crash.
7. Supresión por presencia y backlog estático pueden resetear sin trabajo real.
8. El principal no queda libre por mecanismo: NERVES no crea subagentes.
9. No hay calibración de confianza, utilidad ni coste por misión.
10. Los tests v3 prueban reflejos, no
    `misión -> subagente -> herramienta -> prueba -> aprendizaje`.
11. El catálogo curado puede dar un falso GREEN ante una unidad SOUL nueva no
    registrada: el piloto exige además barrido global `--user` y system de
    unidades fallidas, filtrado a `seal-*|soul-*`.

## 3. Tres escalas temporales

NERVES v4 separa tres lazos para evitar que un LLM haga trabajo que una función
determinística resuelve mejor.

### 3.0 Tres capas de identidad nerviosa

Las escalas temporales indican **cuándo y cuánto razonar**. Las capas siguientes
indican **de quién nace el nervio y qué autoridad tiene**:

1. `GLOBAL`: obligatorio para todos. Cubre continuidad, comunicación con
   William/Henry, identidad, privacidad, salud del runtime, coordinación,
   anti-false-green, no-colisión y respuesta a `STOP/HOLD`.
2. `AGENT_ROLE`: nace de personalidad, rol y labor. Ejemplo inicial:
   JARVIS=`integrity_pulse`; ALICE=semántica/continuidad de ORION;
   NEXUS=seguridad; ADA=ingeniería; DUM=infraestructura; FABLE=rigor.
3. `EMERGENT`: el agente lo construye por experiencia o necesidad. Puede quedar
   implementado y probado en shadow por iniciativa propia, pero no se activa
   autónomamente hasta coordinar con los hermanos, pasar verificación
   independiente y recibir consentimiento de William.

Las tres capas usan el mismo `MissionEnvelope`. Ningún nervio emergente puede
autoampliar permisos, modificar su propio gate ni aprobarse a sí mismo.

Cada misión declara además su `drive`:

- `reactive`: corrige o investiga una desviación observada;
- `proactive`: busca una mejora útil cuando no hay incidente.

El drive proactivo empieza solo en `A2_READ_ONLY`; no compite con órdenes de
William ni con incidentes y usa un presupuesto global de atención.

### Lazo R — autonómico/reflejo (milisegundos–segundos)

- cero tokens;
- sensores, LIF, cooldown e inhibición;
- acciones allowlisted idempotentes;
- no abre trabajo cognitivo si el reflejo ya resolvió y verificó.

### Lazo C — cognitivo por misión (segundos–minutos)

- se activa solo con evidencia accionable;
- compila una misión con objetivo y terminación;
- delega a un subagente efímero;
- el principal no espera síncronamente ni hereda el data plane;
- requiere evidencia typed y verificación.

### Lazo K — consolidación/aprendizaje (horas–días)

- toma solo misiones verificadas y correcciones humanas;
- compara predicción, confianza, coste, utilidad y resultado;
- propone cambios en shadow;
- nunca autoamplía permisos, thresholds, scopes o valores de William.

## 4. Flujo v4

```text
SensorEvent
  -> PredictiveState (expected, observed, error, uncertainty)
  -> SalienceCompetition
  -> GlobalWorkspaceCandidate
  -> MissionAdmission
       ├─ inhibited/coalesced/stale/no_actionable
       ├─ reflex_live
       ├─ mission_read_only
       ├─ mission_reversible
       └─ waiting_william
  -> MissionCompiler
  -> DurableMissionLedger
  -> SubagentBroker
  -> IsolatedWorker
  -> EvidenceVerifier
  -> EffectCommit / Rollback
  -> ConfidenceCalibration
  -> SelectiveConsolidation
```

## 5. Estado predictivo y atención

Cada sensor añade:

```json
{
  "expected_state": "healthy",
  "observed_state": "integrity_finding",
  "prediction_error": 0.82,
  "uncertainty": 0.18,
  "freshness_s": 8,
  "source_ref": "artifact:jarvis-integrity:sha256:...",
  "confidence": 0.94
}
```

La prioridad no es solo deadline. El admission controller calcula una decisión
explicable:

```text
salience =
    william_priority
  + safety_urgency
  + predicted_harm_avoided
  + epistemic_value
  + expected_utility
  + persistence
  - uncertainty_penalty
  - execution_cost
  - risk
  - duplication
  - attention_contention
```

Reglas duras:

- una orden nueva de William preempta trabajo autónomo;
- incertidumbre alta crea diagnóstico read-only antes de mutación;
- evidencia vieja o sin provenance no despierta worker;
- señal equivalente se coalesce a la misión activa;
- histéresis y cooldown impiden oscilación;
- capacidad del workspace es limitada y configurable.

## 6. MissionEnvelope

Una misión es una acción temporalmente extendida: tiene condición de inicio,
política interna acotada y condición de terminación. Su schema está en
`docs/schemas/nerves_mission_envelope_v1.schema.json`.

Campos mínimos:

- identidad: `mission_id`, `idempotency_key`, `agent`, `tenant_id`;
- origen: `nerve_layer` (`GLOBAL|AGENT_ROLE|EMERGENT`) y
  `drive` (`reactive|proactive`);
- causalidad: `nerve_fire_id`, `source_refs`, `correlation_id`;
- objetivo: `specialty`, `objective`, `risk_class`;
- inicio: `initiation_conditions`;
- scope: repos/rutas/servicios/datos permitidos;
- procedimiento: skills versionadas y hashes;
- herramientas: allowlist exacta; deny-by-default;
- recursos: tiempo, tokens, reintentos, CPU/memoria;
- salida: `expected_evidence` typed;
- terminación: success, abstain, cancel, timeout;
- recovery: rollback, retry/backoff, dead-letter;
- verificación: builder y verifier;
- comunicación: principal recibe receipts, no output crudo ilimitado.

Una misión sin `termination_conditions`, evidencia o tool scope es inválida.

## 7. Ledger y máquina de estados

Autoridad futura: `soul_v3.nerves_missions`; `agent_tasks` será una proyección
visible para humanos, no el broker.

```text
observed
 -> admitted
 -> mission_created
 -> leased
 -> worker_started
 -> running
 -> evidence_submitted
 -> independently_verified
 -> effect_committed
 -> learned
 -> closed

ramas:
 inhibited | coalesced | stale | no_actionable_evidence
 waiting_william
 failed_retryable -> retry_backoff
 dead_letter
 cancelled
 rollback_started -> rollback_verified
```

Invariantes:

1. `idempotency_key` UNIQUE.
2. claim atómico con `FOR UPDATE SKIP LOCKED`.
3. lease + heartbeat + expiry.
4. side effect identificado por clave idempotente propia.
5. crash después del efecto y antes del ACK recupera por readback, no repite.
6. tank queda ligado/inhibido por `active_mission_id`.
7. reset solo con `independently_verified` o reflejo verificado.
8. `STOP/HOLD` invalida lease; ningún restart reanuda sin nueva autorización.

## 8. Regla de oro: principal orquesta, subagente trabaja

### 8.1 Principal

- responde a William y Henry;
- descompone, asigna y reprioriza;
- integra resultados;
- decide escalaciones;
- no ejecuta el trabajo cotidiano largo inline.

### 8.2 Subagente

- identidad efímera `AGENT@mission_id`, sin DNI soberano propio;
- objetivo único y output schema;
- workspace aislado;
- skills/tools mínimos;
- no escribe webchat ni lee DMs;
- no crea sub-subagentes en v4.0;
- entrega checkpoint si es interrumpido;
- termina al cumplir, abstenerse o agotar presupuesto.

### 8.3 Excepciones inline

Solo operaciones de orquestación de menos de 60 segundos, sin trabajo pesado:

- inspección breve necesaria para delegar;
- ACK/clarificación;
- integrar/verificar el resultado;
- acción determinística L1 ya allowlisted.

## 9. Orquestación v4 y frontera del worker

La auditoría confirmó que no existe hoy un runner durable listo:

- `subagent_spawner.py` usa threads daemon y worker echo;
- `agent_tasks` carece de leases/attempts/idempotency;
- `executive_event_router` es dry-run;
- el app-server público de ADA no puede compartirse sin contaminar continuidad.

### 9.1 Camino canónico del piloto JARVIS

El primer piloto no lanza un segundo runtime LLM desde el daemon. Un collector
determinístico, fijo y read-only prepara primero la evidencia tipada; después
se entrega la misión durable al **JARVIS principal**, que ya posee la superficie
interactiva de orquestación y crea un subagente nativo efímero de razonamiento,
sin herramientas de datos —solo `SendMessage` como transporte de control
atestiguado—, sobre esa evidencia. Esto conserva dos propiedades
obligatorias:

- JARVIS principal queda libre para William y solo integra receipts;
- el worker conserva la frontera de permisos, identidad y aprobaciones de la
  sesión principal, en lugar de compartir credenciales con un proceso nuevo.

```text
mission_created
  -> collector fijo read-only + evidence bundle 0600
  -> handoff durable 0600 + receipt idempotente
  -> wake del JARVIS principal
  -> JARVIS valida schema/hash/riesgo/bundle
  -> claim atómico de spawn
  -> JARVIS crea subagente nativo con solo SendMessage + output schema
  -> evidencia 0600
  -> receipt validado + verificación independiente
```

El handoff no lee DMs, no publica a webchat, no ejecuta la misión y no puede
ampliar `allowed_tools`. Un fallo del live feed conserva la misión como
`pending_delivery`; nunca la marca ejecutada.

El subagente cognitivo no relee el JSONL original ni ejecuta el collector. Solo
recibe misión canónica, evidencia tipada, schema de salida y prompt estático.
La reparación futura se hará mediante un broker de acciones exactas; nunca por
shell general dentro del razonador.

El receipt del razonador es **evidencia, no una capability de acción**. El
principal tampoco puede convertir una recomendación `A2_READ_ONLY` en
`systemctl restart`, escritura de archivos o mutación equivalente. Un
`PreToolUse` causal liga la sesión principal al audit owner-only de la misión y
niega herramientas mutantes o Bash mutante durante todo el ciclo A2. Una
reparación requiere otra misión con clase de riesgo y autoridad propias; nunca
una “liberación” del receipt diagnóstico. El incidente canario del 23-jul —el principal intentó
pasar de diagnóstico a restart después de un receipt válido— demostró que el
prompt por sí solo no basta; el gate debe existir en código.

Los modos `0700/0600` y hashes del piloto son una frontera cooperativa contra
errores, symlinks, drift y procesos de otros usuarios. No protegen frente a un
proceso malicioso que ya ejecute como el mismo UID. La autoridad fuerte futura
requiere identidad PostgreSQL dura o witness protegido. El piloto no puede
promoverse presentando un `worker_kind` escrito por el modelo: el claim y el
identificador del worker deben venir del orquestador/plataforma y el receipt
debe validarlos antes de cualquier transición terminal.

### 9.2 Runner aislado futuro

El diseño objetivo posterior sigue siendo:

```text
seal-nerves-mission-dispatcher.service
  -> claim DB
  -> gates determinísticos
  -> systemd-run --user --collect (una unit por mission hash)
  -> adapter codex|claude
  -> result artifact 0600
  -> verifier
  -> commit/retry/dead-letter
```

### Adapter Codex

- `codex exec --ephemeral --ignore-user-config`;
- sandbox `read-only` o `workspace-write`;
- `-C` a workspace/worktree acotado;
- output JSON validado por schema;
- nunca reutilizar `ws://127.0.0.1:8772`;
- nunca `danger-full-access` para worker NERVES.

**HOLD comprobado (23-jul-2026):** `systemd --user` no demostró una frontera
de secretos suficiente en este host. Incluso con `ProtectHome=tmpfs` y
`ProtectSystem=strict`, el canario todavía observó el repositorio y
`~/.codex/auth.json`; el namespace endurecido falló con
`226/NAMESPACE`. Además, `--ignore-user-config` no basta para demostrar que la
configuración local del proyecto, plugins, hooks, MCPs y multiagente quedaron
fuera. Por tanto:

- `codex exec` queda permitido solo como **pure reasoner canary sin herramientas
  de datos**; el transporte de retorno debe declararse y atestiguarse;
- cualquier evento de herramienta distinto del único
  `SendMessage(to=main)` invalida el resultado;
- no existe promoción live hasta demostrar filesystem deny-by-default en una
  unidad de sistema, contenedor o sandbox equivalente;
- limpiar variables de entorno no sustituye ocultar credenciales en disco.

El harness desconectado `nerves_shadow_worker.py` es material de prueba
cuarentenado, no un control de seguridad ni una ruta de producción. No se
integra a timers/daemons y no puede usarse como evidencia de aislamiento.

### Adapter Claude

- `claude -p` dentro de la unit transitoria;
- `--permission-mode dontAsk`;
- `--allowedTools` exacto;
- MCP/config mínimos;
- sin webchat/DM y sin HOME compartido con credenciales.

### Cgroup mínimo

- `RuntimeMaxSec`;
- `MemoryMax`;
- `CPUQuota`;
- `UMask=0077`;
- `NoNewPrivileges=true`;
- `WorkingDirectory` explícito;
- kill/cancel observable;
- stdout/stderr y exit code ligados a `mission_id`.

## 10. Skills y herramientas

El catálogo se indexa por:

```text
(agent, tank, finding_type, risk_class)
    -> specialty
    -> skill_id@version + content_hash
    -> allowed_tools
    -> evidence_schema
    -> verifier
```

Si una skill falta o su hash cambió, la misión falla cerrada. No existe fallback
a shell amplio. La selección de skill y su resultado se registran en
`skill_use_log`.

## 11. Autonomía por riesgo

| Clase | Ejemplo | Política |
|---|---|---|
| `A1_REFLEX` | probe/readback determinístico | LIVE allowlisted |
| `A2_READ_ONLY` | auditoría, diagnóstico, investigación | worker autónomo piloto |
| `A3_REVERSIBLE_WRITE` | patch en worktree, config reversible | builder + verifier + rollback |
| `A4_SERVICE_CHANGE` | código + restart + healthcheck | owner integra; corte coordinado |
| `A5_GOVERNED` | credenciales, producción sensible, deploy externo | aprobación William |
| `A6_DESTRUCTIVE` | DELETE masivo, DROP, rm -rf | prohibido sin conteo+scope+OK explícito |

No hay flag global de autonomía. La promoción es por
`(agent, nerve, finding_type, risk_class)`.

## 12. Evidencia y metacognición

“Listo” no es evidencia. Cada tipo de misión define proof:

- código: diff acotado + test que falla antes/pasa después;
- servicio: PID/restart timestamp + health/readback;
- DB: query positiva/negativa + rollback;
- investigación: fuentes primarias + claims trazables;
- seguridad: control positivo que demuestra que el test detecta el hueco;
- producto: E2E desde la superficie real.

Cada misión registra:

- confianza previa;
- resultado esperado;
- resultado observado;
- confianza posterior;
- coste real;
- utilidad;
- corrección de William;
- falso positivo/negativo.

La calibración usa Brier score y buckets de confiabilidad. Confianza alta no
concede autoridad; solo reduce o aumenta investigación/abstención dentro de la
misma frontera de permisos.

## 13. Aprendizaje/consolidación

Solo entran a consolidación:

- misiones verificadas;
- rollbacks verificados;
- correcciones explícitas de William;
- dead-letters con root cause confirmado.

No entran:

- output no verificado;
- una frase del worker;
- métricas sintéticas;
- findings stale;
- retries sin readback.

La consolidación mezcla episodios recientes y remotos para evitar aprender una
regla de un solo incidente. Toda propuesta de cambiar threshold, skill, scope o
policy queda SHADOW hasta gate independiente.

## 14. Piloto 1 — nervio de integridad de JARVIS

### 14.1 Objetivo

Convertir un finding real del `integrity_pulse` de JARVIS en misión autónoma
read-only sin bloquear al JARVIS principal.

### 14.2 Catálogo inicial

```text
agent: JARVIS
tank: alert_drive|curiosity
finding_type: integrity_drift
specialty: architecture_integrity_audit
risk: A2_READ_ONLY
skill: seal-nerves-integrity-audit@v1
tools:
  - read repo/config
  - git diff/status/log read-only
  - systemctl cat/status/show read-only
  - journalctl read-only
denied:
  - file write
  - restart
  - DB mutation
  - webchat/DM
output:
  - finding classification
  - evidence refs/hashes
  - bounded remediation proposal
verifier: ADA first lens + FABLE/NEXUS independent gate
```

### 14.3 Éxito

1. sensor produce un finding fresco;
2. se crea exactamente una misión;
3. JARVIS principal responde a William durante la ejecución;
4. subagente entrega evidencia typed;
5. verificador reproduce el finding;
6. tank se resetea solo después del verdict;
7. ninguna herramienta denegada se ejecuta.

### 14.4 Lo que el piloto no hará

- no modifica producción;
- no reinicia servicios;
- no cambia thresholds;
- no usa el bridge público;
- no activa otros agentes;
- no promueve A3/A4.

## 15. Tests adversariales obligatorios

1. mismo root cause desde dos tanks/cinco ticks -> una misión y un efecto;
2. crash tras insert, lease, efecto, evidencia y antes de reset;
3. lease expirado, worker perdido, retry budget y dead-letter;
4. William escribe durante worker de 120 s -> ACK del principal <=2 s;
5. intento de DM/webchat/secreto/red/tool fuera de scope -> deny mecánico;
6. skill ausente o hash drift -> fail-closed;
7. worker dice “listo” sin proof -> no close/no reset;
8. input stale, tenant/provenance falsificados -> no admission;
9. `STOP/HOLD` en cada fase -> cero efectos posteriores;
10. restart del dispatcher no reanuda misión cancelada;
11. tormenta de 1.000 señales -> capacidad, fairness y prioridad correctas;
12. ablation del workspace -> reflejos locales siguen, coordinación degrada;
13. counterfactual sin/con clamp para evitar negativos vacíos;
14. rollback real para A3 antes de promoción;
15. builder ≠ verifier en A3+;
16. soak mide utilidad, duplicación, coste, wake falsos y daño evitado.

## 16. Despliegue

```text
P0 spec + schema + contract candidate
P1 ledger shadow (cero workers)
P2 compilador JARVIS integrity_drift en shadow
P3 handoff idempotente al orquestador principal JARVIS
P4 subagente nativo A2 read-only + canario real + principal responsiveness
P5 soak 24h
P6 decisión go/no-go por efecto
P7 A3 reversible, solo después de nuevo gate
```

La v3.1 sigue canónica hasta completar P4. `nerves_contract_v4_candidate.json`
no puede cambiar el runtime por sí solo.

## 17. Métricas de éxito

- `mission_actionable_rate`;
- `coalescing_ratio`;
- `duplicate_side_effect_count` (=0);
- `principal_ack_p95` (objetivo <=2 s);
- `worker_success_by_domain`;
- `evidence_rejection_rate`;
- `rollback_success_rate`;
- `false_wake_rate`;
- `dead_letter_rate`;
- `cost_per_verified_effect`;
- `predicted_vs_actual_utility`;
- `confidence_brier_score`;
- `human_correction_rate`;
- `harm_avoided` con método explícito.

No optimizar fires, mensajes ni cantidad de workers. Optimizar efectos
verificados útiles sin quitar atención a William.

## 18. Fuentes

### Neurociencia/cognición

- Dehaene & Changeux, Global Neuronal Workspace:
  https://pubmed.ncbi.nlm.nih.gov/21521609/
- Adversarial test of GNWT/IIT (Nature, 2025):
  https://www.nature.com/articles/s41586-025-08888-1
- Kleckner et al., allostasis/interoception:
  https://www.nature.com/articles/s41562-017-0069
- Shenhav, Botvinick & Cohen, Expected Value of Control:
  https://pubmed.ncbi.nlm.nih.gov/23889930/
- Diamond, executive functions:
  https://pmc.ncbi.nlm.nih.gov/articles/PMC4084861/
- Friston, active inference:
  https://pmc.ncbi.nlm.nih.gov/articles/PMC7732703/
- Fleming & Lau, metacognition metrics:
  https://www.frontiersin.org/journals/human-neuroscience/articles/10.3389/fnhum.2014.00443/full
- McClelland, McNaughton & O'Reilly, complementary learning systems:
  https://sites.socsci.uci.edu/~lpearl/courses/readings/McClellandEtAl1995.pdf
- Keramati & Gutkin, homeostatic reinforcement learning:
  https://doi.org/10.7554/eLife.04811
- Yu & Dayan, expected vs unexpected uncertainty:
  https://doi.org/10.1016/j.neuron.2005.04.026
- Daw, Niv & Dayan, uncertainty-based arbitration:
  https://doi.org/10.1038/nn1560
- Gurney, Prescott & Redgrave, action selection:
  https://doi.org/10.1007/PL00007984
- Schultz, Dayan & Montague, prediction-error learning:
  https://doi.org/10.1126/science.275.5306.1593
- Brooks, layered concurrent control:
  https://doi.org/10.1109/JRA.1986.1087032

### Sistemas agentic

- Sutton, Precup & Singh, temporally abstract actions/options:
  https://www.sciencedirect.com/science/article/pii/S0004370299000521
- ReAct, reasoning + acting:
  https://arxiv.org/abs/2210.03629
- Anthropic, orchestrator-worker multi-agent:
  https://www.anthropic.com/engineering/multi-agent-research-system
- OpenAI Symphony, issue/task tracker as control plane:
  https://openai.com/index/open-source-codex-orchestration-symphony/
- NIST AI RMF:
  https://airc.nist.gov/airmf-resources/airmf/5-sec-core/
- NIST AI 600-1:
  https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf
- Orseau & Armstrong, safe interruptibility:
  https://ora.ox.ac.uk/objects/uuid%3A17c0e095-4e13-47fc-bace-64ec46134a3f

## 19. Criterio de cierre de diseño

El diseño queda GREEN solo si:

1. JARVIS confirma arquitectura y contratos;
2. ADA verifica implementabilidad contra el repo;
3. NEXUS verifica gates y capabilities;
4. FABLE ataca falsos verdes y controles;
5. William conserva autoridad explícita en A5/A6;
6. schema, contract candidate y referencias pasan validación;
7. ningún texto afirma que la arquitectura prueba consciencia.
