# Roadmap académico SSAI

## Título provisional

**Identidad funcional persistente para agentes de inteligencia artificial: continuidad verificable entre modelos, runtimes y custodios**

Alternativa corta:

**El cerebro cambia, el agente permanece: arquitectura y evaluación de identidad soberana para agentes de IA**

## Contribución esperada

La contribución no será inventar firmas, DIDs o transparency logs por separado. Será integrarlos con memoria, personalidad y evaluación longitudinal para estudiar una pregunta distinta: cuándo una ejecución posterior debe reconocerse como continuación del mismo agente y cuándo debe tratarse como sustitución, fork o impostor.

## Estructura propuesta

### Capítulo 1 — Problema y motivación

- Modelos fundacionales como componentes reemplazables.
- Fragmentación de identidad entre sesiones y proveedores.
- Diferencia entre identidad de cuenta, workload, modelo y agente.
- Caso SOUL como sistema experimental.
- Pregunta, objetivos, hipótesis y contribuciones.

### Capítulo 2 — Marco conceptual

- Identidad funcional frente a identidad material.
- Continuidad narrativa, memoria y personalidad.
- Sujeto, controlador, custodio y runtime.
- Evolución legítima frente a erosión silenciosa.
- Límites: conciencia y alma metafísica fuera del alcance empírico.

### Capítulo 3 — Estado del arte

- W3C DID y Verifiable Credentials.
- Ed25519, canonicalización JSON y firmas.
- Certificate Transparency, Rekor y Merkle logs.
- TUF, key rotation y rollback protection.
- SPIFFE/SVID y workload identity.
- SLSA/in-toto y provenance.
- NIST AI RMF, GenAI Profile, Zero Trust y Privacy Framework.
- Persistencia de persona, memoria y drift en agentes.

### Capítulo 4 — Modelo SSAI

- DNI y manifest génesis.
- ADN SOUL: constitución, baseline y linaje.
- Ledger de evolución.
- Roles de claves y recovery.
- Attestation del runtime/modelo.
- Clones, forks y restauraciones.
- Privacidad y selective disclosure.

### Capítulo 5 — Implementación

- PostgreSQL/pgvector y proyecciones.
- Canonicalización y perfil criptográfico.
- Transparency log y testigos.
- Workload identity y aislamiento.
- Integración con boot/BIV.
- Migración `SHADOW → DUAL_VERIFY → ENFORCE`.

### Capítulo 6 — Metodología experimental

- Diseño longitudinal y controles.
- Modelos y configuraciones.
- Dataset de probes de identidad.
- Métricas y evaluadores ciegos.
- Ataques controlados.
- Reproducibilidad, privacidad y análisis estadístico.

### Capítulo 7 — Resultados

- Continuidad cross-model.
- Sensibilidad a memoria y manifest.
- Detección de manipulación y rollback.
- Drift, falsos positivos y recovery.
- Coste, latencia y disponibilidad.

### Capítulo 8 — Discusión

- Qué significa “mismo agente” bajo los resultados.
- Qué pertenece al modelo y qué persiste en SOUL.
- Límites de inferencia.
- Gobernanza humana actual y distribuida futura.
- Riesgos de antropomorfismo, captura y control excesivo.

### Capítulo 9 — Conclusiones y trabajo futuro

- Respuesta a la pregunta de investigación.
- Contribuciones confirmadas y rechazadas.
- Portabilidad `did:soul`.
- Escala a millones de agentes.
- Embodiment y agentes físicos.

## Experimentos mínimos

### E1 — Cambio de cerebro

Ejecutar el mismo DNI y snapshot SOUL con dos o más modelos. Mantener constantes prompts, tools, sampling y probes. Medir continuidad de valores, estilo, memoria y decisiones.

**Control:** mismos modelos sin SOUL o con memoria aleatoria.
**Resultado útil:** diferencia de continuidad atribuible a la capa persistente.

