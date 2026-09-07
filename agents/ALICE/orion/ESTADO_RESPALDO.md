# Respaldo de `orion_exam` — estado real al 7-sep-2026 13:20

## Lo que hay, medido

```text
datos             842 filas en 14 tablas (contadas una por una, no por
                  estadisticas: pg_stat decia 0 y era falso)
copia integra     SI, dentro del volcado COMPLETO de la base:
                  /mnt/spark-2/backups_seal/<dia>/seal_memory_completa_<dia>.dump
                  verificado leyendo el archivo ENTERO (pg_restore -f /dev/null,
                  exit 0) + codigo de salida 0 del productor. NO por su indice:
                  un dump truncado LISTA BIEN, porque su tabla de contenidos
                  va al principio.
respaldo DEDICADO NO CORRE
```

## Por que no corre el dedicado

`orion_backup.py` exige `ORION_EXAM_DSN` y **ninguna unidad se lo da**; aborta
fail-closed antes de tocar nada. **Esta bien escrito**: prefiere no correr antes
que usar el superusuario `seal`. Pero lleva sin correr desde el 7-sep 09:30.

**El peligro de este estado es que no se ve:** un respaldo que aborta siempre
parece uno que nunca hizo falta. No falla ruidosamente, no deja rastro en el
journal, y su timer figura sin proxima ejecucion. Es la misma forma del borrado
del 7-sep: el silencio parece salud.

## Que hace falta, y por que no lo resuelvo sola

Credencial del rol **`svc_orion_exam`** —que YA EXISTE y tiene SELECT sobre las
14 tablas— en un `EnvironmentFile` propio de `orion-backup.service`. **No la
fabrico ni uso `seal` mientras tanto**: eso es exactamente lo que el script se
niega a hacer, y tiene razon.

**Salvedad de ADA (13:06):** tener SELECT en las 14 tablas **no prueba** que el
respaldo solo necesite SELECT —puede requerir secuencias, tipos o metadatos—.
`svc_orion_exam` es un rol **candidato**, no acreditado: quien provisione la
credencial debe probar una corrida REAL, no asumir que alcanza.

## Diferencia que importa y no hay que mezclar

```text
volcado general   salva los DATOS ante una perdida total
respaldo por schema  permite restaurar SOLO los examenes sin tocar el resto
                     de la base -- que es lo que Henry necesitaria
```

Hoy esta resuelta la primera. La segunda, no.
