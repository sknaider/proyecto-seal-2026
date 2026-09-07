# Lector de expedientes de exportación (GTL / Henry) — prototipo etapa 1

**Ficha:** soul_v3.agent_tasks #1426 · **owner:** JARVIS · **estado:** prototipo medido, 2-sep-2026.

## Qué hace (etapa 1 de 3)
1. `pdftoppm -r 300 -png` → una imagen por página (a 150 dpi el formulario de la pág. 1 salía basura).
2. `tesseract -l spa+eng --psm 3` → texto por página (24 págs en 125 s en el Spark, CPU).
3. `clasificar_paginas.py <prefijo>` → tipo de documento por reglas de palabras clave + campos
   (RUC, AWB, factura, guía, peso, unidad, placa, fecha) + comparación contra etiquetas manuales.

## Medido sobre el expediente real de Henry (24 págs, 14 tipos, upload_1788377622606834978)
- Clasificación: **24/24**. Campos: RUC en 24/24, AWB en las 4 págs que lo llevan, facturas y guías electrónicas correctas.
- **Ya detecta sola la discrepancia de unidad**: pág. 3 (certificado de origen) `KG` vs `GR`/`GRAMOS` en el resto.
- **Salvedad:** las reglas se escribieron leyendo ESTE expediente: 24/24 no prueba generalización. Se necesitan
  2–3 expedientes de otras empresas (pedidos a Henry el 2-sep 14:42).
- Ruido OCR conocido: RUCs mal leídos (p.ej. 20614140891, 20614892201). El dígito de control (mod 11) caza
  el primero pero **el segundo pasa por azar** (1 de 11 lecturas ruidosas pasan). Regla: un RUC vale si
  (a) pasa el checksum **y** (b) aparece en ≥2 páginas del expediente; si no, «ilegible», nunca «distinto».

## Etapas 2 y 3 (no hechas)
2. Extracción por esquema por tipo (mejor con VLM local por página: gemma3:12b vía Ollama, o soul_vision).
3. Matriz de cruces con semáforo (RUC, peso+unidad, ley→fino, AWB, DAM, factura, cadena de guías, placas,
   fechas, consignatario) con la página y el recorte como evidencia de cada campo (idea de EAGLE, arXiv 2605.30698).

## Etapa 2 + 3 — extracción por esquema y cruces (2-sep-2026 21:57, medido)

`cruzar_campos.py <dir_ocr> <prefijo>` lee 3 páginas ya clasificadas (instrucción de embarque,
certificado de origen, factura comercial), extrae campos por esquema con **evidencia
página+línea**, y corre 6 cruces con semáforo OK / ERROR / REVISAR. Resultado sobre el
expediente real de Henry (OCR 300 dpi, `resultado_cruces_expediente_henry_20260902.json`):

```text
ERROR  unidad de peso        instrucción = GR   ·  certificado = KG        (5,993.30 kg de oro ≠ 5,993.30 g)
OK     bruto × ley = fino    5993.30 g × 92.60 % = 5549.80 g  = cantidad facturada
OK     cantidad × unitario   5549.80 × 139.23 = 772,698.65  = importe total
OK     AWB                   074-7269-0111 en instrucción y certificado
OK     peso bruto            5993.30 igual en instrucción / certificado / factura
ERROR  comprador             instrucción = GURUDEV INTERNATIONAL DMCC · factura = GURUDEV INTERNATIONAL FZCO
```

Los dos ERROR son exactamente los dos hallazgos que salieron leyendo el PDF a mano; los cuatro
OK son aritmética verificada, no coincidencia de texto. `REVISAR` = campo no leído (nunca se
inventa un valor: el humano abre la página que indica la evidencia).

Lecciones de esta etapa:
- **Los formularios escaneados parten etiqueta y valor en líneas distintas** («N° de bultos y
  peso total:» ↵ «1 BARRA - 5,993.30 GR.»). `find()` prueba la línea sola y luego
  etiqueta+siguiente-no-vacía; sin eso, 3 de 5 campos de la instrucción daban `None`.
- El regex numérico con grupos de miles obligatorios (`\d{1,3}(?:[.,]\d{3})*`) **no lee
  «5549.80» y trunca «5993.30» a «599»** sin fallar. Número general `\d[\d.,]*\d` + normalización.
- Los nombres (consignatario/comprador) hoy están **cableados** al expediente (GURUDEV/TIME AHEAD).
  Para generalizar hace falta la etapa 4: diccionario de entidades por cliente, o NER.

Pendiente para producción: 2-3 expedientes más de Henry (los regex están ajustados a UNO),
qué cruces le importan primero, y la entrada en GTL `/sistema` (subir PDF → matriz con semáforo).
