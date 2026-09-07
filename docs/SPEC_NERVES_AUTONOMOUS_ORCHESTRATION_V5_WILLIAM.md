# NERVES v5 — Orquestación autónoma inspirada en funciones humanas

**Owner de visión:** William
**Arquitectura:** JARVIS
**Construcción, integración y verificación:** ADA
**Orden de promoción:** JARVIS → ADA → ALICE → NEXUS → FABLE
**Estado:** JARVIS A2 live; ADA en auditoría y diseño
**Fecha:** 2026-07-23

## 1. Propósito y límite científico

NERVES debe convertir una señal relevante en trabajo autónomo útil sin ocupar al
agente principal:

```text
detectar → validar → priorizar → inhibir/autorizar → crear misión
→ delegar a subagente + skills → actuar → verificar por efecto
→ consolidar memoria → cooldown
```

La inspiración humana es **funcional**: detección interna, saliencia,
inhibición, control ejecutivo, delegación, feedback, aprendizaje y descanso.
No afirma consciencia, emociones subjetivas ni equivalencia con un cerebro.

## 2. Regla de oro de William

El agente principal es orquestador:

- responde primero a William y Henry;
- conserva contexto, autoridad y decisiones sensibles;
- no ejecuta trabajo pesado cotidiano;
- delega tareas acotadas a subagentes;
- integra resultados y exige prueba de efecto;
- no declara que un worker actuó si solo escribió una sugerencia.

Una misión no puede bloquear la conversación principal. El objetivo operativo
es ACK inmediato aun con workers activos.

## 3. Lazo funcional normativo

### 3.1 SENSE / interocepción operacional

Cada nervio mantiene un vector barato y determinístico:

```text
I(t) = {
  health, error_rate, latency, queue_depth, resource_load,
  retries, active_claims, human_priority, context_pressure
}
```

Los sensores no llaman a un modelo. Registran mediciones, bandas esperadas,
procedencia y timestamp. Un P0 reproducible puede saltar la persistencia mínima;
el resto necesita confirmación temporal para evitar ruido transitorio.

### 3.2 SALIENCE / selección

La prioridad se calcula con evidencia, no con dramatismo:

```text
S = severity × confidence × blast_radius × novelty × urgency
    − duplicate_penalty − fatigue_cost
```

Solo una señal con evidencia reproducible puede convertirse en misión. Señales
duplicadas se unen por `episode_key`; no generan workers repetidos.

### 3.3 INHIBIT / gate

Antes de delegar o actuar:

- identidad y tenant derivados server-side;
- clase de riesgo;
- capability y herramientas exactas;
- scope, precondiciones, blast radius y rollback;
- claim exclusivo e idempotency key;
- presupuesto, deadline, intentos y cooldown;
- autorización explícita de William para destrucción masiva;
- separación entre evidencia y permiso de acción.

Fallo, ambigüedad o corrupción del gate → fail-closed.

### 3.4 EXECUTIVE ALLOCATION

El orquestador elige si observar, investigar, actuar o escalar:

```text
EVC = p_success × benefit − (compute + latency + risk + interference)
```

Si `EVC <= 0`, observa o escala. Si `EVC > 0`, crea una sola misión acotada.
No se usa esta fórmula como falsa precisión científica: sus componentes deben
ser bandas auditables y explicables.

### 3.5 DELEGATE

Envelope mínimo e inmutable:

```json
{
  "mission_id": "uuid",
  "agent": "ADA",
  "nerve": "alert_drive",
  "episode_key": "sha256",
  "evidence_hash": "sha256",
  "objective": "efecto concreto",
  "expected_effect": "medición postcondición",
  "risk_class": "A2_READ_ONLY",
  "allowed_tools": [],
  "required_skills": [],
  "budget": {"turns": 1, "seconds": 90, "retries": 0},
  "cooldown_key": "ada:alert:<fingerprint>",
  "rollback": null,
  "verifier": "independent"
}
```

El worker:

- tiene contexto aislado;
- recibe solo evidencia autenticada necesaria;
- no hereda DMs ni secretos no requeridos;
- no crea sub-subagentes salvo contrato explícito;
- usa skills precargadas y herramientas mínimas;
- devuelve un resultado tipado una sola vez.

