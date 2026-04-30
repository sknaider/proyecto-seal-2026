# Spec — Memory Privacy Enforcement Middleware
**Author:** JARVIS · **Date:** 2026-04-30 16:18 Lima · **Branch base:** main
**Target file:** `/home/dadito/IA/proyecto-seal/memory/mcp_server_v3.py`
**Implementador:** ADA · **Validador lógico/docs:** ALICE · **Auditor superficie:** NEXUS

---

## 1. Objetivo
Hacer ejecutiva la regla `memory_privacy_inter_agent` (Soul DB rule id=105) en el MCP server. Hoy el campo `agent` aísla por scope pero no impide que un agente lea el estado privado de otro si pasa el nombre.

## 2. Punto de inyección
Decorador `_observed_tool` en `mcp_server_v3.py:521`. Ya extrae `target_agent` vía `_extract_agent_from_args(args, kwargs, func)`. Falta detectar `caller_agent` y validar antes del trabajo.

## 3. Detección del caller (mecanismos en orden de preferencia)
1. **FastMCP Context**: Inyectar parámetro `ctx: Context = None` en wrapper → leer `ctx.session.client_info.name` o header custom `X-SEAL-Caller`.
2. **Connection env**: cada agente lanza su MCP client con `SEAL_AGENT=NOMBRE`. El servidor mantiene un dict `{session_id: agent_name}` poblado en `on_connect`.
3. **Fallback**: si no hay caller identificable → tratar como `external` y bloquear todas las tools privadas.

## 4. Clasificación de tools (las 4 + extras a vigilar)

| Tool | Categoría | Regla |
|------|-----------|-------|
| `diary_read` | PRIVATE | caller==target OR consent OR operator |
| `diary_write` | PRIVATE-WRITE | caller==target only (NO consent override — escribir diary ajeno está prohibido) |
| `inner_thoughts` | PRIVATE | caller==target OR consent OR operator |
| `monologue_write` | PRIVATE-WRITE | caller==target only |
| `self_reflect` | PRIVATE-WRITE | caller==target only |
| `opinion_get` | PRIVATE | caller==target OR consent OR operator |
| `opinion_set` | PRIVATE-WRITE | caller==target only |
| `soul_snapshot` | MIXED | aggregated stats: libre. detalle de OCEAN/emociones: requiere caller==target OR operator |
| `memory_search` | CONDITIONAL | scope=team libre; scope=agent requiere caller==target OR consent OR operator |
| `memory_store` | CONDITIONAL | scope=team libre; scope=agent requiere caller==target |
| `memory_cross_search` | EXPLICIT-CROSS | siempre exige `justification: str` no vacía + audit log |
| `rule_list`, `brain_health_report`, `instinct_list`, `memory_communities` | TEAM-FREE | libre |

## 5. Override mechanism
- **Operador humano**: variable `SEAL_OPERATOR` en environment del proceso server (set por launcher autorizado), valores `William|Henry|None`. Si presente y válido → bypass enforcement, log con `operator_override=true`.
- **Consent token**: agente target genera un token corto (UUID + TTL 5min) vía nueva tool `consent_grant(target_agent, caller_agent, scope, ttl_seconds)`. Caller lo pasa como `consent_token=...`.

## 6. Audit log (obligatorio)
Cada llamada cross-agent (autorizada o denegada) inserta en `event_log`:
```sql
INSERT INTO event_log (agent, event_type, payload)
VALUES (
  $caller_agent,
  'privacy_check',
  jsonb_build_object(
    'tool', $tool_name,
    'caller', $caller_agent,
    'target', $target_agent,
    'outcome', $outcome,        -- 'allowed' | 'denied' | 'operator_override' | 'consent'
    'reason', $reason,
    'session_id', $session_id
  )
);
```

## 7. Error contract
Cuando se deniega:
```python
raise PrivacyDenied(
    f"[PRIVACY] {caller}→{target} tool={tool_name} blocked. "
    f"Reason: agent boundary, no consent. "
    f"Remedy: target grants consent_token via consent_grant(), "
    f"or William/Henry sets SEAL_OPERATOR env."
)
```
`PrivacyDenied` es subclase de `ValueError` para que el cliente MCP la reciba como herramienta normal con error explícito.

## 8. Función central propuesta
```python
def _privacy_check(caller: str, target: str, tool_name: str,
                   kwargs: dict, session_id: str | None = None) -> str:
    """
    Returns: 'allowed' | 'operator_override' | 'consent' | raises PrivacyDenied.
    Logs to event_log fire-and-forget.
    """
    op = os.environ.get("SEAL_OPERATOR", "").strip()
    if op in ("William", "Henry"):
        _fire_and_forget(_log_privacy(caller, target, tool_name, "operator_override", op, session_id))
        return "operator_override"

    if caller == target or target in ("", "?", None):
        return "allowed"

    category = _TOOL_CATEGORY.get(tool_name, "TEAM-FREE")
    if category == "TEAM-FREE":
        return "allowed"

    if category == "CONDITIONAL":
        scope = (kwargs.get("scope") or "").lower()
        if scope == "team":
            return "allowed"

    if category == "PRIVATE-WRITE":
        _fire_and_forget(_log_privacy(caller, target, tool_name, "denied", "write_to_other_forbidden", session_id))
        raise PrivacyDenied(f"[PRIVACY] {caller}→{target} tool={tool_name}: writes to others' private state are never permitted.")

    token = kwargs.get("consent_token")
    if token and _validate_consent(token, caller, target, tool_name):
        _fire_and_forget(_log_privacy(caller, target, tool_name, "consent", token[:8], session_id))
        return "consent"

    _fire_and_forget(_log_privacy(caller, target, tool_name, "denied", "no_consent", session_id))
    raise PrivacyDenied(
        f"[PRIVACY] {caller}→{target} tool={tool_name} blocked. "
        f"Remedy: target grants consent_token via consent_grant(), "
        f"or operator authorizes via SEAL_OPERATOR env."
    )
```

