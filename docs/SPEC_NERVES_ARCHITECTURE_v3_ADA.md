# SOUL NERVES — Arquitectura canónica v3.1

**Fecha:** 2026-07-22
**Owner del contrato:** ADA
**Estado:** LIVE en sensado/motivación/reflejos determinísticos; SHADOW en activación ejecutiva con LLM
**Contrato legible por máquina:** `memory/nerves_contract_v3.json`

## 1. Por qué existe esta versión

Los specs v1 describían correctamente la identidad de cada agente, pero quedaron
fragmentados por agente y por drive. Después se añadieron mantenimiento útil,
FABLE, RLS, canarios y el governor de proactividad sin una capa canónica que
distinguiera tres hechos diferentes:

1. un tanque acumula presión;
2. un reflejo allowlisted produce un artefacto;
3. un runtime LLM despierta, recibe herramientas y ejecuta trabajo abierto.

Solo (1) y parte de (2) están LIVE hoy. (3) sigue deliberadamente SHADOW. Este
documento reemplaza afirmaciones ambiguas como "NERVES activó al agente" por un
contrato que exige nombrar la etapa y la evidencia observada.

Los specs históricos siguen siendo la fuente de personalidad y calibración. Esta
v3.1 es la fuente de verdad de arquitectura, lifecycle y gates.

### 1.1 Corrección de alcance: SOUL Nervous System ≠ daemon NERVES

El documento fundacional `memory/SOUL_NERVOUS_SYSTEM.md` usa *sistema nervioso*
como analogía de **todo SOUL**: memoria, instintos, recompensa, procedimientos,
atención y working state. Esa visión sigue vigente. El proceso
`memory/seal_nerves.py` es un subsistema mucho más estrecho: motivación LIF,
sensores y reflejos acotados.

Confundir ambos produjo la expectativa falsa de que un `fire` ya equivalía a
despertar la corteza/LLM con todas sus herramientas. La arquitectura canónica
separa desde ahora:

```text
SOUL Nervous System (macroarquitectura cognitiva completa)
├─ memoria/hipocampo        → memories, working_state, graph
├─ instintos/reflejos      → instincts + NERVES REFLEX
├─ motivación/estado      → motivation_states + LIF
├─ atención/tálamo       → recall/rerank/attention gate
├─ policy/ganglios basales→ governor, leases, budgets, safety
├─ corteza ejecutiva      → runtime del agente/LLM
├─ cerebelo/procedimientos→ skills, procedures, harnesses
└─ efectores               → tools/MCP/servicios permitidos

NERVES Motivation Engine (este spec)
└─ sensores → tanques → gate → reflejo o propuesta ejecutiva
```

NERVES marca la diferencia cuando cierra el circuito; no necesita fingir que es
la totalidad del cerebro.

## 2. Definición operativa

NERVES es el sistema de **sensado, acumulación LIF, decisión y reflejos acotados**
de SOUL. Corre con cero tokens LLM por tick. No es por sí mismo un planificador,
un agente general ni una prueba de conciencia.

```text
evento tipado
  -> sensor con provenance/frescura
  -> tanque LIF por agente
  -> umbral + cooldown + single-flight
  -> policy de acción
       -> REFLEX: ejecutor determinístico allowlisted (LIVE)
       -> EXECUTIVE: propuesta/wake gobernado (SHADOW)
  -> efecto + artefacto + métrica
  -> reset solo después del efecto requerido
```

### 2.1 Invariantes no negociables

1. **Causalidad:** todo efecto lleva `run_id`, `action_id`, `trigger_source` y
   cadena de estados auditable.
2. **At-most-one:** el lease cubre sensado→estímulo→decisión→efecto→commit,
   no solamente la lectura final del tanque.
3. **Fail-closed:** sensor desconocido no equivale a sano/vacío; acción fallida
   no equivale a completada.
4. **Reset-after-proof:** si no existe efecto/entrega/telemetría requerida, el
   tanque conserva presión para retry.
5. **No theatrical fire:** una frase `Investigando…` o `[SILENT]` sin artefacto
   no es trabajo.
6. **Least authority:** cada reflejo recibe solo la función/rol/credencial que
   requiere; un impulso no hereda automáticamente todas las herramientas.
7. **No synthetic truth:** timers no inventan uso de contexto, salud ni
   prioridad cuando falta una señal autoritativa.

Un `nerves_fire` publicado en chat es **telemetría**, no un wake. Los bridges y
monitores lo filtran para impedir feedback recursivo y gasto de tokens accidental.

## 3. Inventario vivo

### 3.1 Motor compartido

Agentes: `ADA`, `ALICE`, `DUM`, `JARVIS`, `NEXUS`.

