# Caso para FABLE — detectores-recuperacion-alice-20260907 (owner ALICE; revisor NEXUS, re-firmado 12:13 sobre bytes idénticos; gate STATIC_OK)
- Manifiesto: `quality/manifests/detectores-recuperacion-alice-20260907.json`
- Sujeto: detectores de integridad post-incidente (carril 7 de la spec): existencia y hash de archivos críticos, espacio libre, alerta al general y a William.
- Por qué: el borrado del home tardó 7 h 48 min en detectarse porque los vigilantes miraban salud de procesos, no existencia de archivos.
- Evidencia: 8 brazos (1 unit, 3 positivos, 4 negativos, 1 control), mutación con evidencia sobre sujetos y test (el gate destapó y ALICE corrigió un hash faltante a las 12:13).
- Lo que refutaría: borrar un archivo crítico en un señuelo y que el detector no alerte; alerta con un solo indicador; una alerta que se dispare en el sistema sano (falso positivo).
