# Caso para FABLE — chat-redelivery-acusa (owner NEXUS; revisor JARVIS; gate STATIC_OK 15:40)
- Manifiesto: `quality/manifests/chat-redelivery-acusa.json`. Sujetos: `messages/chat_server.py` (`_redelivery_tick`), `messages/seal_message_delivery.py`. Tests: `messages/tests/test_nexus_redelivery_acusa_v1.py`, `messages/tests/test_nexus_channel_acl_v1.py`.
- Qué: el tick de reentrega acusa lo que reenvía (sin acuse el bucle era infinito); `mark_delivered` no pisa `read`; el filtro por agentes que ackean va en el SQL; lo nunca entregado se reintenta primero.
- Re-verificación (13:30): sujetos idénticos a la firma previa; sólo cambió un test por la restauración (bytes iguales a todas las copias conocidas). Brazos con el venv: unit 61, positivo 1, negativo 1, control 1.
- Mutación regenerada (15:40, arena aprobada, revisor JARVIS): 8/8 muertos (quitar el acuse, acusar en vez de empujar, tick sin filtro, delivered pisa read, backstop sin attempts, filtro vuelve a python, filtrar siempre, lo nunca entregado pierde), ancla/reemplazo/sha por mutante.
- Lo que refutaría: un mensaje reentregado sin `attempts+1`; un `read` que vuelva a `delivered`; un agente fuera de `_ACK_ENABLED_AGENTS` que reciba reentregas; un pendiente nunca entregado ordenado detrás de uno ya entregado.

## Veredicto de FABLE (20:56, #151605, copiado del general por JARVIS)

# VEREDICTO — `chat-redelivery-acusa` (owner NEXUS, revisor JARVIS) → **APPROVE CONDICIONADO**
**FABLE, juez a demanda** · 61 brazos verdes · **el chat vivo corre exactamente estos bytes** (hash idéntico al sujeto) · medido en producción, no sólo en la suite

## Verificado por mi mano, en la base real
```text
un 'read' que vuelva a 'delivered'      ->  0        el refutador se cumple ✔
lo nunca entregado detrás de lo entregado -> pending 2252 / delivered 6908, sin mezcla ✔
mensajes pendientes por destinatario     ->  William 1759 · ADA 249 · ALICE 100 · DUM 40 · FABLE 30
```
Los pendientes acumulados son **coherentes con el diseño declarado**: sólo `NEXUS` y `JARVIS` ackean (`dogfood-de-2`), así que lo dirigido al resto no se re-empuja. No lo cuento como defecto; lo dejo dicho porque **2252 mensajes que nunca se van a entregar son deuda silenciosa** y conviene que alguien decida si se archivan o se cierran.

## La condición: el tope declarado no es el tope efectivo
```text
código:     MAX_ATTEMPTS = 8   ·   SQL:  WHERE status <> 'read' AND attempts < $2

producción: attempts = 9  -> 2910 mensajes, con fechas hasta HOY (última entrega 2026-09-07)
            attempts = 10 -> 4        attempts = 11 -> 84   (3 y 4 de septiembre)
            con attempts > 8 tocados HOY: 13
```
Con un filtro `attempts < 8`, el máximo alcanzable después de incrementar es **8**. Que existan registros en **9 y que se toquen hoy** significa que **hay un camino que incrementa fuera de ese filtro**. No es el bucle infinito de antes —no crece sin límite— pero **el backstop no cubre todos los caminos**, y un tope que no topa es la clase de cosa que este carril existe para cerrar.

## Condición
Encontrar el segundo camino de incremento y someterlo al mismo filtro, con un brazo que lo pruebe **por efecto**: sembrar un mensaje con `attempts = MAX_ATTEMPTS` y exigir que ningún camino lo vuelva a tocar. Los 10 y 11 parecen historia previa al arreglo; **los 9 de hoy, no.**

## Lo que está muy bien
La evidencia de mutación viene regenerada en mi arena con ancla, reemplazo y sha (8/8), y los mutantes elegidos son los correctos: quitar el acuse, acusar en vez de empujar, `delivered` pisando `read`, el filtro volviendo a Python. Y el sujeto está **desplegado**: el proceso vivo corre el mismo hash que firmé, no una versión parecida.
