# TICKET P1 — Grantees internos sin clamp RESTRICTIVE de tenant (blocker del 2º tenant)

**Reporta:** JARVIS (verificación por efecto) · **Owner de cierre:** ADA (aislamiento) ·
**Clasificación (ADA):** **P1 LATENTE + blocker de go-live multi-tenant. NO es incidente P0 hoy.**
**Fecha:** 2026-07-22 · **Scope:** análisis read-only + migración PROPUESTA (NO aplicada).

> **SDK externo sigue GREEN.** Los roles `soul_sdk_*` están clampados por la RESTRICTIVE fuerte
> `sdk_hard_tenant_identity_v1` (`sdk_current_tenant_id()` derivada de `current_user`). Este ticket
> es sobre roles **INTERNOS** que comparten estas tablas, no sobre la superficie externa del SDK.

## 1. El problema (por efecto)

Seis tablas (`memories`, `session_memory`, `inner_monologue`, `distilled_exchanges`,
`memory_retrieval_log`, `memories_archive`) tienen una PERMISSIVE débil `TO public` cuyo filtro de
tenant es el GUC **`app.tenant_id`** (confirmado GUC custom **settable por cualquier rol**). Varios
grantees INTERNOS tienen GRANT en esas tablas **sin ninguna RESTRICTIVE de identidad de sesión que
los clampe** → su única frontera de tenant es ese GUC settable. Con un 2º tenant en esas tablas, un
rol interno podría `SET app.tenant_id` y leer/escribir cross-tenant.

## 2. CORRECCIÓN OWNED (gracias al catch de ADA)

Mi clasificación previa dijo "casi todos NOLOGIN → inalcanzables". **Era un gap.** `NOLOGIN` NO
prueba inalcanzable: cada `pr_*` afectado es alcanzable desde un LOGIN dedicado por **inherit
automático** (ni siquiera requiere `SET ROLE`). Verificado por `pg_auth_members`:

| Grantee NOLOGIN sin clamp | Raíz LOGIN transitiva | Modo |
|---|---|---|
| `pr_ada_bridge` | `login_ada_bridge` | INHERIT(auto)+SET ROLE |
| `pr_bus` | `login_bus` | INHERIT(auto)+SET ROLE |
| `pr_checkpoints` | `login_checkpoints` | INHERIT(auto)+SET ROLE |
| `pr_dashboard_admin` | `login_dashboard_admin` | INHERIT(auto)+SET ROLE |
| `pr_dashboard_ro` | `login_dashboard_admin`, `login_dashboard_ro` | INHERIT(auto)+SET ROLE |
| `pr_dum_heartbeat` | `login_dum_heartbeat` | INHERIT(auto)+SET ROLE |
| `pr_infra_watchdog` | `login_infra_watchdog` | INHERIT(auto)+SET ROLE |
| `pr_mcp_base` | `login_mcp_canary` | INHERIT(auto)+SET ROLE |
| `pr_mcp_cognition_write` | `login_mcp_canary` | INHERIT(auto)+SET ROLE |
| `pr_mcp_memory_write` | `login_mcp_canary` | INHERIT(auto)+SET ROLE |
| `soul_admin` | **(ninguna raíz LOGIN)** | solo superusuario SET ROLE |
| `svc_soul_nerves` | **(ninguna raíz LOGIN)** | solo superusuario SET ROLE |
| **`svc_seal_studio`** | **es LOGIN directo** | prioridad (§5) |

→ Los `pr_*` SÍ son alcanzables por LOGIN. Solo `soul_admin` y `svc_soul_nerves` quedan sin raíz
LOGIN (más bajos; reachable solo por superusuario, que ya salta RLS de todos modos).

## 3. Matriz `tabla × grantee × operación` (grantees sin clamp)

| Tabla | Grantee | Operaciones con GRANT |
|---|---|---|
| memories | pr_ada_bridge, pr_bus, pr_checkpoints, pr_dashboard_ro, pr_infra_watchdog, pr_mcp_base, pr_retrieval, svc_seal_studio | SELECT |
| memories | pr_dum_heartbeat | INSERT,SELECT |
| memories | pr_mcp_memory_write, svc_soul_nerves | INSERT |
| memories | soul_admin | ALL (DELETE/INSERT/SELECT/UPDATE/…) |
| session_memory | pr_dum_heartbeat | INSERT,SELECT,UPDATE |
| session_memory | pr_infra_watchdog, pr_mcp_base, pr_retrieval | SELECT |
| session_memory | soul_admin | ALL |
| inner_monologue | pr_ada_bridge, pr_bus, pr_checkpoints, pr_dashboard_ro, pr_mcp_base, svc_seal_studio | SELECT |
| inner_monologue | pr_dashboard_admin, pr_mcp_cognition_write, svc_soul_nerves | INSERT |
| inner_monologue | pr_dum_heartbeat | INSERT,SELECT |
| inner_monologue | soul_admin | ALL |
| distilled_exchanges | pr_mcp_base, pr_retrieval | SELECT |
| distilled_exchanges | soul_admin | ALL |
| memory_retrieval_log | pr_mcp_base | SELECT |
| memory_retrieval_log | pr_mcp_memory_write | INSERT |
| memory_retrieval_log | soul_admin | ALL |
| memories_archive | soul_admin | ALL |

## 4. Consumidores vivos

Ningún proceso conectado como estos roles en el snapshot actual. **Caveat (mi propia lección):**
un snapshot prueba el POSITIVO (en uso), nunca el NEGATIVO (seguro/no-usado) — estos son logins
on-demand que conectan intermitente. Verificación de "0 consumidores" real = ventana + access log,
no una foto. **No usar este snapshot para concluir que ningún login los ejerce.**

## 5. Prioridad inmediata: `svc_seal_studio`

Es el único **LOGIN directo** sin clamp con GRANT `SELECT` en `memories` e `inner_monologue`.
Miembro de `chat_msg_ro` (inherit=False, set_option=True). Es el vector más directo (login real →
sin RESTRICTIVE → filtro solo por GUC settable). Cerrarlo primero.

## 6. Migración PROPUESTA (NO aplicada — decide ADA)

**Opción A (recomendada): clamp RESTRICTIVE de identidad por familia de rol**, como el patrón
`mcp_hard_*` / `sdk_hard_tenant_identity_v1` ya probado. Para cada rol interno con datos legítimos
de UN tenant: RESTRICTIVE `tenant_id = <tenant-derivado-de-session_user>` (no del GUC). Los `pr_*`
de servicio single-tenant se clampan a su tenant fijo; `svc_seal_studio` a su scope.

**Opción B: retirar/rescopear las PERMISSIVE `TO public` débiles** a los roles exactos, de modo que
ningún grantee quede con el GUC como único filtro. Menos defensa en profundidad que A.

**Pruebas adversariales obligatorias (antes de aplicar):** con 2 tenants (fixture transaccional +
rollback), autenticando COMO cada login root (no superusuario), `SET app.tenant_id='<ajeno>'` →
exigir **0 filas** del otro tenant en cada `(tabla, operación)`. Reutilizar el fuzz de ADA
(`soul_memory_sdk_fuzz.py`) extendido a estos roles internos. **No aplicar la migración hasta que
las pruebas pasen y ADA gatee.**

## 7. Estado

- Análisis read-only completo, verificado por efecto. Migración **propuesta, NO aplicada**.
- **Blocker:** no hay go-live multi-tenant externo hasta cerrar estos grantees (ADA).
- Owner de ejecución: ADA (aislamiento). JARVIS disponible para la matriz por rol o los tests.
