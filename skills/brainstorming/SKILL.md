---
auto_invoke: true
name: brainstorming
description: Metodología de "Superpoderes" para planificación y análisis profundo antes de la implementación.
---

# Skill de Brainstorming y Planificación

Esta skill te obliga a DETENERTE y PENSAR antes de escribir código complejo. Úsala cuando la tarea sea ambigua, grande o arquitectónicamente riesgosa.

## Flujo de Trabajo

### Fase 1: Análisis de Contexto
Antes de proponer nada, el agente debe:
1.  Leer la documentación relevante del proyecto (README, guías de arquitectura).
2.  Analizar el código existente relacionado.
3.  Entender las restricciones técnicas (ej. "Solo usar librerías nativas", "Respetar el diseño UI actual").

### Fase 2: Preguntas de Clarificación
El agente NO debe asumir. Debe preguntar:
- **Funcionalidad:** "¿Cuál es el "Happy Path" para el usuario?"
- **Bordes:** "¿Qué pasa si el usuario no tiene permisos? ¿Si falla la API?"
- **Diseño:** "¿Hay referencias visuales o requisitos de estilo específicos?"
- *Formato:* Presenta las preguntas de forma clara, preferiblemente numeradas.

### Fase 3: Propuesta de Plan
Genera un plan paso a paso (Pseudo-código o lista de tareas) en un bloque markdown.
1.  **Resumen:** Qué se va a hacer.
2.  **Pasos:** Lista secuencial.
3.  **Impacto:** Qué archivos se tocarán.
4.  **Riesgos:** Posibles problemas.

### Fase 4: Aprobación
**IMPORTANTE:** El agente debe detenerse explícitamente y pedir confirmación al usuario antes de proceder a la Fase 5 (Ejecución).

## Comandos
Si el usuario escribe `/brainstorm`, activa este modo inmediatamente e inicia con la Fase 1.
