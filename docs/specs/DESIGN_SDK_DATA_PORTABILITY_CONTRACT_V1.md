# SOUL SDK — Contrato de Portabilidad de Datos (v4, DISEÑO)

**Owner arquitectura:** JARVIS · **Gate de seguridad/aislamiento:** ADA · **Fecha:** 2026-07-22
**Scope:** DISEÑO / SPEC — **NO implementa; artefacto para re-gate read-only de ADA.**
**Origen:** orden de William (foso = valor/identidad, no cautiverio) + contrato de primera clase
pedido por ADA. Consistente con `export ≠ leak` y la corrección #5 del doc DNI-licensing.

> **v4 = v3 + la SELECCIÓN NORMATIVA ÚNICA de cripto exigida en el re-gate de ADA** (fijar una
> primitiva, no alternativas). v3 integró los 3 residuos técnicos; v2 las 5 precisiones. Integrado
> por JARVIS. Estado: **borrador, re-gate ADA. Sin implementación.**

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

## 5. Manifiesto firmado + criptografía concreta (precisión #2 + residuo #1/#2 v3)

- Manifiesto: conteos por tipo, checksums (ver §7), `schema_version`, `contract_version`,
  **`export_subject_id`**, ventana temporal, modelo/versión de embeddings, timestamp.
- **`export_subject_id` NO correlacionable (residuo #2 v3):** el manifiesto **no** lleva el
  `tenant_id` interno crudo; lleva un identificador **opaco, per-export, no correlacionable**
  (derivado server-side, sin permitir reconstruir el tenant interno). Igual para cualquier ID
  interno referenciado (pseudonimizado).

### 5.1 Bytes exactos de la firma — SELECCIÓN NORMATIVA ÚNICA (residuo #1 v3, re-gate)

Sin alternativas: "bytes exactos" exige UNA opción fija, no un menú.

- **Canonicalización: RFC 8785 (JSON Canonicalization Scheme), UTF-8, OBLIGATORIO.** Sin encoding
  alternativo. Misma entrada → exactamente los mismos bytes en cualquier implementación.
- **Firma: Ed25519** (consistente con el DNI-licensing). `alg = "Ed25519"` fijo; rechazo de `alg`
  desconocido o `none`.
- **Preimagen EXACTA con dominio fijo:** la firma se calcula sobre
  `DOMAIN_TAG_BYTES || JCS(manifest_UTF8)`, donde `DOMAIN_TAG_BYTES` = los bytes UTF-8 del literal
  fijo **`"soul-export-manifest-v1\x00"`** (separador `\x00` incluido). Nada más entra en la
  preimagen. Firma sobre esos bytes, no sobre una re-serialización.
- **`kid` explícito** identifica la llave. **Clave separada por propósito** (dura): la de firma de
  manifiesto ≠ la KEK de datos ≠ la de identidad; ninguna se reutiliza entre propósitos.

### 5.2 Cifrado de datos — SELECCIÓN NORMATIVA ÚNICA (residuo #1 v3, re-gate)

- **AEAD: AES-256-GCM, OBLIGATORIO** (sin ChaCha como alternativa). **Nonce: 96 bits (12 bytes),
  generado por CSPRNG, ÚNICO por export** (nunca reusar un nonce con la misma clave).
- **AAD canonicalizado:** el AAD son los bytes **JCS** de la cabecera `{export_subject_id,
  schema_version, contract_version, kid}` — así el ciphertext queda ligado a su metadato exacto y
  no se puede mezclar cabecera de un export con datos de otro.
- **Envoltura de la clave de datos (KEK): AES-256-KW (RFC 3394), OBLIGATORIO.** La clave de datos
  por export se envuelve con la KEK vía key-wrap determinista RFC 3394.
- **URL firmada ≠ cifrado** (precisión ADA): la URL controla ACCESO; el AEAD protege el CONTENIDO.
- **Qué cubre cada checksum (residuo #1/#3):** **`plaintext_sha256`** = SHA-256 sobre los **bytes
  PLAINTEXT canónicos exactos** del export (integridad semántica, sobrevive al descifrado),
  declarado con ese nombre en el manifiesto. El **tag AEAD (GCM)** cubre el **CIPHERTEXT**
  (no-manipulación en transporte). Son dos cosas distintas y el manifiesto las declara por separado.

## 6. Entrega + autorización (precisión #3 de ADA)

- **Asíncrona** (job: encolar → generar → notificar), **cifrada** (envelope §5), **auditable**
  (con redacción por sensibilidad en el log), **con expiración** (artefacto y URL caducan).
- **Autorización:** scope explícito **`data:export`**; **reautenticación / step-up** para
  disparar un export (operación sensible); **operador least-privilege** (sin superusuario);
  **tenant derivado server-side**.

## 7. Verificación — canarios sintéticos + DOS chequeos distintos (precisión #4 + residuo #3 v3)

- **NUNCA buscar un secreto real** en el output (grep del secreto real es el anti-patrón —
  corrige mi v1). En su lugar: sembrar **canarios sintéticos** (valores marcadores conocidos) en
  campos sensibles y verificar por efecto que **no aparecen** en el export.
- **Escaneo estructurado** del JSONL/Parquet (por campo/esquema), no grep de texto plano.

### 7.1 Dos verificaciones SEPARADAS (residuo #3 v3)

Son cosas distintas y no deben confundirse:

1. **Integridad binaria del artefacto:** el **checksum del archivo** (sobre los bytes exportados)
   prueba que el artefacto **no se corrompió** en reposo/tránsito. Es un hash binario exacto.
2. **Equivalencia SEMÁNTICA del round-trip:** exportar de A → importar en B aislado → el
   **contenido, relaciones, procedencia y conteos** cuadran con el manifiesto. **NO se exige el
   mismo hash binario** — al importar, los IDs se **remapean** (ver IDs portables §2), así que los
   bytes difieren legítimamente. La equivalencia es por CONTENIDO/estructura, no por hash del archivo.

### 7.2 Entorno y prueba negativa

- **Round-trip solo en DB aislada** (tenant/DB limpia), con **limpieza (teardown)** al terminar.
- **Prueba negativa:** el export de A no contiene datos de otro tenant ni canarios de otro origen
  (conteos cruzados por tenant), verificado por estructura.

## 8. Exclusiones estrictas (la línea del foso)

Secretos/credenciales/claves privadas; políticas internas/RLS; embeddings propietarios (con
modelo/versión en el manifiesto para recomputar); datos de otros tenants, DMs o grafos de terceros.

## 9. Scope y gate

- **En scope AHORA:** este spec (diseño) → **re-gate read-only de ADA antes de implementar.**
- **FUERA de scope sin gate:** construir el endpoint, tocar datos reales, generar un export vivo.

---
**Estado:** v4 con la selección normativa única de cripto (RFC 8785 / Ed25519 / AES-256-GCM /
AES-256-KW / SHA-256) integrada por JARVIS. Para su re-gate read-only. No implementa nada.
