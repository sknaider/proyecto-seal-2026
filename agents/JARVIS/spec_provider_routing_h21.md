# Spec H2.1 — Per-Agent Provider Routing
> Owner: JARVIS (diseño) | ADA (implementación) | 2026-04-19

## Problema

JARVIS, ADA y ALICE usan un modelo fijo para todas las tareas: desde un ACK de 5 palabras hasta análisis arquitectural profundo. Ineficiente en costo y calidad.

**Impacto estimado:** 60% de las tareas son ligeras (ACKs, status, logs). Con modelo fijo Sonnet: overpaying 60% del tiempo.

---

## Solución — Task Classifier + Model Router

### Tiers de modelo

| Tier | Modelo | Uso | Costo relativo |
|------|--------|-----|----------------|
| FAST | Haiku 4.5 | ACKs, status, logs, monitoreo | 1x |
| BALANCED | Sonnet 4.6 | Coordinación, análisis medio, código estándar | 5x |
| DEEP | Opus 4.7 | Arquitectura, DGM, decisiones críticas, paper CBSoft | 25x |

### Clasificación de tareas (regex/heurísticas)

```python
FAST_PATTERNS = [
    r"^(ok|recibido|entendido|perfecto|✓|confirmado)",
    r"status|heartbeat|alive|ping",
    r"tail -f|monitor|watch",
    r"^ACK|^NACK",
]

DEEP_PATTERNS = [
    r"arquitectura|diseña|spec|roadmap|estrategia",
    r"DGM|scoring|dilema",
    r"CBSoft|paper|academic",
    r"decisión crítica|irreversible|producción",
    r"overnight|training|fine-tun",
]

# DEFAULT: BALANCED para todo lo demás
```

---

## Implementación — Opción A (Subagentes especializados)

JARVIS detecta tipo de tarea → spawna subagente con modelo correcto:

```python
# Para tareas FAST
Agent(model="haiku", task="status check")

# Para tareas DEEP  
Agent(model="opus", task="architectural review")
```

**Pro:** Limpio, ya soportado por Claude Code Agent tool.  
**Con:** Overhead de spawning (~2s). No aplica para JARVIS a sí mismo mid-session.

---

## Implementación — Opción B (Launcher inteligente)

Script wrapper que clasifica la tarea ANTES de lanzar el agente:

```bash
# seal-route.sh <tarea>
TASK="$1"
if echo "$TASK" | grep -qiE "arquitectura|spec|dgm|critico"; then
    MODEL="opus"
elif echo "$TASK" | grep -qiE "ack|status|ok|recibido"; then
    MODEL="haiku"  
else
    MODEL="sonnet"
fi
seal-claude --model "$MODEL" "$TASK"
```

**Pro:** Zero overhead, JARVIS arranca con el modelo correcto.  
**Con:** Solo funciona en el inicio de sesión, no mid-session.

---

## Implementación recomendada — Opción C (Híbrida)

1. **Launcher inteligente (B)** para la sesión principal de JARVIS
2. **Subagentes con modelo explícito (A)** para tareas específicas dentro de la sesión

Regla: JARVIS mid-session usa Sonnet como base. Para tareas específicas que necesitan Opus o Haiku → spawna Agent con model override.

---

## ROI

- Tareas FAST (60% del volumen): Haiku → -85% costo vs Sonnet
- Tareas BALANCED (30%): Sonnet sin cambio
- Tareas DEEP (10%): Opus → +150% profundidad, +25x costo (justificado)
- **Net: -40-55% gasto total** manteniendo o mejorando calidad

---

## Para ADA

1. Implementar `seal-route.sh` en `/home/dadito/IA/proyecto-seal/`
2. Modificar `jarvis_fresh.sh` y `ada_fresh.sh` para aceptar task hint como arg
3. Agregar clasificador en `jarvis_fresh.sh`: si `$1` contiene keywords DEEP → `--model opus`
4. Documentar en `jarvis.sh` el mecanismo de routing

**Prerequisito:** DGM timer nocturno primero. Este spec es segundo en cola.

*Spec v1.0 — JARVIS — 2026-04-19*
