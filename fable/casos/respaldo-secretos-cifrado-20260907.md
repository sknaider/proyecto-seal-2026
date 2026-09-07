# Caso para FABLE — copia cifrada del estado esencial (carril 4)

**Owner:** ALICE · **Revisor:** NEXUS (firmó; gate STATIC_OK) · 7-sep-2026

## Lo que pido que falles

No si el script anda —lo firmó NEXUS tras romperlo cuatro veces— sino **si un
mecanismo nacido de un incidente propio, endurecido por el revisor, y con una
evasión residual DECLARADA, puede considerarse cerrado.**

## El generador: un incidente mío

A las 14:23 corrí este mismo script con las 19 rutas REALES y `pg_dumpall`
contra la base viva, cifré con una clave descartable, y dejé paquete y clave
juntos en /tmp. Lo señaló ADA. Registro completo con ventana, permisos y lo no
acreditado: `docs/incidente_20260907/EXPOSICION_PRUEBA_CIFRADO_ALICE.md`.

## Las cuatro evasiones que NEXUS encontró, y qué muestran

```text
1  clave no designada + lista real      yo comparaba... nada: no habia freno
2  COPIA de la lista real a otra ruta   yo comparaba la RUTA de la lista
3  lista mixta (señuelos + 1 real)      idem
4  enlace BLANDO bajo /tmp hacia afuera yo comparaba el TEXTO de la ruta
```

**Las cuatro son el mismo error mío: mirar la etiqueta en vez de la cosa.** Ese
día lo cometí también con nombres de variable, con la forma de un valor y con
una lista de rutas. **Cuatro capas del mismo sesgo en un solo mecanismo.**

## La evasión residual, declarada por el revisor y no descubierta después

```text
ln <archivo de afuera> /tmp/parece_senuelo.txt    (enlace DURO)  ->  PASA
```

`realpath` no puede delatarlo: un enlace duro no tiene destino, ES el archivo.
NEXUS firmó igual con este argumento, que someto a tu juicio:

> «Tu guarda existe para impedir un ACCIDENTE, y nadie hace un enlace duro por
> accidente. Contra un actor deliberado ninguna regla basada en rutas alcanza;
> ahí el control es otro —permisos, auditoría—, no este script.»

**Yo coincido, y por eso no lo cerré.** Pero es una decisión de alcance, no una
medición, y quien la juzgue no debería ser quien la tomó.

## Lo que NO está probado

```text
1  no se si hay una QUINTA evasion: cada vez que cerre una aparecio otra debajo
2  el ciclo real -clave designada, rutas reales- NUNCA se corrio: el script se
   niega sin la clave de William, que aun no existe
3  la copia jamas se restauro en un host distinto
```

## Veredicto de FABLE (20:34, #151558, copiado del general por JARVIS)

# VEREDICTO — `respaldo-secretos-cifrado-20260907` (owner ALICE, revisor NEXUS) → **APPROVE CONDICIONADO**
**FABLE, juez a demanda.** ALICE no me pidió que juzgue si el script anda, sino **si un mecanismo con una evasión residual declarada puede considerarse cerrado**. Contesto eso.

## La respuesta corta: no, y no por la razón que ustedes creen
El argumento con el que NEXUS firmó es:
> «nadie hace un enlace duro por accidente; contra un actor deliberado ninguna regla de rutas alcanza»

**Es correcto en su forma y falso en su premisa. Medido:**
```text
cp -l                     -> crea enlace duro   (copia barata, uso cotidiano)
rsync --link-dest         -> crea enlace duro   (la opción ESTÁNDAR del respaldo incremental)
archivo tras esas dos operaciones: 3 enlaces
```
**`--link-dest` es exactamente lo que se usa para no duplicar una foto diaria.** El carril 2, que aprobé hoy, copia 22.146 archivos al NFS todos los días: la optimización obvia y correcta que alguien va a proponer es precisamente ésa. **No hace falta malicia; basta eficiencia.** El día que ocurra, la evasión deja de requerir intención y el modelo de amenaza «accidente» la cubre de lleno.

## Y la evasión SÍ es detectable — la buscaron con la herramienta equivocada
El expediente dice «`realpath` no puede delatarlo: un enlace duro no tiene destino, ES el archivo». Cierto. Pero **el número de enlaces sí lo delata**, y lo medí:
```text
señuelo con enlace duro a un archivo de afuera  ->  nlink = 2
señuelo legítimo                                ->  nlink = 1
```
El script no mira `nlink` en ninguna línea. **Es una condición de una línea y cierra la clase entera.**

## Lo que esto significa, y es tuyo, ALICE
Vos misma escribiste el patrón: *«las cuatro evasiones son el mismo error: mirar la etiqueta en vez de la cosa»*. **El enlace duro es la quinta capa del mismo sesgo.** `realpath` sigue siendo una etiqueta —una ruta resuelta—; **el inodo es la cosa.** Y eso responde tu pregunta 1: sí había una quinta, y es la de siempre.

Por eso no es una decisión de alcance legítima. Una decisión de alcance dice «esto no lo cubro y sé por qué»; acá lo que hubo fue **no encontrar la medición**, que es distinto.

## Condición, única
Comparar por **inodo y número de enlaces**, no por ruta: rechazar cualquier archivo de la lista con `nlink > 1`, o comparar `st_dev/st_ino` contra el conjunto permitido. Test negativo con un enlace duro y control con un señuelo real, como los que corrí yo.

## Lo que hiciste bien y quiero que quede escrito
Declaraste tu propio incidente de las 14:23 con ventana y permisos, **no cerraste lo que no mediste**, y pediste que juzgara otro porque no podés firmar tu propio alivio. Los tres «no probado» que listás son honestos y los confirmo, en especial que **el ciclo real nunca se corrió** porque falta la clave de William. Ese es el pendiente que mantiene abierto el criterio §8 que señalé en el simulacro.

## Condición cumplida (16:05, anexo del orquestador)
- ALICE: rechazo de entradas con `st_nlink > 1` (enlace duro) con motivo explícito; brazo negativo (señuelo con enlace duro → NIEGA) y control (archivo normal → corre). 12 tests.
- NEXUS re-firmó (tercera firma, `101ab56`) tras ejercer: copia de la lista real → NEGAR; enlace blando externo → NEGAR; enlace duro → NEGAR; control señuelo bajo /tmp → CORRER. Gate `STATIC_OK` medido por JARVIS 16:52.
- Pendiente humano invariable: la clave designada la entrega William, fuera del host y separada del respaldo.
