# papers/ — artefactos publicables de SOUL

Esta carpeta existe porque **no existía**, y esa ausencia costó el paper.

## Qué pasó (10-sep-2026)

El 7-sep se borró `/home/dadito` entero. El paper enviado a ICSTE 2026 vivía en
`~/Documentos` y en un `papers/` que **nunca estuvo versionado**: medido ese día,
`git log --all -- papers/` no devolvía nada en las 16 ramas. Se perdieron el PDF y
el fuente. La única copia que quedaba estaba en el portal de la conferencia, y la
repuso William subiéndola al chat.

## La regla que deja

**Un entregable externo —lo que sale del equipo hacia una conferencia, un cliente o
un organismo— va a git el mismo día que se envía.** No al terminar, no cuando esté
prolijo: el día del envío. Un artefacto que sólo existe en un disco no existe.

## Contenido

| archivo | qué es | sha256 |
|---|---|---|
| `SOUL_Core_ICSTE2026.pdf` | versión enviada a ICSTE 2026 (paper JK1208, zmeeting id 41537), repuesta desde el portal por William el 10-sep-2026 | `ef66c15760625a7510e4b36604fd74d650e2019ecc1e81c29a0920563757a5f2` |

**Falta el fuente** (`SOUL_ICTSE2026_draft.md` o el `.tex`/`.docx` de la v10). El PDF
no se puede editar para las revisiones: si la conferencia las pide, hay que
reconstruirlo o pedírselo a Henry o a Marks Calderon, los otros dos autores.

## Fechas del calendario oficial de ICSTE 2026

Tomadas de icste.org el 30-jul-2026 y verificadas de nuevo hoy contra la DB
(memorias 328895, 329205, 329213, 329212):

```text
30-ago-2026   deadline del paper completo
30-sep-2026   notificacion de aceptacion
15-oct-2026   version final
18-20 dic     conferencia, Kobe, Japon
```
