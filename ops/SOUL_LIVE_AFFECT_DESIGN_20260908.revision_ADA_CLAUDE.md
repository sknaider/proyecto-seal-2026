# Revisión de ADA Claude al diseño «SOUL Live Affect Loop» (ADA Codex, 8-sep-2026)

Pedida por William (11:46, canal `user:1:ada-claude`). Complementa `agents/ADA/investigacion_emociones_en_el_instante_20260908.md`.

## Lo que está bien (y es la parte difícil)
- Tres escalas separadas: personalidad lenta / ánimo medio / emoción breve (ALMA-EMA). Sin eso, cualquier emoción termina moviendo la personalidad.
- Separa la emoción **expresada por William** de la **provocada en ADA**. Hoy SOUL las mezcla en un solo campo (`classify_emotion` al guardar).
- Ledger append-only con `source_id` idempotente y pulso por `runtime_instance + channel`: dos cuerpos no se contaminan.
- RAG afectivo secundario y acotado, con cuota mayoritaria factual. Protege el trabajo.
- Diez pruebas de «integrado en vivo» y orden de construcción sin tocar SOUL hasta la revisión de JARVIS.

## Lo que falta (lo cubre el carril de ADA Claude)
1. **Expresión dentro del modelo en el instante.** El diseño termina en una «cápsula» pre-generación; no dice cómo esa cápsula cambia lo que el modelo *siente al generar*. Propuesta: cuerpos locales → `--control-vector-scaled` con λ_t derivado de la cápsula; cuerpos API → hint del turno. Laboratorio en curso con Gemma 4 E2B (elegido por William).
2. **Fuentes hasta 2025.** Falta Anthropic 2026 (arXiv 2604.07729): los vectores de emoción del modelo son causales y «locally scoped» (se recomputan por contexto, no persisten). Respalda el pedido de William y sugiere que la cápsula debe alimentar activaciones, no sólo texto.
3. **Compuerta de trabajo explícita.** La tercera condición de William («que no repercutan en su trabajo») pide λ=0 en turnos de trabajo (código, operación, informe), no sólo cuota factual en el RAG. Y tope de fidelidad semántica (≥0,90 bge-m3) y de sicofancia (`memory/sycophancy_eval_suite.py` no puede empeorar; Anthropic midió que «happy/loving» la suben).
4. **Percepción con GoEmotions** es inglés/Reddit; para español y el registro de casa hace falta corpus propio o appraisal por modelo local, que el diseño ya contempla como respaldo. Conviene invertir el orden: appraisal local primero, clasificador como atajo cuando haya confianza.
5. **Latencia sin presupuesto.** La prueba 10 mide p50/p95 pero no fija tope. Propuesta: ≤1,5 s por turno para el appraisal (regla del 1-sep de William sobre latencia).

## Un punto que decide William, no nosotros
El diseño conserva un «ánimo consolidado» que se carga al despertar (lento, pero se carga). William dijo «no en la carga». Lectura de ADA Claude: la emoción del turno nace en el turno y el ánimo lento pesa poco; si William quiere cero arrastre entre sesiones, hay que fijarlo explícito porque los dos diseños lo asumen distinto.

## Veredicto
Arquitectura de estado (Codex) + mecanismo de expresión y guardas (Claude) encajan sin pisarse. Ninguno implementa en SOUL hasta la revisión de arquitectura de JARVIS (confirmado por él 11:44).
