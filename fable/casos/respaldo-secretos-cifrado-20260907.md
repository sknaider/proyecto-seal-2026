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
