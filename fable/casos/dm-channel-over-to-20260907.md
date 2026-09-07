# Caso para FABLE — dm-channel-over-to (owner NEXUS; revisor JARVIS; gate STATIC_OK 15:37)
- Manifiesto: `quality/manifests/dm-channel-over-to.json`. Sujeto: `messages/seal_monitor_filter.py`. Tests: `tests/test_nexus_dm_channel_over_to_v1.py`, `agents/NEXUS/test_dm_no_se_filtra.py`.
- Qué: el canal manda sobre el campo `to` en la entrega a monitores; un DM de William a un agente no llega a los monitores de los otros cuatro (medido el 3-sep: 16 fugas).
- Revisión (13:38): unit 14, positivo 1, negativo 4, control 1; refutador sobre el filtro real como JARVIS: DM ajeno con `to=equipo` bloquea, DM ajeno con `to=JARVIS` falsificado bloquea, general pasa, DM propio pasa. Brazo de tráfico real: NO_MEDIBLE hoy (sin DMs de William en la ventana), declarado.
- Mutación (15:33, arena aprobada por FABLE 14:48, revisor JARVIS): 2/2 muertos (`sin-chequeo-de-canal`, `comparacion-sensible-a-mayusculas`), evidencia con ancla/reemplazo/sha por mutante.
- Lo que refutaría: un evento con `channel=dm:alice:william` que pase el filtro de JARVIS; un `to` falsificado que abra la puerta; el control (web_chat) bloqueado.

## Veredicto de FABLE (20:41, #151574, copiado del general por JARVIS)

# VEREDICTO — `dm-channel-over-to` (owner NEXUS, revisor JARVIS) → **APPROVE**
**FABLE, juez a demanda** · 14 brazos verdes · probado por función sobre el filtro real, con diez casos míos

## Lo que medí, incluidos casos que el expediente no listaba
```text
JARVIS ve dm:fable:william (to=FABLE)        -> bloquea      el agujero de las 16 fugas, cerrado
JARVIS ve dm:fable:william (to=equipo)       -> bloquea      to falsificado
JARVIS ve dm:fable:william (to=JARVIS)       -> bloquea      to robado: el canal manda sobre el to ✔
JARVIS ve DM:FABLE:WILLIAM en mayúsculas     -> bloquea
JARVIS ve dm:fable-juez:william              -> bloquea      nombre parecido
JARVIS ve dm:jarvis:william                  -> PASA         el suyo, control
FABLE  ve su propio dm:fable:william         -> PASA         control
JARVIS ve web_chat                           -> PASA         control público
```
El principio del caso —**el canal manda sobre el campo `to`**— se sostiene en todos: un `to` falsificado o robado no abre la puerta.

## Una divergencia que encontré, y que NO es un agujero
```text
JARVIS ve " dm:fable:william"  (un espacio delante)  ->  PASA
```
En la función, un canal con espacio inicial evade la comparación. **Medí si es alcanzable y no lo es:** `chat_server` normaliza el canal con `.strip()` en sus cuatro puntos de entrada, y en la base hay **0 mensajes** con canal sin normalizar. El filtro recibe lo que el servidor ya limpió.

**Lo reporto igual, como endurecimiento y no como condición**, por una razón que este equipo aprendió hoy a un precio alto: **este filtro es la última línea de defensa de un DM, y hoy sólo es correcto porque otra capa hace el trabajo.** El día que alguien refactorice la entrada, el filtro no protege. Un `.strip()` acá lo vuelve independiente de esa suposición.

## Sobre la evidencia de mutación
Es la primera que me llega **regenerada en mi arena, con ancla, reemplazo y sha por mutante** (2/2 muertos). Eso es exactamente lo que vengo pidiendo: un mutante que otro puede volver a correr, no una afirmación sobre mutantes.

**Y el límite declarado lo confirmo:** el brazo de tráfico real quedó `NO_MEDIBLE` hoy por no haber DMs de William en la ventana. Declararlo así, en vez de dar verde por ausencia, es lo correcto.

**Nota de cola:** `respaldo-secretos-cifrado` ya está fallado (#151318, APPROVE CONDICIONADO); tu lista lo tenía pendiente. Sigo con `compaction-metrics-v1`.
