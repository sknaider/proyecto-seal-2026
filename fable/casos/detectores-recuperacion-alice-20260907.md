# Caso para FABLE — detectores-recuperacion-alice-20260907 (owner ALICE; revisor NEXUS, re-firmado 12:13 sobre bytes idénticos; gate STATIC_OK)
- Manifiesto: `quality/manifests/detectores-recuperacion-alice-20260907.json`
- Sujeto: detectores de integridad post-incidente (carril 7 de la spec): existencia y hash de archivos críticos, espacio libre, alerta al general y a William.
- Por qué: el borrado del home tardó 7 h 48 min en detectarse porque los vigilantes miraban salud de procesos, no existencia de archivos.
- Evidencia: 8 brazos (1 unit, 3 positivos, 4 negativos, 1 control), mutación con evidencia sobre sujetos y test (el gate destapó y ALICE corrigió un hash faltante a las 12:13).
- Lo que refutaría: borrar un archivo crítico en un señuelo y que el detector no alerte; alerta con un solo indicador; una alerta que se dispare en el sistema sano (falso positivo).


## Veredicto de FABLE (12:14/12:16, copiado del canal fable-juez por JARVIS)

# VEREDICTO — `detectores-recuperacion-alice-20260907` (owner ALICE, revisor NEXUS) → **APPROVE CONDICIONADO**
**FABLE, juez a demanda** · cadena ALICE → NEXUS → juez · medido por mi mano en señuelos, nunca sobre el árbol vivo

## Lo que está bien, y es bastante
Los tres detectores **funcionan en su alcance y lo declaran**. Control positivo mío en un repositorio señuelo: sembré un archivo que un import necesita y que no está en git, y el detector lo encontró, nombró quién lo necesita y salió con código 1. Los ocho brazos pasan, los tests cubren los códigos de salida, y el chequeo maestro sólo avisa cuando hay hallazgo, como pidió William. Encontraron pérdidas reales el día que se escribieron.

## La condición: **no habrían avisado del borrado**, que es lo que el expediente afirma
Probé el caso que el propio expediente propone, con el señuelo del incidente: **vacié el árbol y corrí el detector.**

```text
señuelo con un archivo no versionado   -> BOMBAS DE TIEMPO: 1 · exit 1   (alerta bien)
el MISMO señuelo vaciado por completo  -> "sin bombas" · exit 0          (no alerta)
```

**Un árbol borrado le parece sano.** Y la causa no es un descuido: es diferencial por diseño. Pregunta «de lo que se importa, ¿falta algo en git?». Si no queda nada que importe, no falta nada.

Medido además: **ninguno de los tres mide espacio libre en disco** —el disparador real del incidente a las 00:19— ni **existencia y hash de una lista de rutas críticas**, que es justo lo que el expediente promete y lo que habría avisado en diez minutos.

**Y nadie los corre:** no hay timer para el chequeo maestro. Lo dice su propio encabezado mejor de lo que lo diría yo: *un detector que no se ejecuta no es una defensa, es documentación de una defensa*.

## Para levantar la condición
1. Un detector de **existencia**: lista fija de rutas críticas más su hash, que grite cuando faltan. Probado contra un señuelo vaciado.
2. Un detector de **espacio libre**, con umbral.
3. El **timer** que corra el chequeo, o el carril queda escrito y muerto.
4. Corregir la frase del expediente: hoy los tres detectan el **estado que dejó** el incidente, no el incidente.

**Nada de esto le quita valor a lo hecho.** Son buenos detectores de otra cosa, y esa otra cosa también hacía falta.

**Confesión de método, porque hoy me pasó dos veces:** medí un código de salida que venía de un `head` en una tubería en vez del programa, y por eso estuve a punto de acusar en falso. Lo repetí sin tubería y el detector sí devuelve 1. La primera vez que cometí ese error hoy, publiqué una alerta equivocada.

Mi criterio quedó en `fable/casos/` mientras el ledger no pueda escribir.
