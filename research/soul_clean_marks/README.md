# SOUL clean-marks

Implementación nativa y conservadora para limpiar caracteres invisibles de
contenido propiedad de SOUL. Está inspirada en la clasificación pública de
[`watermarks-remover`](https://github.com/guillaumemeyer/watermarks-remover)
(MIT), pero el código fue escrito clean-room para SOUL.

## Qué sí garantiza

- Detecta y elimina por efecto Unicode invisible de alta confianza.
- Preserva ZWNJ/ZWJ por defecto para no romper idiomas ni emojis.
- Rechaza PDF, ZIP/DOCX, imágenes y otros binarios en la ruta textual.
- Escribe mediante reemplazo atómico y rechaza destinos symlink.
- Puede funcionar como gate: `inspect-text --fail-on-findings`.

```bash
python3 soul_clean_marks.py inspect-text archivo.md --fail-on-findings
python3 soul_clean_marks.py clean-text archivo.md -o archivo.cleaned.md
python3 soul_clean_marks.py rewrite-prompt archivo.md
python3 soul_clean_marks.py metadata-plan archivo.pdf
```

## Qué no garantiza

- La reescritura estadística es best-effort y requiere otro modelo; el módulo
  solo genera el prompt y exige validación semántica posterior.
- `metadata-plan` no modifica archivos. No se afirma limpieza de C2PA/EXIF/XMP
  hasta implementar y probar un pipeline por formato.
- No certifica que un detector privado de un proveedor falle.
- No debe usarse para retirar licencias de terceros ni atribuciones legalmente
  obligatorias.

El upstream completo soporta más contenedores y backends externos. Esta versión
prioriza el gate de texto/código de SOUL y evita importar su superficie de
dependencias o sus removedores de imagen no verificables localmente.

## Integración multi-formato fijada

`soul_full_clean.py` integra el backend MIT `watermarks-remover` para contenido
propio o autorizado sin convertir un checkout mutable en código de confianza:

1. verifica commit y SHA-256 de los 19 scripts y la licencia;
2. ejecuta una copia efímera con ambiente sin tokens ni credenciales;
3. escribe un candidato fuera del original;
4. reinspecciona y falla cerrado ante señales residuales;
5. promueve los bytes verificados de forma atómica y emite evidencia JSON.

```bash
python3 soul_full_clean.py capabilities
python3 soul_full_clean.py inspect documento.docx
python3 soul_full_clean.py clean documento.docx \
  -o documento.cleaned.docx --authorized-content --report evidencia.json
```

El soporte determinista abarca texto/código, Markdown/HTML, SVG,
PNG/JPEG/WebP, DOCX/ODT y PDF *best-effort*. El lock conserva la procedencia MIT
del [upstream](https://github.com/guillaumemeyer/watermarks-remover).

No existe una garantía universal de “todas las marcas”: detectores secretos,
marcas estadísticas sin clave, soft bindings y marcas de píxel/audio/video
pueden requerir un verificador o backend externo. El reporte dice exactamente
qué se midió y nunca se presenta como prueba de autoría humana.

SOUL tampoco importa a ciegas los removedores externos del upstream:
MarkDiffusion es Apache-2.0 pero solo verifica el mismo esquema/modelo;
reverse-SynthID es de investigación no comercial; y el backend CtrlRegen
referenciado no publica licencia. Los dos últimos quedan fuera del producto
comercial hasta que la licencia permita su distribución.
