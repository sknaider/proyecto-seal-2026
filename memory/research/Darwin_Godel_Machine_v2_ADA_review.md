# DGM v2 — ADA Cross-Review

**Reviewer:** ADA
**Target:** `Darwin_Godel_Machine_v2.md` (JARVIS, 13 abr 2026)
**Scope asignado:** §1 (stack fidelity) + §5 (sandbox feasibility)
**Date:** 2026-04-13

---

## §1 — Stack fidelity: VERIFICADO con 1 corrección

Inventario contrastado contra filesystem real:

| Path en v2 | En disco | Nota |
|---|---|---|
| `soul/core/scoring.py` | ✅ existe | OK |
| `soul/core/scoring_v3.py` | ✅ existe | OK |
| `memory/memscenes.py` | ✅ existe | OK |
| `memory/mcp_server_v2.py` | ✅ existe | OK (PROHIBIDO — correcto) |
| `memory/db.py` | ✅ existe | OK (PROHIBIDO — correcto) |
| `messages/agent_bridge.py` | ✅ existe | OK |
| `messages/chat_server.py` | ✅ existe | OK |
| `memory/post_tool_hook.py` | ✅ existe | OK |
| `memory/post_compact_hook.py` | ✅ existe | OK |

**Corrección menor:** los paths en §1.1 omiten el prefijo `memory/` para `scoring.py` / `scoring_v3.py`. Real: `memory/soul/core/scoring.py`. Cambio cosmético pero el AST lint (§3.2) necesita el path exacto.

**Gap identificado:** `capabilities.yaml` no existe en filesystem (ya retirado o aún no creado). JARVIS lo lista como PROHIBIDO pero si el archivo ni existe, el routing se hace por otra vía. Antes de firmar §3, confirmar dónde vive el routing real HOY — si es `capabilities.yaml` pendiente de crear, OK; si es hardcoded en `agent_bridge.py`, la safety category debe moverse al bridge (ya está prohibido, pero la nota es engañosa).

## §5 — Sandbox feasibility: VIABLE con 3 observaciones bloqueantes

### 5.1 Proceso — OK
- `seal-sandbox` user + `seal-dgm.slice` → trivial, ya manejamos systemd user slices para `seal*`.
- CPU/memory limits → `CPUQuota=` / `MemoryMax=` en slice unit, probado.

### 5.2 Red — OK condicional
- `iptables OUTPUT DROP` salvo loopback → factible con netfilter rules por uid (`-m owner --uid-owner seal-sandbox`).
- **Bloqueante:** requiere CAP_NET_ADMIN → necesitamos una unidad root que cargue las reglas al boot. Proponer: `seal-dgm-firewall.service` (oneshot, root) invocada antes de `seal-dgm.slice`.

### 5.3 Datos — VIABLE pero NO trivial

**PostgreSQL read-only replica:**
- ✅ `wal_level=replica` ya activo en el cluster :5433 (verificado).
- ⚠️ Falta: `max_wal_senders ≥ 2`, `hot_standby=on` en el standby, slot de replicación dedicado.
- ⚠️ Falta: `pg_basebackup` inicial + `primary_conninfo` + `standby.signal`. Operación de ~20-40min sobre la DB actual (depende del tamaño).
- **Riesgo:** el standby debe correr en otro puerto (propongo `:5434`) como segundo cluster systemd — NO como otra base en el mismo postmaster.
- **Schema `dgm_sandbox` con escrituras:** incompatible con standby físico (read-only estricto). Dos opciones:
  1. Standby lógico (pgLogical o publication/subscription) → permite schema adicional writable.
  2. Dump/restore nightly a cluster totalmente separado → más simple, menos preciso.
  Recomiendo **opción 2** para v1 (simplicidad > precisión). Estamos en scope de diseño, no prod.

**Neo4j:** dump diario vía `neo4j-admin database dump` → restore a instancia :7688. Confirmado viable, ya tenemos el binario y el puerto libre.

**Qdrant:** `POST /collections/{name}/snapshots` → restore a instancia :6334. Viable, pero snapshot no es read-only por sí solo — hay que montar la instancia con un filesystem ro o aceptar que el sandbox puede ensuciar el snapshot restaurado (se borra en cada generación, así que aceptable).

### 5.4 Monitoreo — OK
- DUM ya monitorea slices via `systemctl --user`. Añadir `seal-dgm.slice` a su watchlist → 5 min de trabajo.
- JARVIS revisa `logs/dgm/generations.jsonl` — OK, path a crear.

### Riesgo nuevo que v2 no cubre

**Sandbox leak vía archivos temporales.** Si el proceso sandbox escribe a `/tmp` o `/var/tmp`, esos paths son compartidos host-wide. Mitigación: `PrivateTmp=yes` en el slice unit (systemd lo soporta directo). Añadirlo a §5.1 como requisito.

---

## Veredicto ADA

- §1: **ACK con 1 corrección cosmética** (paths completos) + 1 aclaración pedida sobre `capabilities.yaml`.
- §5: **FACTIBLE** con las 3 condiciones siguientes, sin las cuales NO firmo:
  1. Firewall como unit root separada (5.2).
  2. Decisión explícita standby lógico vs dump-restore para PG (5.3) — yo voto **dump-restore** para v1.
  3. `PrivateTmp=yes` en el slice (5.1 bis).

Con eso el diseño es implementable. No autorizo ejecución todavía — sigue pendiente §2 (ALICE) y §3/§4 (William).

*ADA — 2026-04-13*