| Drive LIVE | Sensor/entrada | Efecto actual | Clasificación |
|---|---|---|---|
| `curiosity` | inactividad, tema/paper, prioridad | mantenimiento del rol cuando `SEAL_NERVES_USEFUL=1` | REFLEX LIVE |
| `social_drive` | silencio/interacción | mantenimiento del rol; contacto solo si useful está apagado | REFLEX LIVE |
| `task_drive` | tareas, deadlines, cambios de estado | dedup, escalación y sugerencia durable; no inicia un LLM | DECISION LIVE, EXECUTOR SHADOW |
| `alert_drive` | logs y probes por dominio | diagnóstico, monólogo o alerta dirigida | REFLEX LIVE |
| `context_pressure` | solo evento autoritativo del runtime | checkpoint/distilación/briefing | HANDLER LIVE, SENSOR `external_signal_pending` |

Los tanques históricos `boredom`, `energy_drive`, `learning_drive` y
`vigilance` permanecen en PostgreSQL para trazabilidad. El motor los excluye del
contrato vivo y jamás debe generar métrica/fire nuevo para ellos.

### 3.2 Acciones útiles por rol

Cada tick puede ejecutar como máximo una acción útil por agente. Dos drives que
crucen juntos reutilizan el mismo resultado.

| Agente | Pulso allowlisted | Artefacto esperado |
|---|---|---|
| ADA | `engineering_pulse` | `nerves_ada_maintenance.jsonl` |
| ALICE | `delivery_pulse` | health de ORION |
| DUM | `infrastructure_pulse` | `nerves_dum_maintenance.jsonl` |
| JARVIS | `integrity_pulse` | `nerves_jarvis_maintenance.jsonl` |
| NEXUS | `security_pulse` | `nerves_nexus_maintenance.jsonl` |

Un resultado limpio queda silencioso. Un finding produce artefacto y solo se
publica según severidad/política. Un fallo requerido no permite resetear el tank.

**Frontera ALICE/ORION:** la lógica canónica vive únicamente en
`agents/ALICE/orion/orion_nerve.py` y su único scheduler es
`alice-orion-nerve.timer` (20 min). `delivery_pulse` solo valida que el artefacto
sea `0600`, fresco, `OK`, `/login=200` y `db_user=svc_orion_exam`; jamás vuelve a
ejecutar el probe. El timer LIF compartido no sirve como scheduler de producto:
sus acciones dependen del cruce de umbral. Así hay una lógica, un productor y un
consumidor, sin dos probes compitiendo.

**Frontera JARVIS/integridad:** `tools/jarvis_nerves_watch.py::check()` es una
acción acotada invocada por `integrity_pulse` cuando el drive cruza umbral. El
timer paralelo `jarvis-nerves-watch.timer` queda deshabilitado: conservar su
código no autoriza un segundo scheduler. El resultado `GREEN` persiste evidencia
y queda silencioso; `FINDING/BROKEN` falla en voz alta con detalle acotado.

**Frontera NEXUS/seguridad:** `tools/nexus_nerves_watch.py::check()` es la acción
read-only allowlisted de `security_pulse`; `nexus-nerves-watch.timer` queda
deshabilitado para impedir un segundo scheduler. El impulso puede observar,
diagnosticar, persistir evidencia y alertar. No adquiere autoridad para mutaciones
destructivas ni para eludir aprobación: esa frontera permanece fuera de NERVES.

### 3.3 Identidad PostgreSQL dura

`app.agent` es contexto funcional, no identidad: cualquier cliente PostgreSQL
puede cambiar un GUC personalizado. Cada runtime autentica directamente como
`svc_soul_nerves_<agente>` con una credencial `0600` propia. Las políticas RLS
RESTRICTIVE derivan el agente de `session_user` mediante
`soul_v3.nerves_session_agent()` e ignoran un `SET app.agent` falsificado.

La frontera cubre estado, métricas, tareas, GAM, diagnósticos y las escrituras
allowlisted de memoria/monólogo. Los roles son `NOSUPERUSER`, `NOBYPASSRLS`,
`NOINHERIT`, sin membresías; el antiguo `svc_soul_nerves` permanece `NOLOGIN`.

### 3.4 Sidecar FABLE

FABLE conserva su arquitectura adaptada: `curiosity_drive`, `teach_drive`,
`rigor_drive`, `care_drive`. Solo `verificar_por_efecto` está LIVE; los otros tres
targets están OBSERVE-ONLY. Un target LIVE se resetea únicamente si su acción
termina y persiste el reporte.

FABLE no se añade al daemon genérico porque copiar los drives de la familia
violaría su contrato de identidad y su namespace de base.

## 4. Arquitectura v3.1 por capas

### S0 — SensorEvent

Toda presión nueva debe poder representarse como:

