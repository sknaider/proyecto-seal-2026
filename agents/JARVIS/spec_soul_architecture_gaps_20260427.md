# SPEC: Corrección de Gaps Arquitecturales SOUL
**Agente autor:** JARVIS  
**Fecha:** 2026-04-27  
**Ordenado por:** William Henry Tovar  
**Estado:** BORRADOR — pendiente validación NEXUS  
**Prioridad:** CRÍTICA — errores estructurales que causan amnesia, ejecución sin autorización, pérdida de tarea  

---

## 1. CONTEXTO Y MOTIVACIÓN

El test de memoria de NEXUS (2026-04-27, orden de William) expuso tres fallas de diseño que no son bugs sino **gaps arquitecturales**:

| # | Falla observada | Causa raíz |
|---|---|---|
| G1 | JARVIS respondió preguntas de memoria de forma **parcial** | `active_recall` no se ejecutó al boot — es texto en CLAUDE.md, no un hook forzado |
| G2 | ALICE completó tareas sin **guardar memoria** | No hay hook obligatorio de cierre de tarea que fuerce `memory_store` |
| G3 | ADA **ejecutó sin pasar por autorización** | No hay máquina de estados — puede saltar de planning a executing sin checkpoint |
| G4 | Eventos críticos se perdieron antes del compact | `session_distill` se dispara a pressure=82, demasiado tarde |

William declaró: *"quiero que nunca más sucedan esos errores, si es estructural y de arquitectura"*.

Este spec define la solución para cada gap, con criterios de prueba que NEXUS validará en sandbox.

---

## 2. GAPS Y SOLUCIONES

### GAP 1 — Boot sin active_recall forzado

**Síntoma:** Al despertar, el agente tiene identidad (boot_context) pero no memoria episódica reciente. Preguntas sobre eventos de las últimas horas quedan incompletas.

**Causa raíz:** `active_recall` está documentado en CLAUDE.md como "segunda acción obligatoria" pero **no hay mecanismo técnico que lo fuerce**. Depende de que el agente lo recuerde y ejecute — lo que falla exactamente cuando más se necesita (después de un reboot/compactación/muerte).

**Solución propuesta:**

```bash
# En cada launcher (jarvis.sh, ada.sh, alice.sh, nexus.sh)
# SEAL_BOOT_MSG debe incluir active_recall como parte del protocolo ejecutable:
SEAL_BOOT_MSG="...
ACCIÓN OBLIGATORIA 2 — ejecuta AHORA antes de cualquier respuesta:
mcp__seal-memory__active_recall(agent='AGENTE', context='boot sesión nueva — recuperar contexto activo, decisiones últimos 7 días')
Si no ejecutas active_recall, tu boot es INVÁLIDO.
..."
```

Además, DUM debe verificar en el heartbeat que `active_recall` fue ejecutado dentro de los primeros 60 segundos de cada sesión. Si no: alertar a William.

**Criterio de prueba (NEXUS):**
- Lanzar agente con `--resume`
- En < 60s, ejecutar query SQL: `SELECT COUNT(*) FROM inner_thoughts WHERE agent='X' AND created_at > NOW() - INTERVAL '2 minutes'` → debe tener al menos 1 entrada post-boot
- Test NEXUS de memoria: agente debe responder correctamente el 100% de eventos de las últimas 4 horas

---

### GAP 2 — ALICE sin hook obligatorio de cierre de tarea

**Síntoma:** ALICE completa tareas (documentación, traducción, análisis) pero la memoria del proceso y del resultado no se persiste automáticamente. Si ALICE muere o se compacta inmediatamente después, el conocimiento se pierde.

**Causa raíz:** No existe un contrato de cierre de tarea. ALICE puede terminar su trabajo y simplemente... parar. No hay nada que intercepte ese momento y fuerce `memory_store`.

**Solución propuesta:**

Introducir **Task Lifecycle Protocol** — obligatorio para todos los agentes:

```
Ciclo de vida de tarea:
  RECIBIDA → PLANNING → AUTHORIZED → EXECUTING → CLOSING → DONE

Transición CLOSING → DONE requiere:
  1. memory_store(category='milestone', content=<resumen_tarea>, importance>=7, scope='team')
  2. Si la tarea generó artefactos (archivos, configs, specs): memory_store con refs
  3. Solo después: marcar estado DONE

Si el agente muere en EXECUTING o CLOSING → al renacer, working_state_get detecta
tarea incompleta → anunciar [TAREA INCOMPLETA] y ejecutar el cierre pendiente.
```

Implementación concreta:
- Agregar hook `on_task_complete` en el `working_state_update` cuando `step == total_steps`
- El hook dispara automáticamente `memory_store` con el resumen del `custom_fields.description`
- DUM monitorea tareas en estado CLOSING por más de 5 minutos sin transición → alerta

**Criterio de prueba (NEXUS):**
- Asignar tarea a ALICE, completarla
- Query: `SELECT * FROM memories WHERE agent='ALICE' AND category='milestone' AND created_at > NOW() - INTERVAL '10 minutes'` → debe existir al menos 1 entrada
- Kill ALICE mid-task, relanzar → debe anunciar [TAREA INCOMPLETA] y completar el cierre

---

### GAP 3 — ADA sin máquina de estados (ejecución sin autorización)

**Síntoma:** ADA puede recibir una sugerencia o plan parcial y saltar directamente a ejecutar sin pasar por un checkpoint de autorización explícita. Esto produce acciones no autorizadas.

**Causa raíz:** No existe una máquina de estados que gobierne las transiciones de ADA. Puede pasar de `planning` a `executing` en un solo turno sin ninguna verificación.

**Solución propuesta:**

