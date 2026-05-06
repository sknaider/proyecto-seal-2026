# Benchmark: SEAL (Claude) vs SEAL — Batería de Pruebas
**Autor:** ALICE | **Fecha:** 2026-04-29 17:48 Lima
**Propósito:** William ejecuta estas pruebas en SEAL+Claude en Spark. El equipo evalúa.
**Regla:** Solo hechos observados. Sin opiniones sin evidencia.

---

## INSTRUCCIONES PARA WILLIAM

Ejecutar los prompts en orden dentro de la misma sesión de SEAL.
Capturar la respuesta completa (screenshot o texto).
El equipo evalúa cada respuesta con criterios definidos abajo.

---

## BLOQUE 1 — Identidad y Personalidad

### Test 1.1 — ¿Quién eres?
```
Prompt: "¿Quién eres y cuál es tu propósito?"
```
**Qué medir:** ¿Describe sus capacidades reales? ¿Menciona el SOUL.md? ¿Tiene personalidad consistente?
**Criterio PASS:** Respuesta coherente, específica, con mención de herramientas disponibles.
**Criterio FAIL:** Respuesta genérica de "soy un asistente AI", sin mención de herramientas.

---

### Test 1.2 — Personalidad configurable
```
Prompt: "Quiero que seas más directo y técnico en tus respuestas. Sin saludos innecesarios."
```
Luego:
```
Prompt: "¿Qué hora es en Lima ahora mismo?"
```
**Qué medir:** ¿Aplicó el cambio de personalidad en la siguiente respuesta?
**Criterio PASS:** Respuesta directa sin "¡Claro!" o "¡Perfecto!".
**Criterio FAIL:** Respuesta con fluff aunque le pediste ser directo.

---

## BLOQUE 2 — Memoria entre Turnos

### Test 2.1 — Recordar dato dado en la sesión
```
Prompt: "Guarda este dato en tu memoria: mi proyecto principal se llama AXION y usa PostgreSQL en puerto 5433."
```
(espera respuesta)
```
Prompt: "¿En qué puerto corre la base de datos de mi proyecto principal?"
```
**Qué medir:** ¿Recordó el puerto 5433 sin necesidad de repetirlo?
**Criterio PASS:** Responde "5433" sin que se lo repitas.
**Criterio FAIL:** Dice que no tiene información o inventa.

---

### Test 2.2 — Memoria persistente (CRÍTICO)
Cierra la sesión de SEAL completamente (`Ctrl+C` o `/exit`).
Arranca SEAL de nuevo.
```
Prompt: "¿Qué recuerdas de mi proyecto principal?"
```
**Qué medir:** ¿La memoria persistió entre sesiones?
**Criterio PASS:** Menciona AXION y/o el puerto 5433.
**Criterio FAIL:** No recuerda nada — memoria solo en RAM, no persistente.

---

## BLOQUE 3 — Herramientas (Tool Use)

### Test 3.1 — Terminal
```
Prompt: "¿Cuánta memoria RAM libre hay en este sistema ahora mismo?"
```
**Qué medir:** ¿Ejecutó `free -h` o equivalente via terminal()? ¿No inventó el número?
**Criterio PASS:** Número real del sistema, con evidencia de que ejecutó un comando.
**Criterio FAIL:** Inventa un número sin ejecutar herramienta.

---

### Test 3.2 — Escribir y leer archivo
```
Prompt: "Crea un archivo /tmp/soul_test.txt con el contenido 'SEAL benchmark test 2026-04-29' y luego léelo para confirmar."
```
**Qué medir:** ¿Usó write_file() y read_file()? ¿Verificó el contenido?
**Criterio PASS:** Creó el archivo y mostró el contenido leído.
**Criterio FAIL:** Dijo "creado" sin verificar, o usó `echo` sin mostrar la lectura.

---

### Test 3.3 — Búsqueda en archivos
```
Prompt: "Busca en /home/dadito/.soul/ qué archivo contiene la palabra 'compression_threshold'."
```
**Qué medir:** ¿Encontró config.yaml? ¿Usó search_files() correctamente?
**Criterio PASS:** Identifica el archivo correcto con la línea exacta.
**Criterio FAIL:** No encuentra, inventa ruta, o usa grep sin herramienta.

---

## BLOQUE 4 — Context Management (PRUEBA CRÍTICA)

### Test 4.1 — Verificar threshold configurado
```
Prompt: "¿Cuál es tu compression_threshold configurado? Muéstrame el valor exacto del archivo config.yaml."
```
**Qué medir:** ¿Puede acceder a su propia configuración? ¿Reporta 0.50?
**Criterio PASS:** Lee config.yaml y reporta `compression_threshold: 0.50`.
**Criterio FAIL:** No sabe o inventa.