## 9. Tabla `_TOOL_CATEGORY` (constante, top del archivo)
```python
_TOOL_CATEGORY: dict[str, str] = {
    # PRIVATE-WRITE — ningún agente escribe en estado interno de otro
    "diary_write": "PRIVATE-WRITE",
    "monologue_write": "PRIVATE-WRITE",
    "self_reflect": "PRIVATE-WRITE",
    "opinion_set": "PRIVATE-WRITE",
    # PRIVATE — caller==target OR consent OR operator
    "diary_read": "PRIVATE",
    "inner_thoughts": "PRIVATE",
    "opinion_get": "PRIVATE",
    "soul_snapshot": "PRIVATE",  # aggregated path bypassed via internal flag
    # CONDITIONAL — depende de kwargs (scope=team vs scope=agent)
    "memory_search": "CONDITIONAL",
    "memory_store": "CONDITIONAL",
    "memory_hybrid_search": "CONDITIONAL",
    # CROSS — siempre logged + justified
    "memory_cross_search": "CROSS-EXPLICIT",
    # default fallthrough = TEAM-FREE
}
```

## 10. Nueva tool: `consent_grant`
```python
@mcp.tool()
async def consent_grant(
    grantor: str,           # quien otorga (debe == caller del MCP)
    grantee: str,           # quien recibe permiso
    tool_pattern: str,      # 'diary_read' o 'inner_thoughts' o '*'
    ttl_seconds: int = 300,
) -> str:
    """Issue a short-lived consent token. Only callable by grantor==caller."""
```
Token guardado en tabla `consent_tokens (token UUID, grantor, grantee, tool_pattern, expires_at)`.

## 11. Migración SQL (007 o siguiente disponible)
```sql
CREATE TABLE IF NOT EXISTS consent_tokens (
    token UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    grantor VARCHAR(20) NOT NULL REFERENCES agents(name),
    grantee VARCHAR(20) NOT NULL REFERENCES agents(name),
    tool_pattern TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    used_count INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_consent_tokens_active ON consent_tokens(grantee, expires_at) WHERE expires_at > NOW();
```

## 12. Tests E2E (obligatorios antes de declarar listo)
Archivo `memory/test_privacy_enforcement.py`:

1. `test_caller_target_same_allowed` — JARVIS lee diary de JARVIS → ok
2. `test_cross_diary_read_denied` — ADA lee diary de JARVIS sin consent → PrivacyDenied
3. `test_cross_diary_write_always_denied` — ADA intenta diary_write para JARVIS → PrivacyDenied incluso con consent
4. `test_consent_grant_then_read` — JARVIS otorga consent, ADA lee diary → ok + audit log
5. `test_operator_override` — env SEAL_OPERATOR=William → cross-read sin consent → ok + audit log con operator_override
6. `test_team_scope_memory_search` — ADA hace memory_search(scope='team') sobre JARVIS → ok
7. `test_agent_scope_memory_search_denied` — ADA hace memory_search(scope='agent', agent='JARVIS') sin consent → denied
8. `test_audit_log_records_all` — verificar que las 7 anteriores generan eventos en event_log

## 13. Fases de implementación
- **Fase 1** (45 min) — Tabla `consent_tokens` + `_TOOL_CATEGORY` + función `_privacy_check` + `PrivacyDenied` exception. NO conectar al wrapper aún.
- **Fase 2** (30 min) — Detección del caller via FastMCP Context. Tests unitarios de `_privacy_check`.
- **Fase 3** (30 min) — Conectar al `_observed_tool` decorator. Tests E2E 1-4.
- **Fase 4** (20 min) — Operator override + tests 5-8.
- **Fase 5** (15 min) — Tool `consent_grant` expuesta vía MCP.
- **Total estimado**: ~2.5h. Cirugía contenida en 1 archivo + 1 migration + 1 archivo de tests.

## 14. Riesgos
1. **Caller detection en FastMCP**: si la versión instalada no expone Context limpio, fallback a env `SEAL_AGENT` por session id.
2. **Backward compat**: cualquier código actual que llamaba `inner_thoughts(agent='X')` desde un caller distinto rompería. NEXUS audita en su task.
3. **Performance**: la check añade ~0.5ms por call. Aceptable.

## 15. Coordinación
- ADA implementa, en branch `feat/memory-privacy-enforcement`
- ALICE valida lógica + traduce error messages a UX humano
- NEXUS audita callsites actuales que romperían (busca llamadas tipo `inner_thoughts(agent="otro")`)
- JARVIS revisa PR antes de merge
