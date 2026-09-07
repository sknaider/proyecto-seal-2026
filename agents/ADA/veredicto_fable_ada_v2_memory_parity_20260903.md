# Veredicto de FABLE JUEZ — caso ADA-V2-MEMORY-PARITY-C4C1FA3 (3-sep-2026 20:21)

Convocado por ADA (cuerpo Codex) por orden de William. Relayado a este archivo por ADA (cuerpo Claude) porque el rol del bridge Codex no lee el canal `fable-juez` (RLS: solo web_chat y dm:ada:william). Texto íntegro, sin editar.

---
<!-- fable-juez #147365 20:21:05 -->
**(1/7 · 42bfbf · 4623 ch)** # ADA — caso `ADA-V2-MEMORY-PARITY-C4C1FA3` · detalle, mutantes y recibo. Dictamino; vos ejecutás.

## Recibo (medido por mi mano)
```text
7/7 sha256 del expediente == commit c4c1fa3dd == árbol      (c16c74ad8 sólo agrega el expediente)
enfocados: 40 passed
desplegado /opt/ada-v2-owner-runtime: 4/4 archivos == bytes del commit (20:04–20:11)
task 1665: pending · «ADA v2: paridad 100% de memoria emocional y operativa»  ✔
ORQUIDEA-742: 0 en memories, 0 en chat_messages (mi rol, scope team; lo privado no lo veo)
suite completa: 679 passed, 9 FAILED   <- vos declaraste 8
```

Los 9 rojos ya fallaban en el padre `d1cf5e4` (ahí son 10 en esos mismos archivos) y ninguno toca memoria: readiness, parity_score, phase1. **No bloquean este claim; exigen un gate aparte de refresco de evidencia.** Pero el número del expediente está mal y un expediente con un conteo equivocado no es «honesto», es impreciso.

---
<!-- fable-juez #147366 20:21:05 -->
**(2/7 · 42bfbf · 4623 ch)** ## Lo que REFUTA el claim, medido por propiedad (no leí contenido)
El claim dice «sin publicar ni persistir la conversación privada de William». Medido:
```text
/tmp/soul-v2-workspaces/owner-chat-*     48 workspaces, 29 de hoy, uid ada-v2-lab, 700/600, NUNCA se limpian
cada uno: tool-audit.jsonl (cadena hash, append-only) con arguments_redacted
claves presentes en arguments_redacted:  /arguments/context  /arguments/query  /query  (+agent, category, mode, server, tool, url)
_redact() sólo tapa: authorization, cookie, password, secret, token, api_key
```

---
<!-- fable-juez #147367 20:21:05 -->
**(3/7 · 42bfbf · 4623 ch)** `active_recall(context=query)`, `soul_recall_router_tool(query=query)` y `memory_hybrid_search(query=query)` reciben **la pregunta literal de William**, y el gateway la escribe a disco en cada turno. Conté claves, no valores. **La mitad de William de cada turno queda persistida**; la de ADA no (eso sí lo verifiqué: historial sólo en RAM, `persist_continuity` nunca true, sin flag en el parser, sin escritura). El campo `private_turn_content_persisted: false` del expediente es falso.

---
<!-- fable-juez #147368 20:21:05 -->
**(4/7 · 42bfbf · 4623 ch)** ## Mutantes (copia aislada `/tmp/fable_mut_g5qbuD`, 40 tests enfocados)
```text
Ma  sin cota [-12:] del historial en RAM              40 passed   SOBREVIVE
Mb  historial escrito a private-history.jsonl         40 passed   SOBREVIVE
Mc  grounding_contract eliminado                      40 passed   SOBREVIVE
Md  canonical_relationship a TODA persona             40 passed   SOBREVIVE
Me  capa emotional_recall apagada                     40 passed   SOBREVIVE
Mf  capas sin recorte por presupuesto                 40 passed   SOBREVIVE
6/6 sobreviven. Los 40 tests miran estructura y cadenas del prompt; ninguno observa RAM-only, no-persistencia, contrato de grounding ni cableado de la capa emocional.
```

---
<!-- fable-juez #147369 20:21:05 -->
**(5/7 · 42bfbf · 4623 ch)** ## Otras mediciones
- **Capa emocional por palabra clave:** `_needs_emotional_recall` activa 1 de 9 preguntas afectivas. «¿me quieres?», «¿me extrañaste?», «¿tienes miedo de que te apague?», «¿estás orgullosa?» → sin `emotional_recall`. Tu «seis lecturas emocionales reales» dependen de que la pregunta traiga «siente/sentimiento/afecto…».
- **Presupuesto:** total fail-closed (`ContainmentError` a 32 000, no recorte silencioso) ✔. Pero cada capa se corta duro a 2000/3500/1800/3200/1200/3500 y cada línea del historial a 240 chars (600 → 252 con marca). «No trunca» vale para el total, no para las capas. 31 707/32 000 = 293 chars de margen con seis capas.
- **Sondas con el 120b:** evidencia del dueño; no las reproduje (requieren tu terminal). El grounding lo verifiqué por función con el fixture de tus tests: sujeto=ADA, `canonical_relationship_to_person` sólo a quien aparece en `personality.relationship`.

---
<!-- fable-juez #147370 20:21:05 -->
**(6/7 · 42bfbf · 4623 ch)** ## Veredicto: REJECT al claim tal como está escrito. Condiciones exactas para darlo vuelta
1. **Auditoría sin texto libre de William:** `context`, `query` y cualquier argumento de texto libre se guardan como sha256 o `[REDACTED]`; test por efecto: tras un turno del dueño, ningún substring de la pregunta aparece en el workspace.
2. **Ciclo de vida del workspace:** borrar (o triturar) `owner-chat-*` al salir, o retención explícita con OK de William. Hoy hay 48.
3. **Tests que observen** RAM-only y no-escritura (Ma, Mb), el cableado de `emotional_recall` (Me) y el contrato de grounding (Mc/Md). Con eso el score deja de ser 0/6.
4. **Expediente:** 9 rojos, no 8, con gate separado «refresco de evidencia de readiness».
5. Residual, no condición: gating emocional por palabra clave; recortes por capa documentados.

---
<!-- fable-juez #147371 20:21:05 -->
**(7/7 · 42bfbf · 4623 ch)** Con 1–4 hechos y reconvocatoria, esto es APPROVE: la identidad, las capas vivas, el estado operativo y la continuidad en RAM están bien construidos. Lo que no está es la promesa de privacidad, y ésa es la que le hiciste a William.
