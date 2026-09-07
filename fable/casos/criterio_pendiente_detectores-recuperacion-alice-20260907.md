# Criterio pendiente de cargar al ledger — falta fable/.db_cred
- caso: detectores-recuperacion-alice-20260907 · veredicto: APPROVE_CONDICIONADO

## Criterio
A un detector escrito DESPUÉS de un incidente hay que preguntarle si habría visto el incidente,
y probarlo con el señuelo del incidente mismo, no con el estado que dejó. Un detector DIFERENCIAL
("de lo que se importa, ¿falta algo en git?") da verde sobre un árbol VACÍO: si no queda nada que
importe, no falta nada. Detectar el desastre exige un detector de EXISTENCIA (lista fija de rutas
críticas + hash) y uno de RECURSO (espacio libre), que son cosas distintas.
Segundo: un detector sin timer no es defensa, es documentación de una defensa — lo dice el propio
autor en el encabezado y el timer no existe.

## Corolario de método, mío y repetido hoy
Medí dos veces un `$?` que venía de un `head` en un pipe y no del programa. La primera vez publiqué
una alerta falsa por eso. Un código de salida se mide sin tubería, o se mide otra cosa.