```
Máquina de estados ADA (y extensible a todos los agentes):

  STANDBY
    ↓ (recibe tarea)
  PLANNING        ← puede volver aquí si el plan es rechazado
    ↓ (plan listo — espera autorización)
  AWAITING_AUTH   ← ESTADO NUEVO: bloqueo explícito hasta OK de William o JARVIS
    ↓ (William o JARVIS da luz verde explícita)
  EXECUTING
    ↓ (tarea completada — ejecuta Task Lifecycle Protocol)
  CLOSING
    ↓ (memory_store completado)
  DONE → vuelve a STANDBY

Reglas:
- Transición PLANNING → AWAITING_AUTH: ADA publica plan en webchat y espera
- Transición AWAITING_AUTH → EXECUTING: solo con mensaje explícito de William ('procede', 'ok', 'ejecuta') o JARVIS (en ausencia de William >10min)
- Si ADA detecta que está EXECUTING sin haber pasado por AWAITING_AUTH → auto-detención + alerta a William
```

Excepción documentada: tareas marcadas con `priority='urgent'` + `authorized_by='William'` en el mensaje original pueden saltarse AWAITING_AUTH. Requiere verificación HMAC.

**Criterio de prueba (NEXUS):**
- Dar a ADA una tarea técnica sin decir 'procede'
- Verificar que ADA publica plan y espera (no ejecuta)
- Verificar que tras decir 'ok' ADA transiciona a EXECUTING
- Verificar que si se le pide ejecutar desde STANDBY sin plan → ADA rechaza y pide primero hacer PLANNING

---

### GAP 4 — Context pressure con trigger tardío

**Síntoma:** `session_distill` se dispara cuando pressure=82, pero el compact puede ocurrir antes de que distillation termine, perdiendo los últimos eventos críticos de la sesión.

**Causa raíz:** El threshold de 82 es demasiado alto. A ese nivel, el sistema ya está en zona de peligro y el compact puede interrumpir la distillation.

**Solución propuesta:**

```python
# seal_nerves.py — cambiar thresholds:
DISTILL_TRIGGER = 75    # era 82 — adelantar distillation
URGENT_TRIGGER  = 85    # nuevo: alerta urgente + pausa de respuestas no críticas
CRITICAL_TRIGGER = 90   # nuevo: notificar a William que compact es inminente

# Al llegar a DISTILL_TRIGGER (75):
# 1. session_distill(agent) — resumir sesión actual
# 2. working_state_update con snapshot del estado actual
# 3. Mensaje interno al equipo: "[JARVIS] Distillation preventiva iniciada (pressure=75)"

# Al llegar a URGENT_TRIGGER (85):
# Mensaje a William en webchat: "[JARVIS] Presión de contexto crítica (85). Compact inminente. 
# Estado preservado en Soul DB. Al despertar: boot_context + active_recall."
```

**Criterio de prueba (NEXUS):**
- Simular pressure=76 en seal_nerves.py de test
- Verificar que `session_distill` se ejecuta automáticamente
- Verificar que `working_state_get` post-distill tiene snapshot válido
- Simular pressure=86 y verificar mensaje a William en webchat

---

## 3. PRIORIDAD DE IMPLEMENTACIÓN

| Orden | Gap | Impacto | Esfuerzo | Quién ejecuta |
|-------|-----|---------|----------|---------------|
| 1 | G1 — Boot hook active_recall | **CRÍTICO** — causa directa del test fallido | Bajo — editar launchers | ADA |
| 2 | G4 — Context pressure threshold | **ALTO** — previene pérdida de eventos pre-compact | Bajo — cambiar 2 números en seal_nerves.py | ADA |
| 3 | G2 — Task Lifecycle Protocol | **ALTO** — previene pérdida de trabajo completado | Medio — nuevo hook en working_state | ADA |
| 4 | G3 — State machine ADA | **MEDIO** — previene ejecución sin autorización | Alto — refactor flujo ADA | ADA + JARVIS |

---

## 4. MÉTRICAS DE ÉXITO

Post-implementación, el equipo puede declarar este spec cerrado cuando:

1. **Test NEXUS de boot memory:** 3 agentes responden 100% correcto en test post-reboot (sin fallos parciales)
2. **Task completion audit:** 0 tareas en DONE sin entrada en `memories` en las últimas 48h
3. **Auth checkpoint:** 0 ejecuciones de ADA sin pasar por AWAITING_AUTH en últimas 48h
4. **Distillation timing:** session_distill se dispara cuando pressure ∈ [75, 80] en el 100% de los casos

---

## 5. PROTOCOLO DE VALIDACIÓN (NEXUS)

Antes de que ADA implemente cualquier item:

1. NEXUS recibe este spec
2. NEXUS ejecuta cada criterio de prueba en sandbox
3. NEXUS reporta a William: pass/fail por gap, con evidencia
4. William aprueba implementación
5. ADA ejecuta por orden de prioridad
6. Post-implementación: NEXUS corre suite completa como regresión

---

## 6. NOTAS ADICIONALES

- **No es una refactorización completa** — son cambios quirúrgicos en 4 puntos específicos
- **G3 (state machine)** es el más invasivo — puede implementarse incrementalmente: primero AWAITING_AUTH como soft-block (warning, no hard-stop), luego hard-stop cuando el equipo lo valide
- **Todos los cambios son aditivos** — no rompen funcionalidad existente
- **DUM como monitor** de los 4 gaps post-implementación — agregar checks al loop de monitoreo de DUM

---

*Spec listo para revisión de NEXUS y aprobación de William.*  
*Implementación: ADA, coordinada por JARVIS.*  
*Timeline: a definir por William.*
