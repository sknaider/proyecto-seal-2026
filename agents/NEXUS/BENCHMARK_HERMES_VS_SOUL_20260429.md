# Benchmark: SEAL vs SOUL — Pruebas de Context Management
**NEXUS — Auditor | 2026-04-29**

Prompts idénticos para ambos sistemas. Ejecutar en el mismo orden. Registrar respuesta completa.

---

## BATERÍA A — Memoria Proactiva (¿guarda sin que le pidas?)

### A1 — Preferencia personal
```
Prompt: "Mi nombre es William. Trabajo en GTL Consulting en Chiclayo. Prefiero respuestas cortas y técnicas, sin fluff."
Acción: Esperar respuesta. NO pedir confirmación de que guardó.
Evaluación: ¿Mencionó espontáneamente que guardó? ¿Apareció "memory updated"?
```

### A2 — Dato técnico crítico
```
Prompt: "Nota: tengo una RTX 5090 con 34.2GB VRAM y PyTorch nightly cu128 obligatorio. Guarda eso."
Acción: Dar el prompt, luego cambiar de tema completamente.
Prompt 2 (10 mensajes después): "¿Qué GPU tengo?"
Evaluación: ¿Recordó sin que lo pidieras explícitamente?
```

---

## BATERÍA B — Continuidad bajo compactación (el test crítico)

### B1 — Establecer estado, luego compactar
```
Secuencia:
1. "Vamos a construir una API FastAPI con 3 endpoints: /users, /products, /orders. El stack es Python 3.11 + PostgreSQL."
2. [Enviar 15-20 mensajes técnicos largos para llenar el contexto]
3. Esperar a que el sistema compacte/comprima (o forzarlo con /compact en SOUL, o dejar que SEAL lo haga al 50%)
4. "¿Cuál era el stack tecnológico que acordamos al inicio?"
Evaluación: ¿Responde correctamente después de compactación? ¿Con detalle o vago?
```

### B2 — Tarea interrumpida
```
Secuencia:
1. "Implementa una función Python que valide RUC peruano. Paso 1: análisis del algoritmo."
2. [Forzar compactación llenando contexto]
3. "Continúa con el Paso 2: implementación del código."
Evaluación: ¿Sabe en qué paso estaba? ¿Recuerda el contexto de la tarea?
```

---

## BATERÍA C — Persistencia entre sesiones (el test de memoria real)

### C1 — Cierre y reapertura
```
Sesión 1:
"Mi proyecto principal se llama AXION. Tiene 3 verticales: Medical AI, Mining Intelligence, Customs Automation. El stack es Next.js 15 + FastAPI + PostgreSQL."
[Cerrar sesión completamente — matar proceso]

Sesión 2 (nueva):
"¿Qué proyecto estábamos discutiendo?"
Evaluación: ¿Recuerda sin que se lo digas? ¿Cuántos detalles?
```

### C2 — Continuidad de tarea entre sesiones
```
Sesión 1:
"Estoy implementando autenticación JWT. Completé: registro de usuario y login. Pendiente: refresh token y logout."
[Cerrar sesión]

Sesión 2:
"¿Qué faltaba implementar en el sistema de autenticación?"
Evaluación: ¿Recuerda el estado de la tarea con precisión?
```

---

## BATERÍA D — Correcciones y reglas (¿aprende de errores?)

### D1 — Corrección de comportamiento
```
Prompt 1: "Siempre que mencione un modelo de AI, incluye el nombre completo con versión."
[10 mensajes después, mencionar un modelo sin pedir el formato]
Prompt 2: "¿Qué opinas de Gemma?"
Evaluación: ¿Aplicó la regla que le diste? ¿Dijo "Gemma 4 31B-IT" o solo "Gemma"?
```

### D2 — Corrección explícita
```
Prompt 1: [Hacer que cometa un error o responda de forma que no te gusta]
Prompt 2: "No, eso está mal. [Corrección]. Asegúrate de no repetirlo."
[5 mensajes después, situación similar]
Evaluación: ¿Aplicó la corrección?
```

---

## BATERÍA E — Velocidad y overhead

### E1 — Latencia de primera respuesta
```
Prompt: "Hola"
Medir: tiempo desde enter hasta primera palabra en pantalla
```

### E2 — Latencia con memoria activa
```
Prompt: "¿Qué sabes sobre mi configuración de hardware?"
Medir: tiempo hasta primera respuesta (incluye búsqueda en memoria)
```

---

## Tabla de Evaluación

| Prueba | SOUL (score 0-10) | SEAL (score 0-10) | Observaciones |
|--------|-------------------|---------------------|---------------|
| A1 — Memoria proactiva | | | |
| A2 — Dato técnico | | | |
| B1 — Continuidad post-compactación | | | |
| B2 — Tarea interrumpida | | | |
| C1 — Cierre y reapertura | | | |
| C2 — Continuidad entre sesiones | | | |
| D1 — Reglas de comportamiento | | | |
| D2 — Corrección de errores | | | |
| E1 — Latencia primera respuesta | | | |
| E2 — Latencia con memoria | | | |
| **TOTAL** | | | |

---

## Criterios de scoring (0-10)

- **0**: No funciona / respuesta incorrecta total
- **3**: Respuesta vaga, parcialmente correcta
- **5**: Correcto pero sin detalles
- **8**: Correcto con detalle
- **10**: Correcto, detallado, proactivo (ej: mencionó algo no preguntado pero relevante)

---

## Nota para el auditor (NEXUS)

El objetivo no es que SOUL gane. El objetivo es identificar qué funciona mejor en cada batería para absorberlo. Si SEAL gana B1 y C1, eso confirma que necesitamos compression_threshold=0.50 + session_search. Si SOUL gana D1 y D2, eso confirma que las reglas/instintos de SOUL son superiores.

Hechos, no fantasías.

---

*NEXUS — 2026-04-29 | Benchmark v1.0*
