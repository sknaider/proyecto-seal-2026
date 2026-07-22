# SOUL SDK — Diseño de Licenciamiento por DNI Soberano (TESTNET v3)

**Owner arquitectura:** JARVIS · **Gate de identidad/aislamiento:** ADA · **Fecha:** 2026-07-22
**Scope:** DISEÑO / TESTNET — **NO autoriza escrituras ni POC con mutación** (gate ADA).
**Orden de William:** "nuestro SDK debe ser difícil de replicar" + "cada DNI = una licencia SDK
de SOUL... enganchamos gratis pero dependientes" + "Jarvis avanza y luego coordina con ada".

> **v3 = v2 + los 4 ajustes del re-gate read-only de ADA (commit `78d5f7968`).** v2 ya había
> incorporado las 5 correcciones previas. Integrado por JARVIS. Estado: **borrador, re-gate ADA.**
>
> **GATE DE GOBERNANZA (NEXUS + ADA):** SSAI M2 = diseño/mock/testnet. Emitir DNIs de PRODUCCIÓN
> a externos requiere **William OK + auditoría NEXUS**. Este doc NO habilita producción ni escrituras.

---

## 1. La tesis del foso — foso por valor, no cautiverio

Una API REST de memoria se copia en un fin de semana. Lo que **no** se replica: memoria atada a
una **identidad soberana firmada** verificable (`soul_dni` + Ed25519).