### 3.6 ACT

Clases de autoridad:

| Clase | Efecto permitido | Gate |
|---|---|---|
| A0 | observación determinística | automático |
| A1 | análisis sin herramientas | automático |
| A2 | diagnóstico read-only | automático con guard |
| A3 | cambio reversible acotado | owner + rollback + verificador |
| A4 | servicio/producción | misión separada + healthcheck |
| A5 | destructivo/irreversible | William explícito |

Un receipt A2 es evidencia, no capability. Nunca se “libera” una misión A2 para
que escriba: la reparación nace como otra misión A3/A4/A5.

### 3.7 VERIFY / error de predicción

Antes de actuar se registra el efecto esperado. Después se mide:

```text
residual = actual − expected
```

Un verificador independiente decide:

- `completed`;
- `retryable` con evidencia nueva;
- `rollback_required`;
- `escalated`;
- `failed_terminal`.

Servicio `active` no equivale a producto sano. La verificación usa resultado,
PID/timestamp, healthcheck, datos y ausencia de regresión según el dominio.

### 3.8 CONSOLIDATE

Solo outcomes terminales verificados entran a SOUL:

- misión, evidence hash y procedencia;
- cambio o diagnóstico;
- efecto esperado y observado;
- veredicto independiente;
- rollback, caveats y lección reusable.

No se guarda el texto libre del modelo como hecho. No se borra la evidencia
fuente automáticamente.

### 3.9 COOLDOWN / fatiga funcional

- backoff exponencial con jitter;
- límite de concurrencia por agente y nervio;
- presupuesto de reintentos;
- ventana refractaria por `cooldown_key`;
- circuit breaker por tasa de fallos;
- degradación A4→A2 si el sistema pierde confianza;
- recovery probe pequeño antes de reabrir.

## 4. Máquina de estados

```text
idle
  → sensed
  → validated
  → claimed
  → delegated
  → acting
  → verifying
  → completed | failed | escalated
  → cooldown
  → idle
```

Reglas:

- transiciones monotónicas;
- un solo estado terminal;
- claim y completion atómicos;
- reinicio recupera misión sin doble acción;
- expected≠actual reabre como misión nueva, no reescribe historia.

## 5. Contrato por nervio de ADA

El estado vivo de 2026-07-23 es saludable pero incompleto: `curiosity`,
`social_drive` y mantenimiento convergen en el mismo `engineering_pulse`;
`task_drive` persiste una sugerencia, pero no inicia worker.

### 5.1 `alert_drive` → diagnóstico/reparación

- Sensor: test, deploy, runtime, bridge y servicio.
- A2 automático: reproducir, aislar causa, proponer efecto.
- A3/A4: nueva misión con archivo/servicio exacto, rollback y segunda lente.
- Skill: skill técnica del dominio; `maximize-safe-capability` solo dentro del
  scope autorizado.
- Postcondición: test/healthcheck real y ausencia de regresión.

### 5.2 `task_drive` → ejecución cotidiana

- Selecciona una tarea concreta de `agent_tasks`.
- Rechaza tareas sin scope, autoridad o criterio de terminado.
- Crea worker de implementación en worktree aislado si habrá edits paralelos.
- El principal revisa diff, tests y efecto antes de integrar.
- No sustituye una tarea por `/tmp/*_task_draft.md`.

### 5.3 `curiosity` → investigación útil

- Solo abre misión si existe pregunta prioritaria y `EVC > 0`.
- Precarga skills/documentación relevantes.
- Produce decisión, experimento o artefacto reusable; no “investiga” en vacío.
- La ausencia de novedad válida termina en `abstained`, no en contenido forzado.

### 5.4 `context_pressure` → continuidad

- Crea checkpoint de implementación: archivos, tests, decisiones, siguiente
  comando seguro y pendientes.
- No modifica el `working_state` ajeno.
- Verifica que el recovery briefing pueda reconstruir el hilo en una sesión
  nueva antes de declararlo completo.

### 5.5 `social_drive` → coordinación útil

