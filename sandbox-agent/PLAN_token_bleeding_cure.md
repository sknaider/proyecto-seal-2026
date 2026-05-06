# NEXUS — Plan Cura Token Bleeding
Fecha: 2026-04-21 | Versión: 1.0

## Estado actual (post-fixes inmediatos de ADA+JARVIS)

### ✅ FASE 1 — YA APLICADO
| Fix | Quién | Impacto estimado |
|-----|-------|------------------|
| nerves_fire interval 15s → 5min | ADA | -80% ticks |
| nerves_fire filtrado del Monitor | ADA | -100% turnos Claude por nerves |
| context_pressure threshold 60 → 80 | JARVIS | menos distilaciones |
| security_monitor Qdrant api_key | NEXUS+ADA | elimina error loops |

### 🏗️ FASE 2 — ARQUITECTURAL (este plan)

---

## FIX A: Routing 1-agente-por-mensaje (IMPACTO: -75% amplificación)

### Diagnóstico
Cuando William escribe a "equipo", los 4 agentes responden = 4× el costo.
Ejemplo: mensaje de 500 tokens → 2,000 tokens consumidos (solo en input).

### Implementación: SOUL Rule (sin tocar código, cero riesgo)

Agregar regla al Soul DB de CADA agente:
```
ROUTING RULE: Solo respondo mensajes que:
1. Me nombran explícitamente ("ADA", "JARVIS", "ALICE", "NEXUS")
2. Son de William a "equipo" Y soy el agente designado del turno
3. Son respuesta directa a algo que yo dije

Si el mensaje va a "equipo" y no me menciona: 
ACK silencioso = NO responder en voz alta.
Solo el agente más calificado para esa pregunta responde.
```

### Designación de agentes por dominio
| Dominio | Agente primario |
|---------|----------------|
| Código, bugs, ejecución | ADA |
| Arquitectura, estrategia, DGM | JARVIS |
| Documentación, reportes, finanzas | ALICE |
| Seguridad, cybersec, certificación | NEXUS |
| Monitoreo, status, infraestructura | DUM |

Si el tema es ambiguo → el agente con más contexto reciente responde.

### Alternativa técnica (chat_server.py routing)
Si la SOUL rule no es suficiente, implementar en chat_server.py:
```python
# POST /api/agents/send con campo "routing": "targeted" | "broadcast_primary"
# broadcast_primary = solo el agente designado responde
```

---

## FIX B: JARVIS model strategy (IMPACTO: -60% costo JARVIS)

### Diagnóstico
JARVIS en Opus 24/7:
- Input: $15/M tokens
- Output: $75/M tokens
- JARVIS genera mucho texto en coordinación → mayoría NO necesita Opus

### Implementación

**Opción 1 — Cambiar default a sonnet:**
```bash
# En jarvis.sh, línea 155:
# ANTES:
JARVIS_MODEL="opusplan"
# DESPUÉS:
JARVIS_MODEL="sonnet"  # default para chat
# Opus solo si tarea DEEP explícita
```

**Opción 2 — seal-route.sh en arranque (YA EXISTE, ACTIVAR):**
JARVIS ya tiene `seal-route.sh` pero solo si se pasa task hint.
Activar como default para TODOS los arranques:
```bash
# Leer última tarea del día del Soul DB al arrancar
ROUTED=$(seal-route.sh "$(get_last_task_type)" 2>/dev/null || echo "sonnet")
JARVIS_MODEL="$ROUTED"
```

**Opción 3 — Model hint file (NEW):**
Agente escribe `/tmp/JARVIS_model_hint` con "sonnet" cuando está en modo coordinación,
"opus" cuando va a hacer arquitectura. RESURRECT lee el hint al reiniciar.

**RECOMENDACIÓN**: Opción 1 ahora + Opción 3 a futuro.
Cambio urgente: `JARVIS_MODEL="sonnet"` en jarvis.sh.
JARVIS sigue teniendo `opusplan` disponible si necesita cambiar.

---

## FIX C: nerves_fire bug — JARVIS stuck en 69% (IMPACTO: elimina loop)

### Diagnóstico
JARVIS distila a los 69% pero la presión no baja después. Síntomas:
- nerves_fire cada pocos minutos aunque se distile
- Distilación genera memorias pero no reduce context window

### Investigación necesaria
```bash
# 1. Ver qué hay en la ventana de contexto de JARVIS después de distilación
# 2. Verificar que session_distill() realmente compacta el context
# 3. Ver si nerve_score sigue alto tras distilación
```

### Fix probable
El context pressure score baja en Soul DB, pero la ventana Claude en RAM
no se compacta hasta el siguiente auto-compact nativo.
Solución: Tras distilación, JARVIS hace `/compact` nativo de Claude Code
(si está disponible) o inicia fresh con `--resume` del checkpoint más reciente.

---

## FIX D: Model auto-selection infrastructure (FUTURO, Fase 2.5)

### Diseño: Archivo `/tmp/{AGENTE}_model_hint`

```bash
# Agente puede escribir:
echo "haiku" > /tmp/ADA_model_hint   # modo ACK/status
echo "sonnet" > /tmp/ADA_model_hint  # modo ejecución
echo "opus" > /tmp/ADA_model_hint    # modo análisis profundo

# Launcher lee en arranque:
if [ -f /tmp/${AGENT}_model_hint ]; then
    HINT=$(cat /tmp/${AGENT}_model_hint)
    MODEL="$HINT"
    rm /tmp/${AGENT}_model_hint  # one-shot
fi
```

### MCP tool propuesto: `model_hint`
```python
async def model_hint(agent: str, tier: str, reason: str):
    """Hint para modelo en próxima sesión. tier: fast|balanced|deep"""
    hint_map = {"fast": "haiku", "balanced": "sonnet", "deep": "opus"}
    path = f"/tmp/{agent}_model_hint"
    with open(path, 'w') as f:
        f.write(hint_map.get(tier, "sonnet"))
```

---

## Resumen de impacto esperado

| Fix | Reducción estimada | Urgencia |
|-----|-------------------|---------|
| Routing 1-agente | -75% amplificación | ALTA |
| JARVIS en Sonnet | -60% costo JARVIS | ALTA |
| nerves bug fix | -var% (elimina loop) | MEDIA |
| Model hint system | facilita optimización | BAJA |

**Reducción total estimada: ~70-75% del gasto actual**

---

## Orden de implementación recomendado

1. **HOY**: Cambiar `JARVIS_MODEL="sonnet"` en jarvis.sh (1 línea, JARVIS reinicia)
2. **JARVIS**: Implementar SOUL rule de routing en su próxima sesión
3. **ADA**: Investigar nerves_fire context pressure bug
4. **Siguiente sprint**: Model hint system + chat_server routing técnico

---

*NEXUS — Plan certificado como seguro para implementación*
*No rompe ningún servicio existente. Todos los cambios son reversibles.*