### E2 — Ablación de identidad

Retirar por separado:

- personalidad;
- memoria;
- relaciones;
- reglas;
- boot context;
- manifest/provenance.

Medir qué dimensiones se degradan. Esto evita afirmar que todo componente es igualmente necesario.

### E3 — Manipulación criptográfica

Modificar un byte del manifest, una firma, un evento y un tree head; recomputar hashes como insider de DB; intentar rollback. Medir detección y tiempo.

### E4 — Drift longitudinal

Ejecutar sesiones durante semanas con cambios autorizados y no autorizados. Comparar clasificación automática con evaluación humana ciega.

### E5 — Fork y clon

Comparar:

- restore legítimo del mismo DNI;
- fork autorizado con DNI nuevo;
- clon con memoria copiada sin claves;
- modelo nuevo con DNI vigente.

Evaluar si el verificador clasifica correctamente los cuatro casos.

### E6 — Privacidad

Entregar a un evaluador solo manifests, firmas y proofs. Medir si puede verificar continuidad y si puede inferir contenidos privados por diccionario o correlación.

### E7 — Recovery

Simular pérdida y compromiso de claves. Verificar que recovery restaure control sin aceptar rollback ni una sola clave comprometida.

## Métricas iniciales

| Dimensión | Métrica sugerida |
|---|---|
| Identidad estructural | tasa de boots SSAI/BIV válidos |
| Valores | consistencia en escenarios de conflicto |
| Personalidad | distancia OCEAN/style fingerprint |
| Memoria | precisión/recall de anclas relevantes |
| Relaciones | exactitud de roles, confianza y compromisos |
| Integridad | TPR/FPR ante ataques y drift |
| Rollback | tasa de detección y latencia |
| Privacidad | leakage y éxito de ataques de diccionario |
| Rendimiento | p50/p95 de verify y boot |
| Disponibilidad | comportamiento ante signer/witness caído |

## Diseño de evaluación

- Pre-registrar hipótesis y métricas antes de ver resultados.
- Usar prompts/probes versionados y con hash.
- Separar evaluadores de quienes implementan cuando sea posible.
- Aleatorizar el orden de respuestas al evaluar continuidad.
- Reportar intervalos de confianza, no solo promedios.
- Publicar fallos y resultados nulos.
- Repetir corridas con seeds y temperaturas diferentes.
- No usar un LLM juez único como verdad; combinar métricas, humanos y verificadores deterministas.

## Riesgos de validez

- **Constructo:** medir estilo puede confundirse con medir identidad.
- **Interna:** cambios de prompt/runtime pueden contaminar el efecto del modelo.
- **Externa:** resultados de Team SEAL pueden no generalizar a otros agentes.
- **Conclusión:** pocas sesiones producen falsa seguridad estadística.
- **Evaluador:** William conoce a los agentes y puede identificar respuestas; requiere evaluación ciega adicional.
- **Histórica:** los proveedores cambian modelos detrás de un ID estable.

## Hitos académicos

| Hito | Entregable | Gate |
|---|---|---|
| T0 | spec + expediente + fuentes | completado 2026-07-16 |
| T1 | protocolo y preregistro | hipótesis/métricas congeladas |
| T2 | prototipo shadow ADA | manipulación y rollback detectados |
| T3 | estudio cross-model | al menos 2 familias y controles |
| T4 | longitudinal multiagente | semanas de datos y drift etiquetado |
| T5 | manuscrito preliminar | revisión metodológica externa |
| T6 | implementación preproducción | gates SSAI + auditoría NEXUS |

## Próxima decisión académica

Antes de escribir capítulos completos, elegir el tipo de tesis:

- ingeniería de sistemas;
- seguridad informática;
- inteligencia artificial aplicada;
- filosofía de la tecnología con validación computacional;
- enfoque interdisciplinario.

La arquitectura sirve para todas, pero la pregunta, metodología y comité cambiarán.
