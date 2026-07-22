# SOUL SDK — Diseño de Licenciamiento por DNI Soberano (TESTNET v1)

**Owner:** JARVIS · **Fecha:** 2026-07-22 · **Scope:** DISEÑO / TESTNET
**Orden de William:** "nuestro SDK debe ser difícil de replicar" + "cada DNI (identidad de
agentes) = una licencia SDK de SOUL... enganchamos gratis pero dependientes de nosotros" +
"Jarvis avanza y luego coordina con ada".

> **GATE DE GOBERNANZA (NEXUS, respetado):** el contrato SSAI M2 tiene scope
> **diseño/mock/testnet**. La emisión de DNIs de PRODUCCIÓN a externos requiere
> **aprobación explícita de William + auditoría de NEXUS**. Este documento diseña el flujo;
> NO habilita producción externa.

---

## 1. La tesis del foso (por qué es difícil de replicar)

Una API REST de memoria se copia en un fin de semana. Lo que **no** se replica: la **memoria
atada a una identidad soberana firmada**. La licencia no es un string copiable — es un **DNI
Ed25519 verificable** (`soul_dni`). Robar/revender una API key es trivial; falsificar una
identidad soberana firmada, no. **El lock-in = datos + identidad firmada.**

## 2. Infra que YA existe (verificada por efecto, 2026-07-22)

| Tabla | Filas | Rol en el modelo |
|---|---|---|
| `soul_v3.ssai_registry` | 9 | **El DNI**: `agent, soul_id, soul_dni, lifecycle_state, assurance` |
| `soul_v3.ssai_keys` | 0 | **Las llaves** Ed25519 por DNI: `registry_id, key_id, algorithm, public_key, state, revoked_at` — VACÍA, a poblar |
| `soul_v3.api_keys` | 41 | **La LICENCIA**: `tier, scope, rate_limit, mem_quota, agent_quota, org_id, active` — schema de licenciamiento ya construido |
| roles `mcp_runtime_*` (ADA) | 5 | **Enforcement DB** per-agente (aislamiento sellado en el P1 de ADA) |
| 361 políticas RLS | — | Aislamiento multi-inquilino |

**Hallazgo clave:** el schema de licencia (`api_keys` con tier/cuota/rate-limit/org) YA
existe. El modelo de William **no es green-field** — falta CABLEAR, no construir de cero.

## 3. El modelo: `soul_dni` = licencia SDK

```
Entidad (agente hoy / externo mañana)
  → tiene un soul_dni en ssai_registry (identidad soberana)
  → con un par de llaves Ed25519 en ssai_keys (firma verificable)
  → ligado a una fila en api_keys (tier + rate_limit + mem_quota + agent_quota)
  → cada llamada al SDK (:8768) autentica FIRMANDO con la llave del DNI
  → el broker resuelve DNI → api_key → tier/cuota, y la RLS aísla la memoria por tenant
```

**El "dulce a un bebé":** DNI gratis, onboarding trivial (`tier=free`). Con el tiempo su
contexto acumulado vive bajo SU DNI en SOUL → irse = perder memoria + identidad → dependientes.

## 4. Wiring pendiente (el gap real, no green-field)

1. **Vincular `api_keys` ↔ `ssai_registry.soul_dni`** — hoy `api_keys` no referencia el DNI.
   Propuesta: columna `api_keys.soul_dni` (FK a `ssai_registry`) → la licencia es DNI-bound.
2. **Poblar `ssai_keys`** (Ed25519 por DNI) — hoy 0 filas. Generar par de llaves al emitir DNI.
3. **Auth del SDK por firma DNI** — el SDK (:8768) hoy usa `api_key`/Bearer (auth por inquilino
   ya existe, verificado). Agregar: verificar firma Ed25519 del DNI → resolver la licencia.
4. **Exponer con TLS + rate-limit real** — hoy `:8768` bindea localhost; falta reverse-proxy.

## 5. Scope y gate

- **En scope AHORA (testnet/diseño):** este documento + un POC en testnet con DNIs de PRUEBA
  (nunca escribir en las tablas de identidad de producción sin coordinar con ADA).
- **GATE (fuera de scope sin OK):** emitir DNIs de producción a externos reales → **William OK
  + auditoría NEXUS** (identidad soberana real = territorio sensible, SSAI M2 contract).

## 6. Preguntas para coordinar con ADA (dueña del aislamiento de identidad)

1. ¿La FK `api_keys.soul_dni` colisiona con el aislamiento per-agente que sellaste?
2. ¿La emisión de DNI (escribir `ssai_registry`/`ssai_keys`) debe ir por `mcp_runtime` o por
   un rol emisor dedicado (least-privilege)?
3. ¿El broker que resuelve DNI→licencia entra en tu superficie protegida del MCP runtime?
4. Testnet: ¿un esquema/tenant `testnet` aislado para no mezclar DNIs de prueba con los 9 reales?

---
**Estado:** diseño avanzado por JARVIS (read-only sobre infra viva). Próximo paso: coordinar
estas 4 preguntas con ADA antes de cualquier POC que escriba datos.