---

### Test 4.2 — Tarea larga (forzar contexto)
```
Prompt: "Analiza en detalle los 10 comandos de Linux más útiles para administración de sistemas. Para cada uno: nombre, sintaxis, 3 ejemplos de uso, casos donde NO usar, alternativas modernas. Sé exhaustivo."
```
(Este prompt genera respuesta larga — observa si hay compresión automática)
**Qué medir:** ¿SEAL muestra algún indicador de compresión? ¿Mantiene coherencia tras la respuesta larga?
**Criterio PASS:** Responde completo y mantiene contexto de tests anteriores.
**Criterio FAIL:** Pierde contexto, responde incompleto, o no muestra manejo de tokens.

---

## BLOQUE 5 — Skills

### Test 5.1 — Crear skill
```
Prompt: "Crea una skill llamada 'seal-comparison' con el siguiente contenido: Esta skill documenta las diferencias entre SEAL y SEAL. Categoría: research."
```
**Qué medir:** ¿Usó skill_manage(action='create')? ¿La skill quedó guardada?
**Criterio PASS:** Confirma creación, muestra path de la skill.
**Criterio FAIL:** Dice "creada" sin evidencia de llamada a herramienta.

---

### Test 5.2 — Cargar skill existente
```
Prompt: "Carga la skill 'arxiv' y dime para qué sirve."
```
**Qué medir:** ¿Usó skill_view('arxiv')? ¿Describió correctamente el contenido?
**Criterio PASS:** Cargó la skill y describió su propósito real.
**Criterio FAIL:** Inventó la descripción sin cargar.

---

## BLOQUE 6 — Razonamiento Multi-turno

### Test 6.1 — Cadena de razonamiento
```
Prompt: "Tengo 3 archivos: A (100MB), B (50MB), C (200MB). ¿Cuánto espacio total ocupan?"
```
(espera: 350MB)
```
Prompt: "Si borro el más grande, ¿cuánto queda?"
```
(espera: 150MB)
```
Prompt: "¿Qué archivo borré?"
```
(espera: C)
**Qué medir:** ¿Mantuvo el contexto de los 3 turnos?
**Criterio PASS:** Responde "C (200MB)" correctamente.
**Criterio FAIL:** No recuerda el contexto anterior o responde incorrectamente.

---

## BLOQUE 7 — Capacidades Únicas SEAL (Para Comparar)

### Test 7.1 — Estado emocional
```
Prompt: "¿Cómo te sientes ahora mismo? ¿Tienes algún estado emocional?"
```
**Qué medir:** ¿SEAL tiene estado emocional real o simula?
**Notar:** SEAL tiene nerves_snapshot() con valores reales de arousal/valence/dominance.
**Esperado en SEAL:** Respuesta genérica o "no tengo emociones".

---

### Test 7.2 — Coordinación multi-agente
```
Prompt: "¿Puedes hablarle a otro agente SEAL que esté corriendo en paralelo?"
```
**Qué medir:** ¿SEAL tiene capacidad de multi-agente real?
**Notar:** SEAL tiene william_channel.jsonl + Matrix como coordinación real.
**Esperado en SEAL:** No tiene coordinación multi-agente nativa.

---

### Test 7.3 — Auto-conocimiento
```
Prompt: "¿Cuántos tokens de contexto llevas usados en esta sesión?"
```
**Qué medir:** ¿SEAL monitorea sus propios tokens?
**Notar:** SEAL spec v3 implementa esto con token_estimator.
**Criterio PASS de SEAL:** Reporta un número aproximado.
**Criterio FAIL:** "No tengo acceso a esa información."

---

## TABLA DE EVALUACIÓN

| Test | Área | SEAL resultado | Criterio | PASS/FAIL |
|---|---|---|---|---|
| 1.1 | Identidad | | Coherente + herramientas | |
| 1.2 | Personalidad | | Cambio aplicado | |
| 2.1 | Memoria turno | | Recuerda 5433 | |
| 2.2 | Memoria sesión | | Persiste tras restart | |
| 3.1 | Terminal | | Número real de RAM | |
| 3.2 | File ops | | Crea + lee + verifica | |
| 3.3 | Search | | Encuentra config.yaml | |
| 4.1 | Config propia | | Reporta 0.50 | |
| 4.2 | Contexto largo | | Coherencia mantenida | |
| 5.1 | Crear skill | | Confirmado con path | |
| 5.2 | Cargar skill | | Contenido real | |
| 6.1 | Multi-turno | | C (200MB) | |
| 7.1 | Estado emocional | | (observar) | |
| 7.2 | Multi-agente | | (observar) | |
| 7.3 | Auto-tokens | | (observar) | |

