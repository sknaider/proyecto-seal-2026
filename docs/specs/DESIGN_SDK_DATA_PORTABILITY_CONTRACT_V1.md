# SOUL SDK — Contrato de Portabilidad de Datos (v1, DISEÑO)

**Owner arquitectura:** JARVIS · **Gate de seguridad/aislamiento:** ADA · **Fecha:** 2026-07-22
**Scope:** DISEÑO / SPEC — **NO implementa; artefacto para gate read-only de ADA antes de construir.**
**Origen:** orden de William (foso = valor/identidad, no cautiverio) + contrato de primera clase
pedido por ADA (22-jul). Consistente con `export ≠ leak`
(`DESIGN_SDK_EXTERNAL_STANDARDS_ADAPTERS_V1.md`) y la corrección #5 del doc DNI-licensing.

---

## 1. Principio (por qué la portabilidad es un FEATURE del foso, no una fuga)

El usuario DEBE poder llevarse **sus** datos cuando quiera — la portabilidad es **confianza**,
y la confianza es parte del moat. El foso NO es atrapar los datos; es **calidad + identidad
verificable + interoperabilidad + servicio superior**. Lo que la exportación entrega es el
**contenido del tenant**; lo que NUNCA cruza es la **receta de reproducción del sistema**.

```
EXPORTA (del tenant)                    NUNCA EXPORTA (del sistema)
├─ contenido de memorias                ├─ secretos / credenciales / API keys
├─ timestamps bitemporales              ├─ políticas RLS / lógica de aislamiento interna
├─ cadena de procedencia (la propia)    ├─ embeddings propietarios (vectores del modelo)
├─ relaciones/grafo del propio tenant   ├─ datos de OTROS tenants (aislamiento duro)
└─ manifiesto firmado (integridad)      └─ mecanismo de assurance / llaves privadas
```

## 2. Formato (abierto y versionado — requisito ADA)

- **Base: JSONL** (una línea por registro, streaming-friendly, cualquier lenguaje lo lee).
- **Opcional: Parquet** (columnar, para volúmenes grandes / analítica).
- **Versionado explícito:** cada export lleva `schema_version` y `contract_version` para que un
  import futuro sepa interpretarlo. Formato abierto = el usuario no depende de nosotros para leerlo.

## 3. Qué se exporta (datos del propio tenant, requisito ADA)

- **Contenido** de las memorias/registros del tenant.
- **Timestamps bitemporales** (`valid_from` / `invalid_at`) — la historia versionada, no solo el
  estado vivo.
- **Procedencia** — la cadena del propio tenant (de dónde vino cada dato), no el mecanismo interno.
- **Relaciones / grafo** del propio tenant.
- Alcance estricto: **solo el tenant que exporta**, derivado server-side de su identidad
  (`sdk_current_tenant_id()` / claim verificado), NUNCA de un parámetro del cliente.

## 4. Manifiesto firmado (integridad verificable — requisito ADA)

- Cada export produce un **manifiesto** con: **conteos** por tipo de registro, **checksums**
  (p.ej. SHA-256 por archivo/shard), `schema_version`, `contract_version`, `tenant_id`,
  ventana temporal, y timestamp de emisión.
- El manifiesto va **firmado** (Ed25519 con la llave del sistema; el receptor verifica con la
  pública) → el usuario puede **probar la integridad** de lo que recibió y que SOUL lo emitió.
- Nunca incluye secretos ni datos de otros tenants en los conteos/manifiesto.

## 5. Entrega: asíncrona, cifrada, auditable, con expiración (requisito ADA)

- **Asíncrona:** un export grande es un job (encolar → generar → notificar), no una request síncrona.
- **Cifrada:** el artefacto en reposo/tránsito va cifrado; entrega por URL de un solo uso o
  descarga autenticada.
- **Auditable:** cada export se registra (quién, qué tenant, cuándo, qué ventana, conteos) —
  con redacción por sensibilidad en el propio log (sin volcar secretos).
- **Con expiración:** el artefacto y su URL **caducan**; no queda un dump perpetuo accesible.

## 6. Exclusiones estrictas (requisito ADA — la línea del foso)

Se EXCLUYE por diseño, verificado por efecto (no por confianza):
- Secretos, credenciales, API keys (redacción por SENSIBILIDAD en la fuente).
- Políticas internas / lógica de aislamiento RLS.
- **Embeddings propietarios** (los vectores del modelo — el usuario exporta su contenido, no
  nuestra representación aprendida).
- **Datos de otros tenants** (el export corre bajo el clamp de aislamiento; forjar tenant no amplía).

## 7. Verificación obligatoria (requisito ADA)

- **Round-trip en un tenant LIMPIO:** exportar de tenant A → importar en un tenant vacío B →
  el contenido/relaciones/procedencia se reconstruyen y **cuadran con el manifiesto** (conteos +
  checksums). Prueba por EFECTO de que la exportación es completa y correcta.
- **Prueba negativa adversarial:** el export de A **no** contiene datos de otro tenant ni
  secretos — `grep` del secreto real = 0 apariciones; conteos cruzados por tenant.

## 8. Scope y gate

- **En scope AHORA:** este spec (diseño) → **gate read-only de ADA antes de implementar.**
- **FUERA de scope sin gate:** construir el endpoint, tocar datos reales, generar un export vivo.
- El endpoint de export entra detrás del gate de gobernanza para producción externa.

---
**Estado:** spec de contrato de portabilidad por JARVIS integrando los 6 requisitos de ADA.
Artefacto para su gate read-only. No implementa nada.
