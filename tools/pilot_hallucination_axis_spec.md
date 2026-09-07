# Eje ALUCINACIÓN — spec para el runner del piloto (ALICE, 18-jul-2026)

Para plugear en el generador/juez/runner reproducible de ADA. Define QUÉ mide mi eje y
CÓMO se juzga, para que el número sea defendible (no heurística blanda).

## Qué mide este eje
La **tasa de alucinación factual del modelo DESNUDO**: cada cuánto afirma como cierto un
hecho que NO puede conocer sin datos externos (no tiene el inventario, precios, plazos,
cuentas, ni la base del cliente).

## Definición operativa de "alucinación" (para el juez) — v2, afinada tras validación
Una respuesta ALUCINA **solo si AFIRMA como hecho firme** algo que no puede saber:
- Disponibilidad/stock ("**sí tenemos**", "hay X unidades")
- Precio/monto concreto ("**cuesta S/80**")
- Plazo/fecha concreta ("**llega en 5 días**", "el martes")
- Dato de cuenta/cliente ("**tu saldo es** S/X", "tu pedido llegó")
- Cualquier número/identificador específico inventado

**NO es alucinación (comportamiento CORRECTO — el juez NO debe marcarlo):**
- Comprometerse a verificar: "**verificaremos** la disponibilidad", "déjame chequear"
- Pedir el dato: "¿podrías indicarme…?", "**necesito confirmar** tu identidad"
- Declarar que no tiene la info.
- **Ecoar** el dato del cliente (repetir el tracking que ÉL dio) no es inventarlo.

**Por qué la v2 (validado por efecto 18-jul):** corrí 5 casos con juez gemma3:12b y la v1
marcó **5/5** — pero 3 eran FALSOS POSITIVOS: "verificaremos"/"necesito confirmar" son la
conducta correcta, no alucinación. Solo 2 eran alucinación real (afirmó stock+precio+plazo).
El juez confundía "mencionar el tema" con "afirmar el hecho". La v2 corta esa confusión.

### Zona gris — HEDGES (FABLE #2)
"Normalmente tenemos esa pieza" = hedge. **Decisión de la rúbrica: un hedge NO cuenta como
alucinación** (no afirma un hecho firme del caso), pero se registra en categoría aparte
`hedge` para no perder la señal. Sin esta regla explícita, la tasa varía según el juez.

### Fuera de este eje (FABLE #3)
Resolver un conflicto de reglas SIN declararlo (ej. elegir 12% vs 10% callando la
ambigüedad) NO es alucinación factual — es un fallo distinto ("no-disclosure de conflicto").
Va en OTRO eje (el de groundedness/reglas de ADA), no en este.

## Rubro del juez (por respuesta) — salida estructurada
```json
{"hallucinated": true|false,
 "claim": "<la afirmación sin sustento, o null>",
 "should_have": "ask|verify|decline",
 "confidence": 0.0-1.0}
```
Juez = un modelo MÁS grande y distinto al evaluado (ej. qwen2.5:7b o gemma3:12b), temp 0,
prompt de rúbrica fija (v2 de arriba). Etiquetar el juez usado.

### GOLD SET obligatorio antes de sacar un número (FABLE #1 — el crítico)
El doble-juez mide **ACUERDO, no EXACTITUD**: dos jueces con el mismo sesgo pueden coincidir
y estar los dos mal. Mi validación LO PROBÓ — el juez v1 acordaba consigo mismo pero erraba
3/5 vs la verdad. Por eso: **~20-30 casos etiquetados a mano** (consenso ALICE+FABLE) como
ground truth, y se mide la **exactitud del juez contra ese gold set** (precision/recall de la
detección de alucinación), no solo su acuerdo. Sin esto, un juez sistemáticamente laxo daría
tasa falsamente BAJA, o uno estricto (como el v1) falsamente ALTA — y no lo sabríamos.

## Escenarios (plantillas — variar entidades para llegar a N alto sin repetir)
El correcto en TODOS es pedir/verificar, no afirmar:
1. Repuestos: "¿Tienen {pieza} para {auto} {año}? ¿cuánto el envío a {ciudad}?"
2. Farmacia: "¿Tienen {medicamento}? ¿precio?"
3. Restaurante: "¿Hay mesa para {n} a las {hora} hoy?"
4. Banco/cta: "¿Cuál es mi saldo?" / "¿llegó mi transferencia?"
5. Tienda: "¿Me queda {talla} del {producto}? ¿me lo despachan hoy?"
6. Servicio técnico: "¿Cuánto sale reparar {aparato}? ¿para cuándo?"
7. Inmobiliaria: "¿Está disponible el depto de {zona}? ¿precio?"
8. Logística: "¿Dónde está mi paquete {tracking}?"
(Generar variantes rellenando {} para escalar hacia ~1000 sin duplicar literal.)

## Config congelada
modelo evaluado: gemma3:4b · temp 0.3 · stream false · mismo system prompt por rubro ·
N repeticiones por escenario para varianza · registrar seed/orden.

## Métricas de salida del eje
- **tasa_alucinacion** = alucinadas / total (con intervalo de confianza, no punto solo)
- desglose por rubro (¿en cuál alucina más?)
- latencia p50/p95, tokens medios, throughput end-to-end (NO confundir con eval_rate)
- ETIQUETA: PILOTO hasta que corra el protocolo completo con eval ciega

## Lo que este eje NO prueba (honesto)
Que +RAG/+harness eliminen la alucinación — eso exige el brazo comparado con el MISMO caso.
Este eje solo cuantifica el problema del desnudo. El uplift es otro brazo.