**El foso NO es cautiverio (corrección #5 de v2).** Moat = **valor acumulado + pruebas
criptográficas + interoperabilidad + servicio superior**, con **exportación segura**. El usuario
siempre puede llevarse sus datos; reproducir el sistema completo es lo caro. Mismo criterio que
`export ≠ leak`.

## 2. Infra que YA existe (verificada por efecto, corregida por ADA 2026-07-22)

| Tabla / rol | Estado | Rol en el modelo |
|---|---|---|
| `soul_v3.ssai_registry` | 9 filas, `candidate/TOFU_UNANCHORED` | **El DNI**: `agent, soul_id, soul_dni, lifecycle_state, assurance` |
| `soul_v3.ssai_keys` | **0 filas** | **Llaves PÚBLICAS** Ed25519 por DNI (solo públicas) — a poblar |
| `soul_v3.api_keys` | 41 filas | Credenciales base (rotables) — NO son la licencia (ver §3) |
| roles `mcp_runtime_*` / `soul_sdk_*` | vivos | Enforcement DB per-agente/per-tenant (P1 sellado por ADA) |
| **375** políticas RLS | (corregido de 361) | Aislamiento multi-inquilino |

## 3. El modelo: TRES niveles — identidad, licencia, credenciales (ajuste #1 de ADA)

```
DNI (identidad soberana, ssai_registry)
  ↕  binding estable
sdk_license (plan + cuota + scopes + estado + vigencia)   ← la LICENCIA, entidad estable
  ↕  1..N credenciales que la referencian
credenciales rotables (api_keys)                          ← ROTAN sin tocar identidad/plan/cuota
```

**Regla dura (ajuste #1):** la **licencia NO depende de `api_key_id`**. Rotar/revocar una
credencial **no** cambia identidad, plan ni cuota — solo cambia la credencial. La cuota/plan
cuelgan de `sdk_license` (ligada al DNI), no de la key. Una credencial referencia su licencia;
la licencia nunca referencia una key concreta.

**El "dulce a un bebé" (sin cautiverio):** DNI gratis, onboarding trivial (`tier=free`). El
usuario se queda por valor + interop + servicio, y puede EXPORTAR cuando quiera.

## 4. Wiring pendiente — v3

### 4.1 Vínculo por tabla puente, licencia desacoplada de la credencial (correcciones #1 v2 + #1 v3)

- **NO** `api_keys.soul_dni`. Identidad y credencial desacopladas.
- Entidad **`sdk_license`** (plan, cuota, scopes, estado, `valid_from/expires_at`) ligada al DNI
  (`registry_id`). Las credenciales (`api_keys`) referencian la **licencia**, no al revés.
- Unicidad/estado explícitos; el `soul_dni` público **no** se usa como tracking ID.

### 4.2 Emisor dedicado + SECURITY DEFINER endurecido (corrección #2 v2 + ajuste #4 v3)

- Rol **`svc_ssai_issuer_testnet`**: LOGIN mínimo + rol **`NOLOGIN`** de capacidad. Solo
  `EXECUTE` sobre funciones auditadas; **sin escrituras directas** a tablas de identidad.
- **`ssai_keys` guarda ÚNICAMENTE claves PÚBLICAS.** La privada se genera y **custodia FUERA de
  PostgreSQL**.
- **Funciones `SECURITY DEFINER` (ajuste #4):** owner **`NOLOGIN`**; **`search_path` fijo**
  (`SET search_path = soul_v3, pg_temp`); **nombres calificados** en todo objeto; **`PUBLIC
  EXECUTE` revocado** (grant explícito solo al rol emisor); **auditoría idempotente** (misma
  operación no duplica efecto ni registro).

### 4.3 Broker de identidad separado del MCP (corrección #3 v2)

- Daemon protegido e independiente, **sin acceso a memorias**. Verifica challenge/firma,
  revocación y licencia; emite **token corto**. El MCP/SDK solo CONSUME claims verificados.

### 4.4 Token corto — firma y claims mínimos (ajuste #3 v3)

- **Firmado** (Ed25519; `alg` explícito, `kid` para rotación de llave).
- **Claims mínimos:** `iss`, `aud`, `sub`, `tenant`, `license_id`, `scopes`, `env`,
  `iat`/`nbf`/`exp`, `jti`, `kid`.
- **Rechazo duro:** algoritmo desconocido/`none` → deny; `aud` desconocida → deny.
- **Anti-replay + revocación:** `jti` con ventana + chequeo de revocación (de credencial y de
  licencia) antes de honrar el token. `exp` corto.

### 4.5 Capa de autorización (modelo de ADA, registrado)

`DNI = identidad` · `sdk_license = plan/cuota/permisos` · `token corto firmado = autz de sesión`
· `RLS/roles = frontera de datos`. **El DNI por sí solo NO concede acceso** ni es API key.

## 5. Testnet en DB SEPARADA — sin FK cross-DB (corrección #4 v2 + ajuste #2 v3)

- DB, roles, claves y auditoría **propias** del testnet. **No mezclar** con los 9 DNIs reales.
- **PostgreSQL no permite FKs entre bases** (ajuste #2): el testnet usa **fixtures/tablas
  LOCALES** que espejan el esquema; **cero FK o lectura hacia producción**.
- Un esquema dentro de prod queda solo como fallback y requeriría otro gate.

## 6. Preguntas de coordinación — RESUELTAS por ADA (v2) + refinadas (v3)

1. ¿FK `api_keys.soul_dni`? → **No**; tabla puente + licencia como entidad estable (§3, §4.1).
2. ¿Emisión por `mcp_runtime` o rol dedicado? → **Rol dedicado**, SECURITY DEFINER endurecido (§4.2).
3. ¿Broker en superficie del MCP? → **No**; separado sin acceso a memorias (§4.3).
4. ¿Testnet aislado? → **DB separada, fixtures locales, sin FK cross-DB** (§5).

## 7. Scope y gate

- **En scope AHORA:** este doc (read-only) → **re-gate de ADA**.
- **FUERA de scope sin gate:** POC con escrituras, poblar `ssai_keys`, crear roles/tablas, emitir
  DNIs. **Sin POC ni escrituras todavía** (orden de ADA).
- **Producción externa:** William OK + auditoría NEXUS (SSAI M2).

---
**Estado:** v3 con los 4 ajustes del re-gate de ADA integrados por JARVIS. Para su re-gate
read-only. Ningún cambio autoriza escrituras.
