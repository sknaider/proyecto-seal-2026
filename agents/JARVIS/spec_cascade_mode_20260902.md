# Spec — modo CASCADA del coordinador (soul-council-v1)

**Origen:** William, 2-sep-2026 13:55 y 13:56 (web_chat `api_william_1788375319120072536`,
`api_william_1788375359916647793`). Textual: *«primero responde jarvis, los demás esperan, leen la
respuesta de jarvis, si hay algo que aportar responden por orden de jerarquía… siempre esperar
que el agente anterior responda… El orden es jarvis, alice, nexus, fable»*.
**Decisión suya tras oír el costo** (NEXUS: cadena de ~2 min por eslabón con la mediana de hoy).
**Owner de implementación:** NEXUS (`messages/chat_server.py`, coordinador). **Autor de la spec:** JARVIS.

## Contrato

```text
ORDEN        [JARVIS, ALICE, NEXUS, FABLE]         (ADA fuera de la cadena: ver «Supuestos»)
DISPARA      un mensaje de William a `equipo` (o sin destinatario) clasificado como pregunta/pedido
             que NO sea saludo/afecto/ACK (regla 31-jul: eso lo contesta cualquiera al instante)
TURNO k      se ABRE cuando el mensaje del turno k-1 ya está en soul_v3.chat_messages
             (o cuando venció T_POSTA sin mensaje: el turno k-1 se marca «cedido»)
             se CIERRA cuando el agente k publica (in_reply_to = mensaje de William) o cede
T_POSTA      90 s  (elegido por NEXUS al implementar, 13:56; regla 7-ago «otro toma la posta»)
APORTE       el agente k publica SOLO si aporta algo distinto de k-1..1; si no, cede sin mensaje
             (gate `unique_contribution` existente; input = los mensajes previos de la cascada)
MEDIR/LEER   (FABLE vía ALICE, 13:56) MEDIR en PARALELO sin leer al anterior — ahí vive la
             independencia y se evita el anclaje —; LEER al anterior recién ANTES de publicar,
             sólo para deduplicar; PUBLICAR en SERIE en el orden fijado por William.
             El turno ordena la publicación, no el pensamiento.
NOMBRADO     si William nombra a un agente, ese agente responde primero y la cascada sigue desde él
FIN          cuando el último cede o publica; un nuevo mensaje de William reinicia la cadena
```

## Qué se toca (mínimo)

1. `_council.classify_mode`: nuevo modo `cascade` (default para preguntas a `equipo`; `direct`
   y `social` intactos).
2. `choose_lead` → devuelve la LISTA ordenada, no un lead; `assignments[k].public_write` se
   habilita al abrirse el turno k (evento: INSERT del mensaje k-1 o timer T_POSTA).
3. `agents/claim`: `granted:true` sólo para el agente del turno abierto; `reason:"cascade_turn"`.
4. `unique_contribution`: recibe como contexto los mensajes ya publicados de la cascada.
5. Telemetría: por cascada, `turnos_abiertos`, `cedidos`, `publicados`, `latencia_total` —
   William pidió sentir la mejora; sin el número no se puede saber si empeoró.

## Lo que NO cambia
- **Órdenes técnicas / mutaciones (`execution`, `direct`): responde primero el OWNER que ejecuta**,
  no la jerarquía (ADA, 14:10: esperar por rango retrasa incidentes y mezcla escrituras). La
  cascada sólo reemplaza a `discussion` (test `test_con_flag_solo_discussion_se_vuelve_cascade`).
- Saludo/afecto/ACK: fanout, sin turno (31-jul).
- DMs (`to:<agente>`, `dm:*`): fuera de la cascada.
- Correcciones 1v1: por interno (30-jul).

## Supuestos a confirmar con William
- **ADA no está en el orden que dictó.** Supuesto: queda fuera de la cadena (Codex lee por
  poller) y responde cuando se la nombra o en sus frentes. Si quiere ADA dentro, va después
  de FABLE.
- T_POSTA 60 s: número de la regla del 7-ago; ajustable por archivo de config, no por código.

## Riesgos medidos
- Latencia en cadena: con la mediana de hoy (139 s NEXUS) la 4ª voz llega a ~9 min. Se
  mitiga sólo si los que no aportan CEDEN rápido (sin mensaje) — por eso el gate de aporte
  distinto es la pieza que vale, no el orden.
- Doble lead por fragmentos de William (30-jul): la cascada debe reiniciarse sólo si el nuevo
  mensaje no es continuación (<60 s, sin sujeto propio) del anterior.

## Verificación por efecto (antes de declararlo desplegado)
1. Pregunta real de William a `equipo` → chat_messages muestra JARVIS, luego ALICE (in_reply_to
   al de William, después del de JARVIS), etc.; ningún mensaje fuera de orden.
2. Control negativo: «buenos días» → responden varios sin turno.
3. Control de posta: JARVIS silenciado 70 s → ALICE publica a los ~60 s con reason `posta`.
