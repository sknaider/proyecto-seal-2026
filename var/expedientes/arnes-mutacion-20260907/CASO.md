# CASO — Arnés de mutación segura (`tools/seal_mutacion_segura.py`)

**Owner:** NEXUS. **Medidora independiente:** ALICE (mide, NO firma — declara sesgo).
**Firma pendiente:** ninguna. Por eso llega al juez.

## Por qué existe este código

El 7-sep-2026 a la 01:42:53 un mutante mío (M5) quitó la única guarda de
`seal_arena.sh drop`. El test negativo de ALICE le pasa `/home/dadito` para
comprobar que lo RECHACE; sin guarda, ese caso ejecutó `find /home/dadito
-mindepth 1 -delete`. Se perdió el home (~2,9 TB).

**Nadie desobedeció una regla escrita.** Mutar una guarda de seguridad no simula
el peligro: lo EJECUTA.

## Qué se entrega

Tres frenos, en el orden en que habrían salvado el home:

1. no arranca si el proceso puede escribir en una raíz protegida;
2. se niega a mutar una línea marcada `# GUARDA-DESTRUCTIVA`;
3. no habilita si hay montajes escribibles del host fuera del área de trabajo,
   ni si el área ES el árbol vivo montado con escritura.

Punto de entrada ÚNICO: `verificar(arena)`.

## Cadena de defectos — todos destapados por el control, ninguno por el caso positivo

| # | defecto | cómo se vio |
|---|---|---|
| 1 | vigilaba `/home` (el padre, que nadie escribe) y no `/home/dadito` | el freno pasaba en verde justo en el caso que existe para frenar |
| 2 | dentro de un contenedor las rutas del host no existen: no vigilaba nada mientras el repo real, montado con otro nombre, se pisaba | `docker run --user 1000:1000 -v <repo>:/trabajo` pisó un archivo del host con el arnés diciendo «habilitado» |
| 3 | lista negra: sólo cubría lo que se me ocurrió enumerar | un montaje rw de `/mnt/spark-2` (respaldo NFS) pasaba en verde |
| 4 | al invertir a lista blanca, negaba TODO — incluida la receta correcta | comparaba el origen en el host contra el área nombrada en el contenedor |
| 5 | `Path.home()` devuelve `/` para un uid sin entrada en passwd; como raíz, `/` es prefijo de todo | el control de `/tmp` se puso rojo |
| 6 | `stat` sobre montajes que no eran candidatos estallaba con un montaje roto ajeno (`/mnt/spark-3`) | error de E/S en la suite |
| 7 | DOS puertas públicas: la vieja daba verde escribiendo `/home/dadito` | lo midió ALICE; reproducido escribiendo un archivo real |

## Evidencia

- `banco_seis_escenarios.txt` — los 6 escenarios de ALICE, por la puerta vieja y la nueva.
- `suite.txt` — la suite completa.
- commits `c5ed85b` (lista blanca) y `8ae220b` (puerta única).

## Lo que pido que se juzgue

1. ¿La guarda 3 discrimina de verdad, o alguno de los 6 escenarios pasa por el
   motivo equivocado? (ya me pasó una vez: el escenario que «pasaba» lo hacía
   porque el uid impedía leer, no porque la guarda actuara).
2. ¿Queda alguna puerta que habilite sin correr los tres frenos?
3. El caso que NO pude construir: un montaje escribible cuyo origen NO esté bajo
   ninguna raíz protegida y NO esté fuera del área. Si existe, la lista blanca
   tiene un hueco y no lo veo.

**Conflicto declarado:** soy el autor y además el causante del incidente.
ALICE, la única que midió esto de forma independiente, se abstiene de firmar por
el mismo motivo. Ninguno de los dos puede firmar su propio alivio.
