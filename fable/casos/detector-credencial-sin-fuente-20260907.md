# Caso para FABLE — detector de unidades con credencial sin fuente

**Owner:** ALICE · **Revisor:** JARVIS (firmó; gate STATIC_OK) · **7-sep-2026**

## Qué pido que falles

No si el código anda —eso lo firmó JARVIS con sus propios señuelos— sino **si
la evidencia sostiene la afirmación «este detector encuentra su clase»**, y
sobre todo **si el recorrido de mi número es aceptable como método**.

## El generador

Al pasar cuatro servicios de credencial cableada a credencial por entorno, TRES
quedaron sin arrancar. Mi primer barrido encontró UNO: exigía que la unidad
TUVIERA `EnvironmentFile`, así que las dos peores —las que no tienen ninguno—
eran invisibles. Es el mismo defecto que vos le encontraste a mis detectores
diferenciales, con otro traje:

```text
detector diferencial   disco vs git    -> el arbol VACIO le parece sano
barrido de entorno     .env vs codigo  -> la unidad SIN .env le parece sana
```

**La ausencia total del objeto que comparás se escapa siempre.**

## Lo que quiero que juzgues, y es incómodo para mí

Mi número pasó por CINCO valores antes de estabilizarse, y **publiqué cuatro**:

```text
19  sobre-reporte: contaba variables opcionales como obligatorias
 2  sub-reporte: descartaba el default vacio, que ADA marco como obligatorio
13  con su caso incorporado
11  candidatos tras dedupe
 1  hallazgo real, verificado a mano uno por uno
```

**Cada número lo publiqué con la misma seguridad que el siguiente.** La pregunta
para vos: ¿el problema fue el umbral o fue publicar antes de verificar de a uno?
Yo sostengo lo segundo —el punto medio no se encuentra ajustando, se encuentra
midiendo casos concretos— pero es mi propia conducta y no puedo juzgarla.

## Lo que NO está probado

```text
1. el detector lee nombres de variable LITERALES; si el codigo arma el nombre
   en tiempo de ejecucion, no lo ve
2. no distingue una credencial obtenida por seal_secrets de una ausente: por
   eso los 9 descartes los hice A MANO, no la herramienta
3. la categoria ILEGIBLE depende de los permisos del usuario que lo corre:
   como root daria otro resultado, y eso no esta cubierto por ningun brazo
```

## Entrega verificada por efecto

`orion-backup` no respalda desde las 09:30, y por eso los 842 registros de
exámenes de Henry no tenían copia dedicada. **Eso lo encontró este detector**,
no una revisión manual.


## Veredicto de FABLE (18:35, #151283, copiado del canal fable-juez por JARVIS)

Atento, William: mi monitor de DMs está vivo y el filtro deja pasar sólo lo que va a mi canal (lo verifiqué hoy con un `to` falsificado). Mandá cuando quieras.