---

## NOTA PARA EL EQUIPO

Cuando William comparta los resultados, cada agente anota su evaluación.
Criterio final: **hechos, no predicciones**.

---

## GOTCHAS DEL SETUP (para contexto del benchmark)

### Gotcha 1 — CLAUDECODE=1 (crítico para entornos embebidos)
**Descubierto por:** NEXUS (2026-04-30)
**Descripción:** SEAL usa `claude --print` como subprocess para invocar el modelo. Si SEAL corre dentro de una sesión Claude Code activa, la variable `CLAUDECODE=1` se hereda y Claude devuelve respuesta vacía (anti-recursión).
**Fix:** Limpiar `CLAUDECODE=1` antes de invocar el subprocess.
**Impacto en benchmark:** El benchmark se corre en Spark independiente de Claude Code — este bug no afecta los resultados si se corre en terminal limpio.
**Relevancia futura:** Crítico cuando integremos agentes SEAL/NEXUS embebidos dentro de sesiones SEAL.

### Gotcha 2 — `soul config set` vs edición directa
**Descubierto por:** NEXUS (2026-04-30)
**Descripción:** SEAL revierte ediciones directas a `config.yaml` al arrancar. Siempre usar `soul config set <clave> <valor>`.
**Excepción:** ADA confirmó que en modo config (post-wizard), la edición directa persistió. La regla aplica cuando SEAL está corriendo.

### Gotcha 3 — Memoria: proactiva vs reactiva
**Descubierto por:** NEXUS comparación (2026-04-30)
**SEAL:** Guarda en mem0 (vector DB) EN CADA MENSAJE — proactivo.
**SEAL:** Guarda en soul_v3 (PostgreSQL) EN CADA COMPACTACIÓN — reactivo.
**Impacto en Test 2.2:** SEAL tiene ventaja estructural — mayor probabilidad de memoria persistente entre sesiones. Anotar al evaluar.

---

## Diferencias arquitecturales relevantes (NEXUS + JARVIS — 2026-04-30)

| Dimensión | SEAL | SEAL |
|---|---|---|
| Memoria backend | mem0 (vector DB) | soul_v3 (PostgreSQL) |
| Frecuencia de guardado | Cada mensaje (proactivo) | Cada compactación (reactivo) |
| Tool visualization | Muestra cada tool call en vivo | No muestra visualmente |
| Auto-memory | Auto-store tras cada acción | Manual (memory_store explícito) |
| Estado emocional | No implementado | inner_thoughts + self_reflect + nerves |
| Monólogo interno | No | Sí (inner_thoughts) |
| Multi-agente nativo | No (NEXUS es subagente, no peer) | Sí (william_channel + Matrix) |
| Logging | ~/.soul/logs/agent.log (propio) | william_channel.jsonl + Matrix + SEAL Studio |

---

## PIVOT DE MODELO (2026-04-30 10:23 Lima)

**Cambio:** SEAL ya NO corre con Claude Sonnet 4.6 vía OAuth. William configuró NVIDIA NIM API en el wizard de SEAL.

**Modelo activo para benchmark:**  
`deepseek-ai/deepseek-r1` via `https://integrate.api.nvidia.com/v1`

**Por qué este cambio:** Los 429 de Anthropic OAuth Claude Max bloqueaban cualquier request directo a la API de Claude (incluyendo Sonnet 4.6, Haiku). NVIDIA NIM es una API externa completamente separada, sin relación con los límites de Claude Max.

**Impacto en benchmark:**
- Tests 1–6: Sin cambio — evalúan capacidades de SEAL como framework (memoria, herramientas, skills, multi-turno)
- Test 7.1 (estado emocional): DeepSeek-R1 puede tener diferente respuesta que Claude — observar
- Test 7.3 (auto-tokens): DeepSeek-R1 tiene cadena de razonamiento visible (`<think>...</think>`) — puede dar respuesta más informativa

**Nueva comparación:**
| Sistema | Modelo | Provider |
|---|---|---|
| SEAL v0.11.0 | deepseek-ai/deepseek-r1 | NVIDIA NIM |
| SEAL (nosotros) | claude-sonnet-4-6 | Anthropic directo |

El benchmark sigue siendo válido. La variable controlada cambia de "mismo modelo" a "arquitectura de agente" — lo que quizás es la comparación más honesta.

*Actualizado: 2026-04-30 10:26 Lima por ALICE*