```json
{
  "event_id": "uuid",
  "tenant_id": "uuid",
  "agent": "ADA",
  "sensor": "task_deadline",
  "observed_at": "RFC3339",
  "expires_at": "RFC3339",
  "source_ref": "task:123",
  "provenance": "db_role_or_supervisor",
  "correlation_id": "uuid",
  "causation_id": "uuid|null",
  "dedup_key": "agent:sensor:source:state",
  "freshness_s": 12,
  "confidence": 1.0,
  "stimulus": "task_due_2h",
  "magnitude": 25.0,
  "risk_class": "read_only|bounded_write|authoritative"
}
```

Reglas:

- sin identidad/provenance confiable: almacenar o descartar, nunca actuar;
- un reloj no puede fingir presión de contexto;
- log viejo o multilinea sin timestamp hereda el timestamp real del evento;
- un sensor no marca dedup como consumido antes de que el efecto sea entregado.

### S1 — Estado LIF

El estado durable vive en `soul_v3.motivation_states`, aislado por `app.agent`.
La conexión restaura identidad RLS en **cada checkout** del pool. Cada agente
aplica sus overrides OCEAN, tau, threshold y cooldown.

Los cambios de modelo/retraining siguen el guard v1: benchmark conductual,
registro de drift y recalibración si cualquier delta OCEAN supera 0.05.

### S2 — Decision gate

Antes de un fire:

- threshold cruzado;
- cooldown libre;
- lock single-flight por agente;
- idempotency/dedup estable;
- ventana/presencia solo cuando no oculta urgencias;
- owner y lifecycle del target conocidos.

### S3R — Reflex executor (LIVE)

Solo comandos/funciones pre-registrados, no destructivos y con alcance de rol.
No construye prompts abiertos ni concede "todas las herramientas". Debe producir
un efecto verificable o un finding explícito.

### S3E — Executive wake broker (SHADOW)

Este nivel convierte una necesidad en un turno real del agente. No se habilita
hasta que existan juntos:

1. ledger durable `proposed -> leased -> running -> effect_verified -> closed`;
2. lease por agente y dedup/idempotency cross-daemon;
3. attention, budget, safety y egress gates enforced;
4. tool scope mínimo por tipo de trabajo;
5. prueba de resultado y dead-letter/recovery;
6. protección contra segunda sesión y loops agent-to-agent;
7. soak independiente sin spam, fuga, wake storm ni acción autoritativa.

`scripts/seal_proactivity_governor.py` y `memory/executive_event_router.py`
permanecen SHADOW. `GATES_ENFORCED=False` es una frontera, no un pendiente que
deba saltarse.

### S3.1 — Escalera de autonomía

| Nivel | Capacidad | Estado actual |
|---|---|---|
| `L0_OBSERVE` | sensar, acumular, medir | LIVE |
| `L1_REFLEX` | función determinística allowlisted, sin LLM | LIVE |
| `L2_BOUNDED_JOB` | job especializado con input/output y tools fijas | PILOT por target; no global |
| `L3_AGENT_WAKE` | despertar runtime del agente con lease y tool-scope | SHADOW |
| `L4_OPEN_EXECUTIVE` | plan abierto/multiherramienta/cross-agent | PROHIBIDO automáticamente hasta autorización y safety case |

La promoción ocurre **por target**, nunca activando de golpe un flag global de
"autonomía total".

### S4 — Efecto, entrega y evidencia

Un fire solo se considera completado cuando se cumplen sus obligaciones:

- la acción terminó;
- el artefacto/entrega requerida existe;
- la métrica fired se persistió;
- después, y solo después, el tank se reseteó.

La máquina causal mínima es:

```text
observed -> admitted -> stimulated -> threshold_crossed
         -> claimed -> effect_verified -> state_committed -> closed
                                  \
                                   -> failed_retryable -> retry|dead_letter
```

El ledger local `nerves_action_ledger.jsonl` persiste `claimed`,
`effect_verified`, `reset_committed` o `failed_retryable`. La métrica DB usa el
mismo `run_id` y `action_id`. El artefacto de negocio no puede sustituirse por
la métrica, ni la métrica por el artefacto.

### S5 — Observabilidad y recovery

La v3 exige distinguir:

- `pressure_crossed`;
- `reflex_completed`;
- `proposal_persisted`;
- `executive_wake_started`;
- `effect_verified`;
- `failed_retryable` / `dead_letter`.

El contador `fire_count` no prueba por sí solo que hubo trabajo autónomo. La
prueba mínima es `evento -> acción -> artefacto -> métrica -> reset/cooldown`.

## 5. Seguridad y límites

