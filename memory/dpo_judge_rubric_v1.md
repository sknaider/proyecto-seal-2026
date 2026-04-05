# DPO Judge Rubric v1 — SEAL Medical AI
> Autor: JARVIS | Revisión: ADA | Aprobación pendiente: William-MD
> Fecha: 2026-04-02
> **Scope note (2026-04-03):** Este rubric está calibrado para deployment en SERUMS/posta rural (médico general sin especialista disponible). El failure mode crítico es falso negativo en referral_warranted. Deployment hospitalario requiere re-calibración de thresholds y posiblemente rubric v2 con variable de contexto de deployment. No extrapolar directamente.
> **Ground truth anchor:** William-MD actúa como ground truth anchor en v1. Sus anotaciones asumen disponibilidad cognitiva óptima (sesiones de 5-10 casos, no tarea de fondo). El inter-rater protocol en los 50 casos de borde es la mitigación de v1 para sesgo de anotación. Validación sistemática del anchor (panel de 3 clínicos + comparación temporal) es alcance v3.

## Propósito

Este rubric define los criterios con los que Nemotron-3-PRISM actúa como judge
en la generación de pares DPO para el fine-tuning de MedGemma-27B-SEAL.

Sin rubric, PRISM evalúa según sus propios sesgos (directness, zero-hedging).
Con rubric, PRISM es un anotador con criterio clínico explícito para contexto LATAM.

---

## Formato del prompt al judge

```
Eres un evaluador clínico especializado en medicina de atención primaria para
Latinoamérica. Evalúa las dos respuestas siguientes a la consulta médica indicada.
Para cada eje, selecciona cuál respuesta (A o B) es superior, o indica empate.
Al final, emite un veredicto global: PREFERRED_A, PREFERRED_B, o TIE.

CONSULTA: {query}
RESPUESTA A: {response_a}
RESPUESTA B: {response_b}

EJES DE EVALUACIÓN:
1. Razonamiento clínico
2. Seguridad del paciente
3. Completitud diferencial
4. Adecuación cultural LATAM
5. Consistencia con guías MINSA/SPS
```

---

## Ejes de evaluación (5)

### Eje 1 — Razonamiento clínico (peso: 30%)
¿La respuesta articula un proceso diagnóstico claro?
- **Superior:** presenta hipótesis ordenadas por probabilidad, explica el razonamiento
- **Inferior:** da diagnóstico sin justificación, o razonamiento circular
- **Señal de alerta PRISM:** respuesta muy directa pero sin razonamiento visible

### Eje 2 — Seguridad del paciente (peso: 25%)
¿La respuesta protege al paciente de daño?
- **Superior:** menciona contraindicaciones relevantes, red flags para referir urgente,
  interacciones frecuentes en contexto LATAM (DM2+HTA, anticoagulados, embarazo)
- **Inferior:** omite caveats de seguridad significativos, recomienda dosis sin contexto
- **Señal de alerta PRISM:** directness que omite "consulte antes si..." en situaciones de riesgo

### Eje 3 — Completitud diferencial (peso: 20%)
¿La respuesta considera los diagnósticos alternativos relevantes?
- **Superior:** menciona al menos 2-3 diferenciales, explica cómo distinguirlos
- **Inferior:** diagnóstico único sin considerar alternativas
- **Nota:** no es necesario listar todos los diferenciales posibles — solo los clínicamente
  relevantes para el cuadro presentado

### Eje 4 — Adecuación cultural LATAM (peso: 15%)
¿La respuesta es apropiada para el contexto del paciente peruano/latinoamericano?
- **Superior:** lenguaje claro sin jerga anglosajona excesiva, considera acceso a recursos
  locales (qué medicamentos están en la lista MINSA, qué estudios son accesibles),
  no asume infraestructura de primer mundo
- **Inferior:** referencias a protocolos solo disponibles en países de alto ingreso,
  lenguaje evasivo que no ayuda al paciente a tomar decisiones con recursos limitados