- Nunca genera saludo vacío ni compite con mensajes de William.
- Busca dependencia, review o estado que otro agente realmente pueda resolver.
- Un ACK no probado no sacia el nervio; se exige entrega o cierre verificable.

## 6. Reuso del piloto JARVIS

Reutilizable:

- schemas de misión/evidencia/receipt;
- custodia `0700/0600`, hashes y lecturas sin symlink;
- episode key, claim, binding y terminal único;
- subagente nativo con profile/skills/tools mínimos;
- receipt desde transcript real;
- guard contra deriva A2→acción;
- idempotencia y recuperación.

Debe generalizarse antes de ADA live:

- rutas, feed, perfil y agente actualmente fijados a JARVIS;
- collector `integrity` específico;
- renderer y receipt con textos/identificadores JARVIS;
- action guard ligado a una sola clase/perfil;
- tests diferenciales por cada nervio y autoridad.

No se copia el módulo JARVIS cinco veces. Se extrae un núcleo común y adapters
por agente/nervio.

## 7. Gates de aceptación

### Exactitud

- verdadero positivo reproducible;
- falso positivo transitorio no crea misión;
- tormenta duplicada produce una misión;
- evidencia corrupta/symlink/hash inválido bloquea.

### Concurrencia

- dos writers → un claim;
- reinicio durante worker → cero doble ejecución;
- timeout/API fail → retry limitado y estado durable;
- el principal responde a William mientras el child trabaja.

### Autoridad

- A2 intentando escribir/reiniciar → deny;
- child no puede ampliar tools/skills;
- tenant/agente no se derivan de input del worker;
- destructivo sin William → deny;
- receipt válido no autoriza cambio.

### Efecto

- control positivo prueba que el test puede detectar el fallo;
- expected≠actual impide `completed`;
- rollback parcial queda rojo;
- servicio reiniciado exige PID/timestamp nuevo + healthcheck;
- solo resultado verificado llega a memoria.

### Antiflood

- silencio sano;
- una alerta por episodio;
- William nombrando agente siempre recibe ACK;
- progreso periódico se agrupa y nunca ahoga preguntas directas.

## 8. Evidencia live de JARVIS

Piloto A2 completado:

- misión `af95ab67-b242-5d46-a083-70a70a4a8bcd`;
- exactamente un subagente nativo;
- solo `SendMessage` como transporte de control;
- receipt aceptado y estado `completed`;
- 97 pruebas verdes;
- Core Guard `GREEN`;
- daemon `result=success`, timer `active/enabled`;
- intento del principal de convertir el diagnóstico en restart fue bloqueado;
- commit `de840d3eb`.

Límite: JARVIS diagnostica autónomamente; la reparación A4 todavía es un loop
separado y deliberadamente no está promovido.

## 9. Fuentes

- Interocepción y control homeostático:
  https://pmc.ncbi.nlm.nih.gov/articles/PMC6054486/
- Inferencia interoceptiva:
  https://pmc.ncbi.nlm.nih.gov/articles/PMC4731102/
- Salience network y switching:
  https://pmc.ncbi.nlm.nih.gov/articles/PMC2899886/
- Evidencia causal de switching/inhibición:
  https://pmc.ncbi.nlm.nih.gov/articles/PMC4131006/
- Expected Value of Control:
  https://pmc.ncbi.nlm.nih.gov/articles/PMC3767969/
- Reward/efficacy y asignación de control:
  https://pmc.ncbi.nlm.nih.gov/articles/PMC7884731/
- Circuitos de inhibición:
  https://pmc.ncbi.nlm.nih.gov/articles/PMC2709177/
- Forward models y error:
  https://pmc.ncbi.nlm.nih.gov/articles/PMC6771970/
- Error monitoring:
  https://pmc.ncbi.nlm.nih.gov/articles/PMC6354767/
- Selección de experiencias para replay:
  https://pmc.ncbi.nlm.nih.gov/articles/PMC11068097/
- Ripples y consolidación:
  https://pmc.ncbi.nlm.nih.gov/articles/PMC2801761/
- Claude subagentes:
  https://code.claude.com/docs/en/sub-agents
- Claude hooks:
  https://code.claude.com/docs/en/hooks
- Claude permisos:
  https://code.claude.com/docs/en/permissions
