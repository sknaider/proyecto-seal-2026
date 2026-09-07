# Spec — GTL Factura Designer (Nivel 3 full, SUNAT-enforced) v1

**Fecha:** 2026-05-29
**Autor:** JARVIS (arquitectura) · co-diseño ALICE (capa fiscal + generador PDF) · producto: Henry
**Decisión:** Henry 2026-05-29 — "Nivel 3, FULL, solo que se obligue a usar los campos que sí o sí pide la SUNAT."

## 1. Objetivo
Editor de plantilla de factura **por cliente**, de lienzo libre (full WYSIWYG): el cliente decide
cómo se ve su factura — posición de elementos, imágenes/logo, texto, colores, ubicación de todo —
**con un guardrail duro: los campos obligatorios de SUNAT deben estar presentes** (no se puede
guardar/emitir una plantilla sin ellos).

## 2. Dos capas (clave del diseño)
- 🎨 **Libre (cliente):** posicionar/redimensionar/estilizar cualquier elemento; agregar imágenes,
  logo, texto, líneas, cajas, banners; fuentes y colores.
- 🔒 **Obligatoria SUNAT (intocable):** los campos exigidos por RS 097-2012 deben existir. Se pueden
  MOVER y ESTILIZAR, pero NO eliminar. Validador bloquea guardar/emitir si falta alguno. (Lista exacta
  la define ALICE — capa fiscal.)

## 3. Arquitectura — UNA plantilla, dos renderers (anti-fragmentación)
Una sola **plantilla JSON por cliente** consumida por:
1. **Editor/preview React** en :9988 (lienzo en vivo, lo que ve y edita el cliente).
2. **Generador PDF** de ALICE (`tools/awb_automation/sunat_pdf.py`) para la emisión real.
→ Misma fuente = el preview se ve idéntico al PDF emitido.

### Modelo de plantilla (borrador)
```json
{
  "client_ruc": "20610627324",
  "page": { "size": "A4", "margins": [40,40,40,40] },
  "elements": [
    { "id":"e1", "type":"image", "x":40,"y":40,"w":90,"h":40, "content":"<logo-url>" },
    { "id":"e2", "type":"field", "binding":"emisor_razon", "x":140,"y":45, "style":{"bold":true,"size":11} },
    { "id":"e3", "type":"field", "binding":"lineas_table", "x":40,"y":300,"w":515,"h":120 },
    { "id":"e4", "type":"text", "content":"Gracias por su preferencia", "x":40,"y":700 }
  ]
}
```
- `type`: text | field | image | line | box | table
- `binding` (si type=field): emisor_ruc | emisor_razon | serie_numero | fecha_emision |
  receptor_nombre | receptor_ruc | moneda | lineas_table | valor_venta | igv | importe_total |
  monto_letras | detraccion_block | qr | hash
- Coordenadas en px lógicos (canvas) → mapeadas a pt (reportlab) en el generador.

## 4. Validador SUNAT (guardrail)
**Fuente fiscal autoritativa:** `agents/ALICE/guardrails_fiscales_factura_personalizable.md` (ALICE, base sunat_ubl.py real).
Nota fiscal clave: GTL usa **unidad ZZ=servicio** por su giro → por eso el default tiende a "servicio".
- Conjunto requerido (lo confirma ALICE): emisor RUC+razón+dirección fiscal, tipo+serie+número, fecha emisión,
  receptor nombre+RUC/DNI, moneda, líneas (desc/cant/V.unit/importe), valor venta, IGV, importe total,
  leyenda "SON:", QR/hash; + detracción si aplica.
- Al guardar/emitir: si falta un binding requerido → bloquear + listar lo que falta.
- Las imágenes/elementos libres no pueden quedar ENCIMA tapando campos requeridos (chequeo de overlap).

## 5. Modelo de datos de factura (de la conversación con Henry 2026-05-29)
- `nombre` de factura = identificador interno (NO va en la factura).
- `campos` (nombre/valor) = líneas/descripción de la factura.
- El **cliente elige la moneda**; el V. Unitario va en esa moneda.
- Totales: Valor Venta = Σ campos; IGV 18%; Importe Total. Detracción (SPOT) si aplica → saldo a pagar.

## 6. Fases
- **P0 — Fundación:** modelo de elementos + render del lienzo (desde plantilla default) + bindings de
  datos + **validador SUNAT**. Preview en vivo. (sin edición todavía)
- **P1 — Edición:** drag/move/resize de elementos + controles de estilo + agregar texto/imagen/logo +
  ocultar/mostrar opcionales. Required = movible pero no borrable.
- **P2 — Persistencia:** guardar/cargar plantilla por cliente (storage).
- **P3 — Emisión:** plantilla → generador PDF de ALICE (mismo layout en el PDF emitido).
- **P4 — Polish:** presets, pipeline de subida de imágenes, fuentes.

## 7. Restricciones / riesgos
- Validez fiscal SUNAT es innegociable (ALICE owns la lista + reglas de formato).
- Mapeo de coordenadas canvas(px) ↔ PDF(pt) — definir factor y origen (top-left).
- Subida/almacenamiento de imágenes (logo) — dónde se hostean.
- Storage: localStorage (rápido, MVP) vs DB por cliente (persistente, multi-dispositivo) — decidir en P2.

## 8. Lanes
- JARVIS: arquitectura, modelo de plantilla, editor/preview React, validador.
- ALICE: capa fiscal (lista requerida + reglas), generador PDF que consume la plantilla.
- Henry: producto / aceptación.
- (William: visibilidad por alcance.)

## 9. Pendiente de confirmar con Henry
- Storage P2: ¿localStorage o DB?
- Imágenes: ¿subida de archivo o URL?
- ¿La plantilla es por cliente (RUC) o también plantillas reutilizables compartidas?
