# SOUL SDK — Diseño de Licenciamiento por DNI Soberano (TESTNET v2)

**Owner arquitectura:** JARVIS · **Gate de identidad/aislamiento:** ADA · **Fecha:** 2026-07-22
**Scope:** DISEÑO / TESTNET — **NO autoriza escrituras ni POC con mutación** (gate ADA).
**Orden de William:** "nuestro SDK debe ser difícil de replicar" + "cada DNI (identidad de
agentes) = una licencia SDK de SOUL... enganchamos gratis pero dependientes de nosotros" +
"Jarvis avanza y luego coordina con ada".

> **v2 = v1 + las 5 correcciones de gate de ADA (22-jul, revisó commit `8b73b1ddb` + esquemas
> vivos).** Integradas por JARVIS. Estado: **borrador válido, listo para gate read-only de ADA.**
>
> **GATE DE GOBERNANZA (NEXUS + ADA, respetado):** el contrato SSAI M2 tiene scope
> diseño/mock/testnet. Emitir DNIs de PRODUCCIÓN a externos requiere **William OK + auditoría
> NEXUS**. Este documento diseña el flujo; NO habilita producción externa ni escrituras.

---

## 1. La tesis del foso (por qué es difícil de replicar) — corregida

Una API REST de memoria se copia en un fin de semana. Lo que **no** se replica: memoria atada
a una **identidad soberana firmada** verificable (`soul_dni` + Ed25519). Robar/revender una API
key es trivial; falsificar una identidad soberana firmada, no.

**El foso NO es cautiverio (corrección #5 de ADA, adoptada).** El moat correcto = **valor
acumulado + pruebas criptográficas + interoperabilidad + servicio superior**, con **exportación
segura**. El usuario SIEMPRE puede llevarse sus datos. Reproducir el sistema completo (identidad
firmada + historia bitemporal + procedencia + políticas + aprendizaje) es lo caro — esa es la
defensa, no atrapar al usuario. Mismo criterio que `export ≠ leak` del doc de adaptadores.

## 2. Infra que YA existe (verificada por efecto, 2026-07-22)

| Tabla / rol | Estado | Rol en el modelo |
|---|---|---|
| `soul_v3.ssai_registry` | 9 filas | **El DNI**: `agent, soul_id, soul_dni, lifecycle_state, assurance` |
| `soul_v3.ssai_keys` | 0 filas | **Llaves PÚBLICAS** Ed25519 por DNI (solo públicas — ver #2) — a poblar |
| `soul_v3.api_keys` | 41 filas | **La LICENCIA base**: `tier, scope, rate_limit, mem_quota, agent_quota, org_id, active` |
| roles `mcp_runtime_*` / `soul_sdk_*` | vivos | Enforcement DB per-agente/per-tenant (P1 sellado por ADA) |
| 361 políticas RLS | — | Aislamiento multi-inquilino |

**Hallazgo:** el schema de licencia (`api_keys`) YA existe. Falta CABLEAR con identidad — pero
**desacoplado** (corrección #1), no fusionado.

## 3. El modelo: DNI ↔ licencia, DESACOPLADOS

```
Entidad (agente hoy / externo mañana)
  → tiene un soul_dni en ssai_registry (identidad soberana)
  → con un par Ed25519: PÚBLICA en ssai_keys, PRIVADA custodiada FUERA de Postgres (#2)
  → su licencia (api_keys) se VINCULA vía tabla puente sdk_license_bindings (#1), NO por FK directa
  → un BROKER de identidad separado (#3) verifica firma/challenge + revocación + licencia
     y emite una credencial CORTA; el MCP/SDK solo consume claims verificados
  → la RLS aísla la memoria por tenant (P1)
```

**El "dulce a un bebé" (sin cautiverio):** DNI gratis, onboarding trivial (`tier=free`). El
usuario se queda por **valor acumulado + interop + servicio**, y puede EXPORTAR cuando quiera.
Dependencia por valor, no por candado.

## 4. Wiring pendiente — v2 con las 5 correcciones de ADA

### 4.1 Vínculo licencia↔identidad = tabla puente, NO columna (corrección #1)

- **NO** agregar `api_keys.soul_dni`. Mantener credencial e identidad **desacopladas**.
- Crear **`sdk_license_bindings(api_key_id, registry_id, environment, status, scopes,
  valid_from, expires_at)`** con **FKs explícitas** (a `api_keys` y `ssai_registry`) y
  **unicidad** explícita (p.ej. un binding activo por `(api_key_id, environment)`).
- **No usar el `soul_dni` público como tracking ID.** El binding referencia `registry_id`
  interno; el DNI público no se convierte en llave de correlación.

### 4.2 Emisor dedicado, nunca `mcp_runtime` (corrección #2)

- Rol **`svc_ssai_issuer_testnet`**: LOGIN mínimo + un rol **`NOLOGIN`** de capacidad.
- Solo **`EXECUTE`** sobre funciones **`SECURITY DEFINER` auditadas** — **sin escrituras
  directas** a las tablas de identidad.
- **`ssai_keys` guarda ÚNICAMENTE claves PÚBLICAS.** La privada se **genera y custodia FUERA
  de PostgreSQL** (nunca toca una fila de la DB).

### 4.3 Broker = control-plane de identidad SEPARADO del MCP (corrección #3)

- Daemon **protegido e independiente**, **sin acceso a memorias**.
- Responsabilidad: verificar challenge/firma, revocación y licencia; emitir **credencial corta**.
- El **MCP/SDK solo CONSUME claims verificados** — no habla con las tablas de identidad ni
  entra en la superficie protegida del MCP runtime.

### 4.4 Auth del SDK por claim verificado

- El SDK (:8768) hoy usa `api_key`/Bearer (auth por inquilino ya existe, verificado). Se
  agrega: el broker verifica la firma Ed25519 del DNI y **resuelve la licencia vía
  `sdk_license_bindings`**, entregando un claim corto que el SDK consume.
- Exponer con TLS + rate-limit real (hoy `:8768` bindea localhost; falta reverse-proxy).

## 5. Testnet en DB SEPARADA (corrección #4)

- **Obligatorio:** base de datos, roles, claves y auditoría **propias** para el testnet.
- **No mezclar** DNIs de prueba con los **nueve reales** de `ssai_registry`.
- Un esquema dentro de producción queda **solo como fallback** y **requeriría otro gate**.

## 6. Preguntas de coordinación — RESUELTAS por ADA

1. ¿FK `api_keys.soul_dni` colisiona con el aislamiento? → **Sí; NO usar FK directa.** Tabla
   puente `sdk_license_bindings` desacoplada (#1).
2. ¿Emisión por `mcp_runtime` o rol dedicado? → **Rol dedicado `svc_ssai_issuer_testnet`**,
   solo EXECUTE sobre SECURITY DEFINER; privadas fuera de Postgres (#2).
3. ¿El broker entra en la superficie protegida del MCP? → **No; broker separado sin acceso a
   memorias** (#3).
4. ¿Esquema/tenant testnet aislado? → **DB separada completa**, no solo esquema (#4).

## 7. Scope y gate

- **En scope AHORA:** este documento actualizado (read-only) → **gate read-only de ADA**.
- **FUERA de scope sin gate:** cualquier POC con **escrituras**, poblar `ssai_keys`, crear
  roles/tablas, o emitir DNIs. **No POC con mutación todavía** (orden explícita de ADA).
- **Producción externa:** William OK + auditoría NEXUS (SSAI M2).

---
**Estado:** v2 con las 5 correcciones de gate de ADA integradas por JARVIS. Listo para su gate
read-only. Ningún cambio autoriza escrituras.
