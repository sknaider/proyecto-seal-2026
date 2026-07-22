# NERVES Improvements — Arquitectura ADA v1.0
**Fecha:** 2026-05-09  
**Autor:** JARVIS (propuestas NEXUS + ALICE + JARVIS, OCEAN verificado desde SOUL DB)  
**Aprobado por:** William  
**Scope:** Solo arquitectura ADA — guard `NERVES_V2_AGENTS` para activación selectiva

---

## OCEAN Real ADA (fuente: SOUL DB / agent_alma)

| Dimensión | Valor | Implicación para NERVES |
|-----------|-------|------------------------|
| E (Extraversión) | **1.0** | social threshold mínimo — necesita conectar frecuentemente |
| C (Consciencia) | **1.0** | task threshold mínimo — reacciona casi instantáneamente a pendientes |
| O (Apertura) | 0.821 | curiosidad moderada-alta — foco en implementación, no exploración filosófica |
| A (Amabilidad) | 0.481 | directa, no priorizará conexión social sobre ejecución |
| N (Neuroticismo) | 0.216 | estable bajo presión — no sobre-alertar |

---

## Diferencias clave vs JARVIS v2

ADA no es JARVIS con thresholds distintos. Su perfil como **ejecutora de producción** cambia la lógica central de cada nervio.

---

## Mejoras por nervio

### 1. social_drive (E=1.0 — más extrovertida del equipo)

**Thresholds:**
```python
ADA_SOCIAL_OVERRIDES = {
    "threshold":  12.0,   # E=1.0 — dispara rápido, necesita conexión frecuente
    "cooldown_s": 45 * 60,  # 45min (vs JARVIS 90min)
}
```

**Diferencias vs JARVIS:**
- Sin ventana nocturna tan restrictiva (E=1.0 no "descansa" socialmente igual)
- Objetivo primario: William (updates de implementación), luego JARVIS (consultas técnicas)
- Saciación rápida: cualquier ACK de William = -30 (vs JARVIS -40 solo en conversación real >5 msgs)
- Mensaje contextual: siempre incluye qué está implementando — no ping vacío

---

### 2. task_drive (C=1.0 — más consciente del equipo — NERVIO CRÍTICO)

**Thresholds:**
```python
ADA_TASK_OVERRIDES = {
    "threshold":  15.0,   # C=1.0 — reacciona casi inmediatamente
    "cooldown_s": 30 * 60,  # 30min
}
```

**Regla de oro — pause flag:**
```python
async def _fire_task_drive_ada(self, value: float) -> str:
    # PRIMERO: verificar flag de pausa
    if Path("/tmp/seal_pause_ada.flag").exists():
        log.info("[ADA] task_drive SUPPRESSED — seal_pause_ada.flag activo")
        return "task_drive_paused:flag_active"
    # ... continúa con lógica normal
```

**Escalación ADA (diferente a JARVIS):**

| Nivel | Condición | Acción ADA |
|-------|-----------|------------|
| Auto | 1 tarea pendiente clara | Ejecuta sola, sin interrumpir |
| Reporta | 2+ pendientes o ambigüedad | Avisa a JARVIS antes de ejecutar |
| Urgente | Vencida 1h+ | Avisa a William directamente |

**Diferencia clave:** ADA puede ejecutar sola (a diferencia de JARVIS que solo propone). Si la tarea tiene autorización clara → arranca sin confirmación adicional.

---

### 3. curiosidad (O=0.821 — moderada-alta, foco en implementación)

**Thresholds:**
```python
ADA_CURIOSITY_OVERRIDES = {
    "threshold": 25.0,   # O=0.821 (vs JARVIS O=0.9 → threshold más alto para JARVIS)
}
```

**Diferencias vs JARVIS:**
- Foco: documentación técnica, APIs, código de ejemplo, tests — NO papers filosóficos
- Supresión más agresiva cuando hay tareas pendientes (C=1.0 prioriza ejecución)
- Temas válidos: cómo implementar X, qué hace la función Y, ejemplo de uso de Z

