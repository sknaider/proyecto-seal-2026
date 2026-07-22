# SOUL SDK — Contrato de Portabilidad de Datos (v2, DISEÑO)

**Owner arquitectura:** JARVIS · **Gate de seguridad/aislamiento:** ADA · **Fecha:** 2026-07-22
**Scope:** DISEÑO / SPEC — **NO implementa; artefacto para re-gate read-only de ADA.**
**Origen:** orden de William (foso = valor/identidad, no cautiverio) + contrato de primera clase
pedido por ADA. Consistente con `export ≠ leak` y la corrección #5 del doc DNI-licensing.

> **v2 = v1 + las 5 precisiones del gate read-only de ADA (commit `259951ba8`).** Integrado por
> JARVIS. Estado: **borrador, re-gate ADA. Sin implementación.**

---

## 1. Principio (portabilidad = feature del foso, no fuga)

El usuario DEBE poder llevarse **sus** datos — la portabilidad es confianza, y la confianza es
parte del moat. El foso = **calidad + identidad verificable + interoperabilidad + servicio
superior**. La exportación entrega el **contenido autorizado del tenant**; NUNCA la **receta de
reproducción del sistema**.

```
EXPORTA (datos propios/autorizados)     NUNCA EXPORTA
├─ contenido del tenant                 ├─ secretos / credenciales / claves privadas
├─ timestamps bitemporales + tombstones ├─ políticas RLS / lógica de aislamiento interna
├─ procedencia propia                   ├─ embeddings propietarios (vectores) *
├─ relaciones/grafo AUTORIZADo          ├─ datos de otros tenants / DMs o grafos de terceros
└─ manifiesto firmado                   └─ mecanismo de assurance
```
\* Embeddings se excluyen, pero el **manifiesto incluye modelo/versión** para permitir
recomputarlos (precisión ADA).

## 2. Snapshot consistente (precisión #1 de ADA)

- La exportación corre bajo un **snapshot consistente** (p.ej. transacción `REPEATABLE READ` /
  export point) para que el dump sea un estado coherente, no un mosaico de lecturas.
- Incluir **tombstones** (registros invalidados/borrados marcados) para que el import reconstruya
  la historia real, no solo lo vivo.
- **IDs portables** y **referencias estables** entre registros (el import resuelve relaciones sin
  depender de IDs internos efímeros).

## 3. Formato (abierto y versionado)

- **Base JSONL** (streaming, cualquier lenguaje); **Parquet opcional** (columnar).
- `schema_version` + `contract_version` explícitos en cada export.

## 4. Qué se exporta (datos propios/AUTORIZADOS — precisión #5 de ADA)

- Contenido, timestamps bitemporales (+ tombstones), procedencia y relaciones del tenant.
- **"Del tenant" NO basta** para DMs o **grafos compartidos**: exportar únicamente datos
  **propios o explícitamente autorizados**; excluir lo de terceros aunque toque al tenant.
- **Pseudonimizar IDs internos** (no filtrar identificadores internos del sistema como
  correlacionables).
- Alcance derivado **server-side** de la identidad, nunca de un parámetro del cliente.

## 5. Manifiesto firmado + criptografía concreta (precisión #2 de ADA)

- Manifiesto: conteos por tipo, **checksums**, `schema_version`, `contract_version`, `tenant`,
  ventana temporal, modelo/versión de embeddings, timestamp.
- **Canonicalización del manifiesto** antes de firmar (bytes deterministas; misma entrada → mismos
  bytes) y **firma sobre los bytes exactos** canonicalizados.
- **`kid` + `alg`** explícitos; **clave separada por propósito** (firma de manifiesto ≠ clave de
  cifrado ≠ clave de identidad).
- **Cifrado envelope por export** (clave de datos por export, envuelta por KEK). **Una URL firmada
  NO equivale a cifrado** (precisión ADA): la URL controla acceso, el envelope protege el contenido.

## 6. Entrega + autorización (precisión #3 de ADA)

- **Asíncrona** (job: encolar → generar → notificar), **cifrada** (envelope §5), **auditable**
  (con redacción por sensibilidad en el log), **con expiración** (artefacto y URL caducan).
- **Autorización:** scope explícito **`data:export`**; **reautenticación / step-up** para
  disparar un export (operación sensible); **operador least-privilege** (sin superusuario);
  **tenant derivado server-side**.

## 7. Verificación — canarios sintéticos, NUNCA secretos reales (precisión #4 de ADA)

- **NUNCA buscar un secreto real** en el output (grep del secreto real es el anti-patrón —
  corrige mi v1). En su lugar: sembrar **canarios sintéticos** (valores marcadores conocidos) en
  campos sensibles y verificar por efecto que **no aparecen** en el export.
- **Escaneo estructurado** del JSONL/Parquet (por campo/esquema), no grep de texto plano.
- **Round-trip solo en DB aislada** (tenant/DB limpia), con **limpieza** al terminar: exportar de
  A → importar en B aislado → contenido/relaciones/procedencia cuadran con el manifiesto
  (conteos + checksums) → teardown.
- **Prueba negativa:** el export de A no contiene datos de otro tenant ni canarios de otro origen
  (conteos cruzados por tenant), verificado por estructura.

## 8. Exclusiones estrictas (la línea del foso)

Secretos/credenciales/claves privadas; políticas internas/RLS; embeddings propietarios (con
modelo/versión en el manifiesto para recomputar); datos de otros tenants, DMs o grafos de terceros.

## 9. Scope y gate

- **En scope AHORA:** este spec (diseño) → **re-gate read-only de ADA antes de implementar.**
- **FUERA de scope sin gate:** construir el endpoint, tocar datos reales, generar un export vivo.

---
**Estado:** v2 con las 5 precisiones de ADA integradas por JARVIS. Para su re-gate read-only.
No implementa nada.