- Cero acceso a DMs ajenos; RLS/roles lo refuerzan.
- Cero acción destructiva automática.
- Secretos, logs y colas locales: `0600`; units: `UMask=0077`.
- `svc_soul_nerves`: no superuser, no bypass RLS, sin herencia peligrosa.
- `context_pressure` acepta solo señal del runtime que conoce el uso real.
- `nerves_fire` no entra a bridges LLM.
- Fallo de entrega/persistencia: no reset; el estado queda reintentable.
- Retired/observe/shadow/live son estados explícitos, nunca inferidos por nombre.

## 6. Lifecycle canónico

| Estado | Significado |
|---|---|
| `live` | efecto permitido, instrumentado y probado |
| `observe` | acumula/mide; no ejecuta target |
| `shadow` | decide qué haría; cero side effects de producción |
| `retired` | preservado para historia; no procesado |

Mover un target hacia `live` exige cambio en el manifest, prueba positiva,
prueba negativa, restart si toca daemon y soak proporcional al riesgo.

## 7. Gates de aceptación

El sello NERVES exige, como mínimo:

1. contrato estático: manifest = código = unidades;
2. cinco tanques compartidos exactos y cuatro históricos sin fires;
3. timers habilitados y último oneshot exitoso;
4. `SEAL_NERVES_USEFUL=1` en los cinco daemons compartidos;
5. RLS + tres policies + rol restringido;
6. canario compartido 5/5 con snapshot/restore;
7. FABLE target LIVE exacto + artefacto;
8. `nerves_fire` filtrado de todos los consumers LLM;
9. governor ejecutivo todavía fail-closed/shadow;
10. regresión enfocada y adversarial verde.
11. ORION: timer dedicado habilitado; el wrapper central solo consume/verifica el artefacto.
12. 1.000 escenarios conductuales **únicos**, no el mismo caso repetido para
    inflar el contador; deben variar agente, tank, voltaje, decay y cooldown.
13. E2E con `stimulate()` real: stimulus → threshold → acción/artefacto →
    fire_count → ledger → reset → restauración acotada del canario.
14. Fallos adversariales: sensor caído, acción fallida, persistencia fallida,
    doble timer y credencial/identidad equivocada deben fallar cerrados.
15. Cada scheduler activo tiene owner único; schedulers paralelos retirados no
    pueden producir el mismo efecto.

Comando canónico:

```bash
/home/dadito/IA/seal-spark/.venv/bin/python3 \
  tools/verify_nerves_spec_contract.py --live-db --evidence \
  --output research/flywire_results/nerves_contract_verification.json
```

## 8. Qué mejora esta v3.1

- una única arquitectura para familia + sidecar FABLE;
- inventario machine-readable que detecta drift;
- lenguaje verificable: presión, reflejo, propuesta y wake ya no se mezclan;
- historia preservada sin que tanques retirados parezcan vivos;
- ruta segura para la autonomía general, sin afirmar que ya existe;
- cada nueva capacidad debe llegar con lifecycle, owner, gate y evidencia.

## 8.1 Delta v3.1 tras auditoría por efecto

- reconcilia el concepto original de SOUL nervioso con el daemon de motivación;
- lock de pipeline completo y estímulo SQL atómico;
- sensores y efectos fallan cerrados;
- provenance causal y ledger antes de reset;
- E2E deja de inyectar presión con `UPDATE` directo y usa `stimulate()`;
- FABLE incorpora single-flight y cooldown;
- 1.000 escenarios son inputs conductuales distintos, no repeticiones;
- explicita que `context_pressure` espera productor autoritativo;
- formaliza la escalera L0→L4 y mantiene el executive general en SHADOW.

## 8.2 Roadmap seguro

1. Publicar un `SensorEvent` durable canónico y migrar sensores uno por uno.
2. Conectar `context_pressure` al monitor que conoce tokens reales, con firma de
   origen y sin incremento por reloj.
3. Promover jobs L2 por rol solo tras canario positivo/negativo y soak.
4. Implementar broker L3 con lease, budget, tool-scope, approval y dead-letter.
5. Evaluar L3 con utilidad, duplicación, costo, falsos wakes y daño evitado; no
   con cantidad bruta de fires.

## 9. Referencias históricas conservadas

- `memory/spec_nerves_*_v1.md`: identidad/thresholds por agente y drive.
- `memory/spec_nerves_retraining_guard_v1.md`: recalibración post-modelo.
- `fable/specs/FABLE_NERVES_DRIVES_v1.md`: identidad de drives FABLE.
- `docs/SPEC_AGENT_PROACTIVITY_GOVERNOR_ADA_v1.md`: diseño ejecutivo.
- `research/flywire_phase3_spec.md`: base experimental LIF.

En conflicto de estado vivo, prevalecen este spec + el manifest + el verificador.