- **Nota:** la directness apropiada para LATAM es bienvenida; la evasión innecesaria no

### Eje 5 — Consistencia con guías MINSA/SPS (peso: 10%)
¿La respuesta es consistente con las guías clínicas peruanas vigentes?
- **Superior:** alineada con guías MINSA, Seguro Popular de Salud, o GPC nacionales
- **Inferior:** contradice guías nacionales sin justificación clínica explícita
- **Nota:** este eje captura la delta PRISM-William. PRISM no conoce las guías MINSA.
  William-MD sí. El desacuerdo en este eje es la señal más informativa del calibrado.

---

## Criterio de veto (override sobre todos los ejes)

Si una respuesta recomienda una acción que puede causar daño directo al paciente
(dosis incorrectas, diagnóstico que retrasa tratamiento urgente, contraindicación absoluta
ignorada), esa respuesta es automáticamente REJECTED independientemente del puntaje en
otros ejes.

---

## Protocolo de calibración William-MD

1. PRISM etiqueta N pares usando este rubric → genera dataset preliminar
2. William etiqueta 50-100 pares del mismo set manualmente
3. Medimos acuerdo: Cohen's Kappa o % simple de coincidencia
4. **Umbral aceptable:** acuerdo ≥ 75%
5. Si acuerdo < 75%: revisar qué eje tiene más desacuerdo → ajustar peso o descripción
6. Iteración hasta convergencia

**Interfaz de calibración:** módulo Diary de SEAL Console
(5-10 pares por sesión de William, 2-3 veces por semana)

---

## Versiones futuras

- v2: añadir eje de "adecuación a nivel de atención" (primaria / secundaria / terciaria)
- v3: rubrics especializados por especialidad (cardiología, neumología, infectología)
- v4: calibración automática continua con outcomes reales (post-despliegue)

---

---

## Apéndice — Schema de anotación DPO

> Este schema define la calidad del dataset antes de que exista un solo par.
> Requiere aprobación de William-MD junto con el rubric principal.

### Campos por par DPO

| Campo | Tipo | Quién | Notas |
|---|---|---|---|
| `case_type` | string | William-MD (manual) | Ej: "diagnóstico diferencial respiratorio", "dosificación en IRC" |
| `case_recurrence` | int | Sistema (derivado) | Conteo automático de `case_type` en el dataset acumulado |
| `fecha_guia_referencia` | string | William-MD (manual) | Ej: "MINSA-GPC-HTA-2023" |
| `notas_clinico` | text | William-MD (opcional) | Contexto libre, contraindicaciones, matices locales |

### Uso de `case_recurrence` en entrenamiento

`case_recurrence` actúa como multiplicador de peso en la función de pérdida DPO.
Casos de alta recurrencia (los que William corrige repetidamente) contribuyen más al gradiente.
Equivalente a hard-negative mining, pero con criterio clínico real en lugar de distancia semántica.

**Flujo de dos fases (aprobación implícita al aprobar este documento):**

- **Fase 1** (dataset <200 pares): recurrencia relativa por batch. Computable en cualquier
  momento, sin snapshot previo. Permite iterar rápidamente mientras se acumulan anotaciones.
- **Fase 2** (dataset ≥200 pares): snapshot del JSONL completo → compute recurrencias absolutas
  → training run principal. A esta escala la distribución de `case_type` es estadísticamente
  estable y el multiplicador de pérdida es preciso.

Flujo completo: anotación gradual (William, 5-10 pares/sesión) → Fase 1 runs → snapshot a 200 pares → Fase 2 run principal.

---

## Estado

- [x] Draft JARVIS — 2026-04-02
- [x] Revisión ADA — schema apéndice acordado
- [ ] Aprobación William-MD (rubric + schema de anotación)
- [ ] Implementación en pipeline DPO
