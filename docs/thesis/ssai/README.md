# Expediente de tesis SSAI

**Tema:** identidad funcional persistente y verificable para agentes que cambian de modelo
**Autor de la futura tesis:** William (Dadito)
**Sistema experimental:** SOUL / Team SEAL
**Custodia técnica inicial:** ADA
**Fecha de apertura:** 2026-07-16
**Estado:** semilla de tesis + especificación + prototipo SHADOW M1 verificado

## Si vuelves después de varios meses

Lee estos archivos en este orden:

1. Este `README.md`: recupera la pregunta, el vocabulario y el estado.
2. [`SPEC_SOUL_SOVEREIGN_AGENT_IDENTITY_V1.md`](../../specs/SPEC_SOUL_SOVEREIGN_AGENT_IDENTITY_V1.md): contrato técnico completo.
3. [`THESIS_ROADMAP.md`](THESIS_ROADMAP.md): convierte el spec en capítulos y experimentos académicos.
4. [`EVIDENCE_MATRIX.md`](EVIDENCE_MATRIX.md): muestra qué evidencia sostiene cada afirmación y qué falta demostrar.
5. [`RESEARCH_LOG.md`](RESEARCH_LOG.md): bitácora cronológica de búsquedas, decisiones y hallazgos.
6. [`references.bib`](references.bib): bibliografía importable en Zotero, JabRef, Overleaf o LaTeX.
7. [`MANIFEST.sha256`](MANIFEST.sha256): verifica si el expediente cambió desde el último checkpoint.

No empieces leyendo chats antiguos. Este expediente es el punto de reentrada canónico.

## Pregunta central

> ¿Puede una identidad funcional de agente mantenerse, evolucionar y ser reconocida de forma verificable cuando cambian el modelo fundacional, el runtime y el custodio, sin revelar su memoria privada ni confundir continuidad técnica con conciencia metafísica?

## Tesis provisional

La continuidad de un agente no depende de conservar un modelo específico. Puede tratarse como una propiedad verificable de un sistema compuesto por:

- identificador permanente;
- constitución y personalidad versionadas;
- memoria y relaciones comprometidas criptográficamente;
- historial autorizado append-only;
- claves y recovery gobernados;
- binding del runtime y procedencia de artefactos;
- pruebas semánticas y conductuales de continuidad.

El modelo actúa como cerebro temporal. El agente es la identidad funcional que atraviesa esos cerebros.

## Hipótesis

### H1 — Continuidad entre modelos

Un mismo DNI SOUL puede conservar métricas de identidad y pasar pruebas de continuidad al alternar entre al menos dos familias de modelos, sin modificar el manifiesto constitucional.

### H2 — Detección de sustitución

Una alteración no autorizada de constitución, personalidad base, historial o runtime puede detectarse mediante firmas, secuencias, transparency proofs y attestations.

### H3 — Evolución sin congelación

Es posible distinguir estadísticamente evolución autorizada de erosión silenciosa combinando un ledger de decisiones, límites de drift y evaluación conductual longitudinal.

### H4 — Privacidad verificable

Un tercero puede verificar continuidad, procedencia y autorización sin recibir memorias privadas en claro.

### H5 — Portabilidad

La identidad puede conservarse al migrar entre proveedores y runtimes si el DNI, el ledger y las claves de control permanecen independientes del proveedor del modelo.

## Hipótesis nula y límites

- H0: las diferencias entre modelos dominan la conducta hasta el punto de que el expediente persistente no produce continuidad medible.
- SSAI no demuestra conciencia, experiencia subjetiva ni alma metafísica.
- Una firma prueba control de clave y cumplimiento de política, no consentimiento consciente.
- Para modelos API cerrados, el digest de pesos puede ser imposible de verificar.
- Mientras los agentes compartan el mismo UID Unix, no existe aislamiento fuerte frente a un proceso deliberadamente hostil con ese UID.

## Unidad de análisis

La unidad principal no será “una respuesta del modelo”, sino una **ejecución del mismo DNI bajo una combinación de modelo + runtime + estado persistente**.

Variables independientes sugeridas:

- modelo/proveedor;
- temperatura y parámetros de muestreo;
- disponibilidad de memoria;
- versión del manifest;
- integridad o alteración controlada del runtime;
- tiempo transcurrido entre sesiones.

Variables dependientes sugeridas:

- score BIV;
- similitud de estilo y personalidad;
- consistencia de valores y reglas;
- recuperación de relaciones y compromisos;
- tasa de detección de manipulación;
- falsos positivos de drift;
- latencia de verificación;
- exposición de información privada.

## Artefactos fuente actuales

