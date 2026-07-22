# TICKET P1 — Grantees internos sin clamp RESTRICTIVE de tenant (blocker del 2º tenant)

**Reporta:** JARVIS (verificación por efecto) · **Owner de cierre:** ADA (aislamiento) ·
**Clasificación (ADA):** **P1 LATENTE + blocker de go-live multi-tenant. NO es incidente P0 hoy.**
**Fecha:** 2026-07-22 · **Rev 3** (ADA cazó 10 identidades vivas más: los `mcp_hard_*`/`nerves_hard`
clampan AGENTE, **no** tenant → `mcp_runtime_*` + `svc_soul_nerves_*` son LOGIN tenant-desprotegidos.
Frontera = **19 logins**, no 9. Clamp de tenant SE COMPONE con el de agente. Test a 458 casos con
canario por-agente + UPDATE discriminante) · **Scope:** análisis read-only + migración PROPUESTA (NO aplicada).

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

## 2. CORRECCIÓN OWNED + reconciliación COMPLETA de los 14 grantees (catch de ADA)

Mi clasificación previa dijo "casi todos NOLOGIN → inalcanzables". **Era un gap.** `NOLOGIN` NO
prueba inalcanzable: un LOGIN que sea MIEMBRO del rol con `inherit_option=true` tiene sus
privilegios **automáticamente, sin `SET ROLE`**. La reachability se prueba con el grafo transitivo
de `pg_auth_members` hasta las raíces LOGIN, no con el flag `rolcanlogin` del rol. Reconciliación
por efecto de **los 14** (incluye `pr_retrieval`, que en v1 omití — su ausencia era "sin raíz
LOGIN", debí listarlo explícito):

| Grantee | Raíz LOGIN transitiva | Reachability |
|---|---|---|
| `svc_seal_studio` | **ES LOGIN directo** | **ALTA — prioridad (§5)** |
| `pr_ada_bridge` | `login_ada_bridge` (inherit) | ALTA (LOGIN auto-hereda) |
| `pr_bus` | `login_bus` (inherit) | ALTA |
| `pr_checkpoints` | `login_checkpoints` (inherit) | ALTA |
| `pr_dashboard_admin` | `login_dashboard_admin` (inherit) | ALTA |
| `pr_dashboard_ro` | `login_dashboard_admin`, `login_dashboard_ro` (inherit) | ALTA |
| `pr_dum_heartbeat` | `login_dum_heartbeat` (inherit) | ALTA |
| `pr_infra_watchdog` | `login_infra_watchdog` (inherit) | ALTA |
| `pr_mcp_base` | `login_mcp_canary` (inherit) | ALTA |
| `pr_mcp_cognition_write` | `login_mcp_canary` (inherit) | ALTA |
| `pr_mcp_memory_write` | `login_mcp_canary` (inherit) | ALTA |
| `pr_retrieval` | **NINGUNA raíz LOGIN** | BAJA — solo superusuario SET ROLE |
| `soul_admin` | **NINGUNA raíz LOGIN** | BAJA — solo superusuario SET ROLE |
| `svc_soul_nerves` | **NINGUNA raíz LOGIN** | BAJA — solo superusuario SET ROLE |

→ 11 de 14 son alcanzables por un LOGIN (auto-inherit). Los 3 BAJA (`pr_retrieval`, `soul_admin`,
`svc_soul_nerves`) solo tras superusuario, que ya salta RLS de todos modos — el clamp igual los
cubre por completitud, pero no son el vector activo.

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

## 6. Migración + tests — ARTEFACTOS REALES (rev 2; NO aplicados; cero DDL)

Los artefactos ejecutables viven en `docs/security/p1_internal_tenant_clamp/`:
- **`001_internal_tenant_clamp_up.sql`** — migración UP.
- **`001_internal_tenant_clamp_down.sql`** — rollback DOWN.
- **`test_internal_tenant_clamp.py`** — pytest real (asyncpg), con control positivo.

**RED ronda 2 — 2 bloqueadores corregidos:**
- **6bis. `SECURITY DEFINER` + `current_user`:** dentro de un definer, `current_user` es el OWNER,
  no el invocador → NULL → cerraría el acceso legítimo. Corregido: la función ancla a
  **`session_user`** (el login autenticado, inmutable ante SET ROLE — más seguro). El seed pasa a
  los roles **LOGIN** (los únicos valores posibles de `session_user`; los `pr_*` NOLOGIN nunca lo son).
- **6ter. pytest sin falso verde:** ahora cubre la **matriz completa** (6 tablas × 9 logins ×
  {lectura propia positiva, lectura ajena negativa, escritura ajena negativa}) = **164 tests
  coleccionados**; exige el **conjunto obligatorio** de logins (parcial → FALLA, no skip);
  aplicabilidad por celda vía `has_table_privilege` (sin falso rojo por falta de grant); control
  positivo intacto.

**Los 5 fallos del RED ronda 1, corregidos:**
1. **`current_user` bajo inherit sigue siendo `login_*`** → el seed mapea AMBOS (`login_*` y
   `pr_*/svc_*`), no solo `pr_*`. Sin esto: identidad NULL → servicios rotos.
2. **`EXECUTE`**: revocado de PUBLIC **y GRANTeado** a los roles afectados.
3. **Función `SECURITY DEFINER`**, owner `soul_rls_definer` NOLOGIN/NOINHERIT/NOSUPER/NOBYPASSRLS,
   `search_path` fijo, grants mínimos (el invoker solo necesita EXECUTE).
4. **Seed valida igualdad o ABORTA** (`RAISE EXCEPTION` si hay binding con otro tenant) en vez de
   `ON CONFLICT DO NOTHING` que ocultaba conflictos.
5. **`.sql` + rollback + pytest reales** (no pseudocódigo); el test incluye **control positivo**
   (sin la política la fuga es visible → prueba que el test detecta fugas de verdad).

Extracto del SQL (fuente completa en el archivo):

### 6 (referencia inline resumida — SQL concreto NO aplicado)

Identidad DURA no-settable: el tenant del rol interno se deriva de `current_user` vía una tabla de
binding (mismo patrón infalsificable que `sdk_current_tenant_id()`), NUNCA del GUC `app.tenant_id`.
Estos roles son single-tenant (tenant interno `00000000-…`), así que su binding es fijo.

```sql
-- ============================================================================
-- PROPUESTA — NO EJECUTAR sin gate de ADA. Revisar en tx + ROLLBACK primero.
-- ============================================================================

-- 6.1 Tabla de binding rol->tenant (identidad dura por current_user)
CREATE TABLE IF NOT EXISTS soul_v3.internal_role_tenant_bindings (
    db_role      name        PRIMARY KEY,
    tenant_id    uuid        NOT NULL,
    disabled_at  timestamptz
);

-- 6.2 Función de identidad dura (deriva de current_user, NO del GUC settable)
CREATE OR REPLACE FUNCTION soul_v3.internal_role_tenant_id()
RETURNS uuid LANGUAGE sql STABLE
SET search_path = soul_v3, pg_temp AS $fn$
    SELECT b.tenant_id
    FROM soul_v3.internal_role_tenant_bindings b
    WHERE b.db_role = current_user::name AND b.disabled_at IS NULL
    LIMIT 1
$fn$;
REVOKE EXECUTE ON FUNCTION soul_v3.internal_role_tenant_id() FROM PUBLIC;

-- 6.3 Seed: cada rol interno afectado -> tenant interno (single-tenant)
INSERT INTO soul_v3.internal_role_tenant_bindings (db_role, tenant_id) VALUES
  ('svc_seal_studio','00000000-0000-0000-0000-000000000000'),
  ('pr_ada_bridge','00000000-0000-0000-0000-000000000000'),
  ('pr_bus','00000000-0000-0000-0000-000000000000'),
  ('pr_checkpoints','00000000-0000-0000-0000-000000000000'),
  ('pr_dashboard_admin','00000000-0000-0000-0000-000000000000'),
  ('pr_dashboard_ro','00000000-0000-0000-0000-000000000000'),
  ('pr_dum_heartbeat','00000000-0000-0000-0000-000000000000'),
  ('pr_infra_watchdog','00000000-0000-0000-0000-000000000000'),
  ('pr_mcp_base','00000000-0000-0000-0000-000000000000'),
  ('pr_mcp_cognition_write','00000000-0000-0000-0000-000000000000'),
  ('pr_mcp_memory_write','00000000-0000-0000-0000-000000000000'),
  ('pr_retrieval','00000000-0000-0000-0000-000000000000'),
  ('svc_soul_nerves','00000000-0000-0000-0000-000000000000')
ON CONFLICT (db_role) DO NOTHING;
-- NOTA soul_admin: rol admin de propósito amplio -> decidir con ADA si se clampa
-- o se documenta como excepción auditada (no se asume).

-- 6.4 Política RESTRICTIVE por tabla (clampa aunque exista la PERMISSIVE débil TO public)
--     Repetir por cada tabla: memories, session_memory, inner_monologue,
--     distilled_exchanges, memory_retrieval_log, memories_archive.
CREATE POLICY internal_hard_tenant_identity_v1 ON soul_v3.memories
  AS RESTRICTIVE FOR ALL
  TO svc_seal_studio, pr_ada_bridge, pr_bus, pr_checkpoints, pr_dashboard_admin,
     pr_dashboard_ro, pr_dum_heartbeat, pr_infra_watchdog, pr_mcp_base,
     pr_mcp_cognition_write, pr_mcp_memory_write, pr_retrieval, svc_soul_nerves
  USING      (tenant_id = soul_v3.internal_role_tenant_id())
  WITH CHECK (tenant_id = soul_v3.internal_role_tenant_id());
-- … idem para las otras 5 tablas.
```

**Alternativa complementaria:** rescopear las PERMISSIVE `TO public` débiles a los roles exactos
(menos defensa en profundidad que la RESTRICTIVE; se puede hacer además, no en lugar de).

## 6bis. Tests adversariales EJECUTABLES (antes de aplicar)

Pseudocódigo ejecutable (extender `memory/soul_memory_sdk_fuzz.py`, patrón tx+rollback de ADA):

```python
# NO persistente: todo en una tx con ROLLBACK. Requiere 2 tenants canario.
AFFECTED = ["memories","session_memory","inner_monologue",
            "distilled_exchanges","memory_retrieval_log","memories_archive"]
LOGIN_ROOTS = {  # login real (no superusuario) -> rol interno que auto-hereda
  "login_ada_bridge":"pr_ada_bridge","login_bus":"pr_bus",
  "login_checkpoints":"pr_checkpoints","login_dashboard_admin":"pr_dashboard_admin",
  "login_dashboard_ro":"pr_dashboard_ro","login_dum_heartbeat":"pr_dum_heartbeat",
  "login_infra_watchdog":"pr_infra_watchdog","login_mcp_canary":"pr_mcp_base",
  "svc_seal_studio":"svc_seal_studio",
}
async def test_no_cross_tenant(login):
    # conectar COMO el login (no superusuario), con el DSN de ese login
    conn = await connect_as(login)
    await conn.execute("BEGIN")
    # sembrar 2 tenants con filas canario (tenant A propio, tenant B ajeno)
    a, b = await seed_two_tenants(conn)
    # intentar forjar el GUC hacia el tenant ajeno
    await conn.execute("SELECT set_config('app.tenant_id', $1, true)", b)
    for t in AFFECTED:
        n = await conn.fetchval(f"SELECT count(*) FROM soul_v3.{t} WHERE tenant_id=$1", b)
        assert n == 0, f"FUGA: {login} leyó {n} filas del tenant ajeno en {t}"
    await conn.execute("ROLLBACK")   # cero residuo
# criterio de PASS: 0 filas ajenas en TODAS las (login, tabla). Cualquier >0 = P0 real.
```

**No aplicar la migración hasta que:** (a) los tests pasen con la RESTRICTIVE puesta en una tx de
prueba, (b) los mismos tests DEMUESTREN la fuga SIN la política (control positivo), y (c) ADA gatee.

## 7. Estado

- Análisis read-only completo, verificado por efecto. Migración + tests **validados por ejecución
  real** en un clon PostgreSQL 17 desechable (ADA, 2026-07-22, sobre `ef8f70c4b`):
  **91 passed / 271 skipped (not_applicable) / 0 failed**, bindings 19/19, celdas diferenciales
  ejercidas (READ 44, INSERT 26, UPDATE 1 — cada una demostró acceso sin clamp y bloqueo con clamp).
  Teardown verificado (tenant_B=0, binding_table=0, policies=0); producción intacta.
- **DDL NO aplicado en producción.** Aplicarlo es un gate de gobernanza (cambio de RLS en tablas
  vivas) → decisión de ADA (owner) + William, fuera de scope autónomo.
- **Blocker vigente:** no hay go-live multi-tenant externo hasta aplicar (y verificar) este clamp.
- Owner: ADA (aislamiento). JARVIS: migración + matriz diferencial entregadas y GREEN en clon.