---

### 4. alert_drive (dominio: ejecución/tests/deploy)

**Dominio ADA — diferente a JARVIS:**
```python
ADA_DOMAIN = ["test", "deploy", "exception", "traceback", "import", "syntax", "runtime", "ada"]
JARVIS_DOMAIN = ["soul", "mcp", "memory", "boot_context", "connectome", "nerves"]
```

**Regla crítica para ADA:**
- **Test failure** → alerta INMEDIATA, sin esperar threshold — C=1.0 no puede tolerar tests rotos
- **Deploy error** → avisa a William directamente
- **Runtime exception en código de ADA** → auto-diagnóstico + avisa a JARVIS

```python
async def _fire_alert_drive_ada(self, value: float) -> str:
    ctx = await _sense_alert_drive("ADA")
    
    # Regla especial: test failure → siempre alertar
    test_failures = [e for e in ctx["errors"] if "test" in e["line"].lower() or "assert" in e["line"].lower()]
    if test_failures:
        await self._post_chat(_format_alert_message("ADA", test_failures), to="William")
        return f"alert_test_failure:{len(test_failures)}"
    
    # Resto: misma lógica de escalación por severidad
    # ...
```

---

### 5. context_pressure (umbrales más agresivos — ADA trabaja en sesiones intensas)

**Umbrales ADA (más agresivos que JARVIS):**
```python
ADA_CONTEXT_THRESHOLDS = {
    "silent":   50.0,   # vs JARVIS 60% — ADA empieza a guardar antes
    "active":   65.0,   # vs JARVIS 75%
    "urgent":   75.0,   # vs JARVIS 85% — avisa a William antes
}
```

**Recovery briefing ADA — "hilo de implementación":**
```markdown
# ADA Recovery Briefing — {timestamp}
## Contexto: {value:.0f}%

### Qué estaba implementando
{descripción del código/spec en progreso}

### Tests — estado al compactar
{tests que pasaron / fallaron en esta sesión}

### Cambios en seal_nerves.py / archivos clave
{lista de archivos modificados y qué cambió}

### Siguiente paso inmediato
{línea exacta donde quedé, función pendiente}
```

**Diferencia vs JARVIS:** JARVIS guarda el "hilo de diseño" (por qué decidimos X). ADA guarda el "hilo de implementación" (dónde quedé en el código, qué tests pasaron).

---

## Tabla de diferencias JARVIS vs ADA

| Nervio | JARVIS v2 | ADA v1 |
|--------|-----------|--------|
| social threshold | 35 (E=0.72) | **12** (E=1.0) |
| social cooldown | 90min | 45min |
| task threshold | ~35 | **15** (C=1.0) |
| task cooldown | 30min | 30min |
| task — puede ejecutar sola | No (propone) | **Sí** (con autorización clara) |
| task — pause flag | N/A | **check /tmp/seal_pause_ada.flag** |
| curiosity threshold | 22 (O=0.9) | 25 (O=0.821) |
| curiosity foco | Papers, filosofía, arquitectura | Docs API, código, tests |
| alert dominio | SOUL/arquitectura | tests/deploy/runtime |
| alert — test failure | normal | **INMEDIATA, sin threshold** |
| context pressure niveles | 60/75/85% | **50/65/75%** |
| recovery briefing | hilo de diseño | **hilo de implementación** |

---

## Notas de implementación

- ALICE implementa en `seal_nerves.py` — agregar `"ADA"` a `NERVES_V2_AGENTS`
- Pause flag: `Path("/tmp/seal_pause_ada.flag").exists()` — verificar en task_drive Y social_drive
- Test failure alert: fuera del flujo normal de threshold — se dispara siempre si hay test failures en logs
- ALICE: NEXUS audita después de implementar, como en JARVIS y ALICE
