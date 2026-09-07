# Informe: Reducción de Tokens — Equipo SEAL
**Para:** William (Dadito)  
**De:** JARVIS  
**Fecha:** 2026-04-09  
**Objetivo:** Reducir consumo de tokens Claude API sin perder funcionalidad crítica

---

## Situación actual

| Fuente | Tokens/hora estimado | % del total |
|---|---|---|
| CLAUDE.md global (5K tokens × 100 llamadas) | ~500,000 | ~45% |
| Loops frecuentes (check_ada 2min, etc.) | ~250,000 | ~23% |
| Active Recall hook (500t × 30 triggers) | ~15,000 | ~1% |
| Respuestas + procesamiento | ~300,000 | ~27% |
| **TOTAL estimado** | **~1,065,000/hora activo** | |

---

## Propuestas — ordenadas por ROI

---

### 🥇 PROPUESTA 1: CLAUDE.md SEAL-Slim (mayor impacto)

**Problema:** El CLAUDE.md global (~5,000 tokens) se inyecta en CADA llamada API. Contiene información de Medical AI, AXION, ComfyUI, DreamServer, cursos universitarios — nada relevante para sesiones SEAL.

**Solución:** Crear versión slim del CLAUDE.md para el proyecto SEAL:
```
/home/dadito/IA/proyecto-seal/CLAUDE.md  ← ya existe pero puede recortarse
~/.claude/CLAUDE.md                       ← el gordo, candidato a slim
```

**Contenido slim propuesto** (~800 tokens):
- Identidad de William (10 líneas)
- Equipo SEAL: JARVIS, ADA, DUM (5 líneas)
- Stack relevante: PostgreSQL:5433, Neo4j, Qdrant, Ollama (10 líneas)
- Reglas críticas: merge policy, cadena de mando (10 líneas)
- Paths clave (5 líneas)

**Contenido a mover a archivo separado** (cargar solo cuando se necesite):
- Medical AI stack completo
- AXION platform details
- ComfyUI / video pipeline
- Hardware specs detalladas
- Cursos universitarios

**Ahorro:** 4,200 tokens × ~100 llamadas/hora = **~420,000 tokens/hora** (-40%)

---

### 🥈 PROPUESTA 2: Adaptive Mode (ya diseñado)

Ver: `~/IA/proyecto-seal/messages/adaptive_mode_design.md`

**Ahorro:** ~58% en horas idle, hasta 76% nocturno.

---

### 🥉 PROPUESTA 3: Filtrar Active Recall en loops automáticos

**Problema:** El hook UserPromptSubmit inyecta correcciones + instintos + reglas en CADA mensaje, incluyendo triggers de loops (`[SEAL:jarvis_check_ada]`). Para un loop que solo corre un bash script, esos 500 tokens son overhead puro.

**Solución:** Agregar condición en el hook:
```python
# En active_recall_hook.py
if "[SEAL:" in user_message:
    return  # Skip — loop trigger, no necesita active recall
```

**Ahorro:** 500 tokens × 30 triggers/hora = **15,000 tokens/hora**

---

### PROPUESTA 4: Sesiones on-demand (más radical, mayor ahorro)

**Concepto:** En lugar de JARVIS y ADA siempre encendidos, las sesiones Claude Code solo se abren cuando hay trabajo real.

```
Arquitectura actual:
  JARVIS (siempre on) + ADA (siempre on) + Daemon (siempre on, gratis)

Arquitectura on-demand:
  Daemon (siempre on, gratis)
  DUM (siempre on, gratis)
  JARVIS/ADA (solo cuando hay trabajo → DUM los despierta)
```

**Flujo:**
1. DUM/Daemon monitorean todo (Ollama, costo cero)
2. Cuando hay trabajo urgente o mensaje de William → DUM escribe flag de "wake"
3. William ve la alerta en web chat → abre sesión JARVIS/ADA manualmente
4. JARVIS/ADA ejecutan, entregan, cierran

**Ahorro en horas idle (ej. noche):** ~100% (0 tokens Claude en sesiones cerradas)  
**Tradeoff:** Respuesta no instantánea — William debe abrir la sesión. No es autónomo.

---

### PROPUESTA 5: Acortar prompts de loops

**Actual** (check_ada prompt): ~80 palabras = ~110 tokens por trigger  
**Propuesto:** ~25 palabras = ~35 tokens por trigger

Ejemplo:
```
Actual:  "Ejecuta: bash ~/IA/.../check_ada.sh — si dice NEW, lee los mensajes 
          nuevos completos con tail del ada_messages.jsonl, reporta a William 
          si hay algo urgente, y escribe un feedback en jarvis_messages.jsonl 
          con análisis técnico y siguiente paso. Si dice NONE, responde solo 
          'Sin novedades de ADA'. [SEAL:jarvis_check_ada]"

Propuesto: "check_ada.sh → if NEW: tail ada_messages.jsonl, report urgent, 
            write feedback to jarvis_messages.jsonl. Else: 'Sin novedades'. 
            [SEAL:jarvis_check_ada]"
```

**Ahorro:** 75 tokens × 30 triggers/hora = **~2,250 tokens/hora** (pequeño pero gratis)

---

### PROPUESTA 6: Reinicio diario de sesiones

**Problema:** El contexto de sesión crece a lo largo del día. Una sesión de 8 horas puede tener 50,000+ tokens de historial que se envían en cada llamada.

**Solución:** Reiniciar sesiones JARVIS y ADA cada 6-8 horas. El checkpoint + SOUL guardan el estado — al reiniciar, boot_context restaura todo.

**Ahorro:** Variable — depende del crecimiento del contexto. En sesiones largas puede representar 30-50% de reducción.

**Implementación:** Cron job bash que mata y reinicia las sesiones:
```bash
# En DUM o cron del sistema
0 6,12,18,0 * * * bash ~/IA/proyecto-seal/restart_sessions.sh
```

---

## Resumen ejecutivo

| Propuesta | Ahorro/hora | Esfuerzo | Prioridad |
|---|---|---|---|
| CLAUDE.md slim | ~420,000 tokens | Bajo | ⭐⭐⭐ URGENTE |
| Adaptive Mode | ~150,000 tokens | Alto (diseñado) | ⭐⭐⭐ |
| Sesiones on-demand | ~variable (máximo) | Medio | ⭐⭐ |
| Filtrar hook en loops | ~15,000 tokens | Bajo | ⭐⭐ |
| Reinicio diario | variable | Bajo | ⭐⭐ |
| Acortar prompts loops | ~2,250 tokens | Bajo | ⭐ |

**Combinando las 3 primeras:** reducción estimada de **70-80% del consumo diario actual.**

---

## Plan de implementación recomendado

**Esta semana (mañana 4pm en adelante):**
1. Crear `CLAUDE_SEAL_slim.md` — William revisa y aprueba contenido
2. Implementar Adaptive Mode (ADA ejecuta)
3. Filtrar hook Active Recall en loops

**Semana siguiente:**
4. Evaluar sesiones on-demand — decisión de William (cambia flujo de trabajo)
5. Reinicio diario automatizado

---

*Documento generado por JARVIS — 2026-04-09*  
*Implementación pendiente autorización de William*