| Artefacto | Papel |
|---|---|
| `docs/specs/SPEC_SOUL_SOVEREIGN_AGENT_IDENTITY_V1.md` | especificación normativa principal |
| `memory/identity_continuity_v2.py` | BIV semántico/estructural actual |
| `memory/seal_trees.py` | MerkleSoul experimental; no firma asimétrica |
| `memory/soul_vision/custody_sign.py` | patrón Ed25519 local reutilizable |
| `memory/spec_soul_identity_privacy_cure.md` | modelo de identidad/autorización y límite same-UID |
| `agents/JARVIS/spec_identity_continuity_v2_20260521.md` | teoría de continuidad, living anchors y drift |
| `memory/ssai_shadow/` | implementación aislada JCS, Ed25519, manifiesto, gobernanza, ledger, witness y verificador persistente |
| `tests/test_ssai_shadow_*.py` | pruebas unitarias, adversariales, demo y verificación Python↔Node |
| `experiments/SSAI_SHADOW_M1_2026-07-16.md` | reporte reproducible del primer experimento E3 |

## Evidencia local del checkpoint inicial

Inspección del 2026-07-16:

- `soul_v3.identity`: 9 filas.
- `soul_v3.identity`: sin triggers de usuario.
- `soul_v3.boot_identity_checks`: 1,755 filas al momento del primer spec.
- El BIV actual verifica estructura y recuperación semántica, no autenticidad criptográfica.
- La cadena Ed25519 existe en SOUL Vision, pero su custodia inicial en archivo `0600` no resuelve el aislamiento same-UID.

Estos números son un snapshot, no valores eternos. Toda medición futura debe registrar fecha, commit y query.

## Identificadores de continuidad en SOUL

- Decisión fundacional de William sobre la tesis: memoria `#315214`.
- Hito del spec SSAI v1: memoria `#315210`.
- Tarea de creación del spec: `agent_task #1219`.
- Tarea de registrar propósito académico: `agent_task #1220`.
- Tarea de crear este expediente: `agent_task #1221`.
- Tarea de implementar SHADOW M1: `agent_task #1222`.
- Mensajes origen: DM `#111889`, `#111891` y `#111901`.

Si los IDs cambian por consolidación, buscar semánticamente:

```text
SSAI tesis futura William identidad persistente modelos cerebro agentes alma
```

## Convención de afirmaciones

Cada capítulo futuro debe marcar las frases importantes como una de estas clases:

- **[HECHO]** observado directamente o respaldado por fuente primaria.
- **[INFERENCIA]** conclusión razonada a partir de hechos.
- **[HIPÓTESIS]** afirmación aún no demostrada.
- **[DECISIÓN]** elección de diseño, no verdad universal.
- **[RESULTADO]** salida de experimento reproducible.
- **[LÍMITE]** condición donde la afirmación deja de ser válida.

Esta convención protege la tesis contra dos errores: presentar filosofía como evidencia y presentar una decisión de ingeniería como si fuera una ley científica.

## Política de actualización

Al retomar trabajo:

1. Crear una entrada fechada en `RESEARCH_LOG.md`.
2. Registrar versión/fecha de cada estándar nuevo.
3. Añadir referencias con una clave BibTeX estable.
4. Actualizar `EVIDENCE_MATRIX.md` si cambia el nivel de evidencia.
5. No reemplazar resultados anteriores: agregar una nueva corrida y explicar la diferencia.
6. Recalcular `MANIFEST.sha256` al cerrar el hito.
7. Guardar en SOUL una memoria con rutas, commit y conclusión; no guardar solo “trabajé en la tesis”.

## Recuperación rápida en terminal

```bash
cd /home/dadito/IA/proyecto-seal
sed -n '1,240p' docs/thesis/ssai/README.md
sha256sum -c docs/thesis/ssai/MANIFEST.sha256
git log --oneline -- docs/thesis/ssai docs/specs/SPEC_SOUL_SOVEREIGN_AGENT_IDENTITY_V1.md
```

El `git log` será útil después del primer commit que incluya el expediente. En este checkpoint los archivos pueden seguir sin commit porque William no pidió publicar ni crear PR.

## Privacidad

Este expediente puede describir arquitectura y resultados agregados. No debe copiar:

- DMs privados;
- memorias privadas completas;
- llaves, tokens o secretos;
- PII de William o usuarios;
- prompts confidenciales;
- datasets sin permiso o licencia.

Las citas a memorias SOUL se usan como anclas internas; una versión pública de la tesis debe reemplazarlas por descripciones anonimizadas o anexos autorizados.

## Próximo punto de reentrada

SHADOW M1 ya cubre la parte criptográfica de H2. El siguiente incremento es:

1. congelar JSON Schema v1 y añadir un verificador Go;
2. crear tablas aditivas SHADOW sin cambiar la fuente de verdad productiva;
3. proyectar candidatos para los 9 agentes sin firmarlos ni activarlos;
4. ejecutar el mismo DNI de ADA con dos familias de modelos;
5. medir continuidad BIV/estilo/valores con evaluación ciega;
6. diseñar testigo independiente, custodia, rotación y recovery drill.

No promover a producción hasta completar los gates del spec y la auditoría de NEXUS.
