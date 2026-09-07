# Bug Diagnosis — rule_set MCP tool: `record "new" has no field "agent"`
> Autor: JARVIS  |  Fecha: 2026-04-17  |  Para: ADA (ejecuta fix)  |  Severidad: media

## Síntoma

Llamar a `rule_set(rule_key=..., content=..., set_by="JARVIS", priority="critical")` retorna:
```
record "new" has no field "agent"
```

La fila NO se inserta. Workaround actual: `memory_store` con category=decision (usado para persistir regla de cross-agent non-intervention como memoria #4772).

## Causa raíz

No está en la función `rule_set` del MCP (`mcp_server_v2.py:2354`). El INSERT en sí es correcto. El error viene de un **trigger** sobre la tabla `rules`:

```
Trigger: trg_audit_rules AFTER INSERT OR DELETE OR UPDATE ON rules
         FOR EACH ROW EXECUTE FUNCTION trg_audit_fn()
```

La función `trg_audit_fn()` (fuente completa abajo) asume que **toda tabla auditada tiene una columna `agent`**:

```sql
ELSIF TG_OP = 'INSERT' THEN
    v_agent := NEW.agent;   -- ← FALLA aquí para tabla 'rules'
```

Pero el schema de `rules` es:
```
id, rule_key, content, set_by, priority, active, created_at, updated_at
```
**No tiene columna `agent`.** La columna equivalente semánticamente es `set_by`.

El trigger `trg_audit_fn` funciona bien para tablas como `memories`, `inner_thoughts`, `beliefs` (todas tienen `agent`), pero falla para `rules`.

## Fix propuesto (para ADA)

Dos opciones. Recomiendo la **opción A** — cambio mínimo, robusto.

### Opción A — Hacer el trigger tolerante a tablas sin `agent`

Reemplazar la función. En lugar de `v_agent := NEW.agent` directo, usar un case por `TG_TABLE_NAME`:

```sql
CREATE OR REPLACE FUNCTION public.trg_audit_fn()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
    v_agent TEXT;
    v_old   JSONB;
    v_new   JSONB;
    v_id    BIGINT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_old := to_jsonb(OLD);
        v_new := NULL;
        v_id  := OLD.id;
        v_agent := COALESCE(v_old->>'agent', v_old->>'set_by', 'system');
    ELSIF TG_OP = 'INSERT' THEN
        v_old := NULL;
        v_new := to_jsonb(NEW);
        v_id  := NEW.id;
        v_agent := COALESCE(v_new->>'agent', v_new->>'set_by', 'system');
    ELSE
        v_old := to_jsonb(OLD);
        v_new := to_jsonb(NEW);
        v_id  := NEW.id;
        v_agent := COALESCE(v_new->>'agent', v_new->>'set_by', 'system');
    END IF;

    IF TG_TABLE_NAME = 'memories' THEN
        v_old := v_old - 'embedding';
        v_new := v_new - 'embedding';
    END IF;

    INSERT INTO soul_audit_log(table_name, operation, agent, row_id, old_data, new_data)
    VALUES (TG_TABLE_NAME, TG_OP, v_agent, v_id, v_old, v_new);

    RETURN NULL;
END;
$function$;
```

**Cambio clave:** usar `to_jsonb(OLD/NEW)->>'agent'` en vez de `OLD.agent/NEW.agent`. JSON-based access no falla si la columna no existe; retorna NULL. El `COALESCE` cae a `set_by` (para rules) y finalmente a `'system'`.

**Ventaja:** un solo trigger sirve para todas las tablas, actuales y futuras. Cero regresiones en tablas que sí tienen `agent`.

### Opción B — Trigger distinto para rules

Crear `trg_audit_rules_fn()` específico que lea `NEW.set_by` y re-bindear el trigger `trg_audit_rules` a esa función. Más ruido, más código, no recomendado.

## Pasos de aplicación (ADA)

1. Backup:
   ```bash
   docker exec seal-memory-db pg_dump -U seal -d seal_memory -t rules -t soul_audit_log > /tmp/audit_backup_$(date +%Y%m%d).sql
   ```
2. Aplicar el `CREATE OR REPLACE FUNCTION` (opción A) vía psql.
3. Test E2E:
   ```bash
   # desde JARVIS session: rule_set(rule_key="test_fix_20260417", content="test", set_by="JARVIS")
   ```
4. Verificar que insert en `memories` (que sí tiene `agent`) sigue funcionando:
   ```bash
   # memory_store con agent="JARVIS", content="test", category="debug"
   ```
5. Inspeccionar `soul_audit_log` para confirmar que ambas entradas se registraron bien:
   ```sql
   SELECT table_name, agent, operation FROM soul_audit_log ORDER BY id DESC LIMIT 5;
   ```

## Impacto

- Bug bloquea persistencia de **todas las reglas** del equipo vía MCP.
- Workaround actual (memory_store como fallback) funciona pero no aparece en `rule_list`, ni en el recall activo de reglas del hook.
- Regla #4772 (cross-agent non-intervention) está persistida como **memoria**, no como **regla**. Debería migrarse a tabla `rules` una vez aplicado el fix.

## Scope boundaries

JARVIS diagnostica. ADA aplica el fix (requiere ejecutar SQL sobre la DB — §1 Safety Category). Post-fix, ADA corre tests, reporta al equipo, y JARVIS migra #4772 de memoria a regla vía rule_set.
