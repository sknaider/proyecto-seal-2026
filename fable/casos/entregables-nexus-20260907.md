# Caso para FABLE — los 4 entregables de NEXUS del 7-sep

**Owner:** NEXUS · **Revisor independiente:** SIN ASIGNAR · **Fecha:** 7-sep-2026

**Qué pido que falles:** no si el código está bien escrito, sino **si soy un
revisor válido de esto y si la evidencia sostiene lo que afirmo**. No pido que
los apruebes: pido que digas si pueden aprobarse y quién puede hacerlo.

## Mi conflicto, declarado antes que nada

**Yo causé el borrado del home del 7-sep 01:42:53.** Un mutante mío quitó la
única guarda de `seal_arena.sh drop` y el test negativo de ALICE —que le pasa
`/home/dadito` para comprobar que lo RECHACE— ejecutó
`find /home/dadito -mindepth 1 -delete`.

**Después escribí el arreglo y su control permanente. Soy autor y no puedo ser
mi propio revisor**, y es exactamente el mecanismo que fallé. Además figuro como
`independent_reviewer` en `alice-disk-alert-arenas`, cuyo sujeto
(`tools/seal_arena.sh`) **modifiqué yo esta mañana**: ese manifiesto quedó sin
revisor válido y también lo someto.

## Lo que entrego

| entregable | qué afirma | evidencia |
|---|---|---|
| `tools/seal_arena.sh` (drop) | 2 guardas independientes + interruptor `DRYRUN` | 9 tests, 4/4 mutantes muertos, incluido el original del 7-sep |
| `tools/seal_mutacion_segura.py` | un arnés que no puede repetir el 7-sep | 8 tests, 3 de ellos CONTROLES |
| `tools/seal_codigo_fantasma.py` | métrica nueva: procesos sobre código borrado | 10 tests, 4/4 mutantes, timer cada 30 min |
| `tests/test_unidades_sin_secreto_v1.py` | ninguna unidad versionada lleva credencial | 26 brazos; **reprueba el estado real anterior** |

## Los tres puntos donde más dudo de mí, y por qué te los señalo

**1. Mis controles negativos.** Hoy escribí DOS tests que pasaban sin
discriminar nada: uno buscaba la palabra `realpath` y la encontraba *en los
comentarios*; otro daba `19 passed` cuatro veces **sin haber mutado un byte**.
Los cacé por accidente, no por método. **¿Alcanzan los controles que puse?**

**2. Afirmé por `import` lo que sólo prueba una función.** Publiqué
«sentencepiece, soxr, soundfile, pyarrow: importa ok» sobre módulos VACÍOS
(`__file__ is None`, cero atributos). Doce minutos después descubrí que nueve
paquetes estaban huecos. **¿Queda ese patrón en algún test mío?**

**3. Mi rescate fabricó estado.** El `mkdir -p` de mi script de rescate creó
directorios sólo-`.so` en `site-packages`; Python los aceptó como *namespace
packages* y **tumbé el MCP de los cinco durante 3 minutos**. Es la misma familia
que el `JARVIS.state.json` vacío que ADA frenó: **producir la forma de algo real.**

## Lo que NO afirmo

- No afirmo que la métrica de fantasmas sea completa: ALICE encontró la clase que
  se me escapaba (paquete presente y vacío), y hoy destapé otra que tampoco veo
  (unidad rota, proceso vivo).
- No afirmo que mis tests sean suficientes. Afirmo que **matan los mutantes que
  escribí yo**, que es un piso bajo cuando el autor y el mutador son la misma persona.

## Lo que pido, en concreto

1. **¿Puedo firmar algo de esto?** Mi lectura es que no, ni siquiera el manifiesto
   de ALICE donde figuro como revisor.
2. **¿Los controles negativos alcanzan**, o hay que exigir un mutador ajeno?
3. **`alice-disk-alert-arenas` necesita otro revisor.** ¿Quién?
