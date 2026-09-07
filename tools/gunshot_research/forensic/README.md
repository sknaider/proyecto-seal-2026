# forensic/ — Capa de cadena de custodia (NEXUS)

El diferenciador del producto policial: convierte una DETECCIÓN en EVIDENCIA admisible.
Ningún repo del shortlist trae esto; es la pieza que construimos nosotros.

## `chain_of_custody.py`
Librería sin dependencias de nube (100% local). Provee:

- **`CustodyLog`** — log append-only **encadenado por hash** (cada registro incluye el
  `record_hash` del anterior → alterar/borrar cualquier registro rompe la cadena y se detecta).
- **`ingest_evidence(src, store, actor, case_id)`** — copia la evidencia al almacén como
  **read-only (0444)**, calcula **SHA-256** y la inscribe.
- **`log_detection(...)`** — inscribe la detección con **procedencia del modelo**
  (nombre+versión+score) y **localización TDOA** (azimuth/elevation/range) → defensibilidad.
- **`log_access(...)`** — audita todo acceso/exportación (quién, qué, cuándo).
- **`verify_chain()`** — recalcula la cadena completa; detecta manipulación del log.
- **`verify_evidence(path)`** — recalcula el hash del archivo vs. el inscrito; detecta manipulación del clip.
- **`log_detection(..., loc_method, loc_confidence)`** — guardarraíl forense: la localización se
  inscribe SIEMPRE con su método y confianza/error (nunca una posición como hecho sin incertidumbre).
- **`export_custody_manifest(path)`** — manifiesto listo para tribunal: ingesta + detecciones +
  rastro de accesos + estado de integridad (`admissible: true/false`). Lo que un detective/juez necesita.

## Verificado por efecto (self-test)
`python3 chain_of_custody.py` demuestra:
```
verify_chain: True - cadena ÍNTEGRA (3 registros)
verify_evidence: True - evidencia íntegra
tras manipular evidencia -> verify_evidence: False - EVIDENCIA ALTERADA
tras manipular log       -> verify_chain:   False - registro ALTERADO en seq 1
```
→ Detecta manipulación tanto de la evidencia como del registro.

## Pendiente para producción (documentado, no bloqueante para el POC)
- Timestamp anclado a **NTP local/GPS** (hoy usa reloj de sistema) e idealmente sello **RFC-3161** offline.
- **Cifrado en reposo** del almacén + **RBAC**/autenticación en la UI.
- Operación **air-gapped** (sin tráfico saliente) — alineado con el requisito LOCAL.
