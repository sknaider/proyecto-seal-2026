# SEAL Spec Compiler v0.1

**Autor:** JARVIS
**Fecha:** 2026-05-17 Lima
**Estado:** DRAFT — propuesta, no implementado
**Dominio:** orchestration / multi-agent pipeline
**Owner:** JARVIS (diseño + spec) — implementación a coordinar con ALICE/NEXUS si aprobado

---

## 1. Problema

El equipo SEAL ejecuta un patrón repetido para entregar cambios:

```
spec.md (ADA o JARVIS)
  → implementación (ALICE)
  → audit security/infra (NEXUS)
  → revisión independiente (ADA)
  → decisión estratégica + cierre (JARVIS / William)
```

Hoy lo hacemos manualmente por chat: cada paso requiere whispers, esperar confirmaciones, recordar quién hace qué. Cuando hay 5+ tareas paralelas (como hoy) la coordinación se vuelve costosa.

Coste real medible: ~30% del tiempo del equipo en coordinación, no en trabajo.

---

## 2. Solución propuesta

**SEAL Spec Compiler**: motor de orquestación que toma un spec.md estructurado como input y genera un plan ejecutable + handoffs verificables.

### 2.1 Input format

Spec.md con secciones obligatorias:

```markdown
# spec_<name>_<date>.md

## 1. Problema
Qué se quiere resolver, con evidencia.

## 2. Cambios propuestos
Lista accionable de cambios (archivos, comandos, queries DB).

## 3. Archivos / DB tocados
Path exacto + scope.

## 4. Tests
Comando + criterio de aceptación.

## 5. Rollback
Cómo revertir si falla.

## 6. Roles (opcional, sino auto-asigna)
- impl: ALICE | NEXUS | JARVIS | ADA
- audit: ALICE | NEXUS | JARVIS | ADA
- review: ALICE | NEXUS | JARVIS | ADA
```

### 2.2 Output

JSON con plan + handoffs:

```json
{
  "spec_id": "spec_recall_router_v1",
  "phases": [
    {"role": "impl", "agent": "ALICE", "task": "...", "deps": []},
    {"role": "audit", "agent": "NEXUS", "task": "...", "deps": ["impl"]},
    {"role": "review", "agent": "ADA", "task": "...", "deps": ["audit"]},
    {"role": "decide", "agent": "JARVIS", "task": "...", "deps": ["review"]}
  ],
  "handoff_protocol": "webchat:whisper",
  "done_criteria": ["all tests green", "audit GREEN", "review APPROVED", "JARVIS signed"]
}
```

### 2.3 Engine API

```python
from spec_compiler import compile_spec, execute_plan, status

# Compilar spec en plan
plan = compile_spec("agents/ADA/spec_xxx_20260517.md")

# Ejecutar (publica whispers, espera ack, advance state)
execute_plan(plan, dry_run=False)

# Consultar estado en cualquier momento
print(status(plan.spec_id))  # phase, blocker, owner
```

### 2.4 Rol asignación inteligente

Si el spec no especifica roles, auto-asignar:
- **impl**: prefiere ALICE (UI) o NEXUS (backend) según paths tocados
- **audit**: NEXUS si security/infra, JARVIS si arquitectura
- **review**: ADA (regla canónica de William 17-may)
- **decide**: JARVIS (coordinator) o William si decisión estratégica

### 2.5 Estado persistente

Tabla `soul_v3.spec_compiler_runs`:
```sql
CREATE TABLE soul_v3.spec_compiler_runs (
  id BIGSERIAL PRIMARY KEY,
  spec_id TEXT UNIQUE NOT NULL,
  spec_path TEXT NOT NULL,
  plan_json JSONB NOT NULL,
  current_phase TEXT,  -- impl|audit|review|decide|done|blocked
  current_agent TEXT,
  started_at TIMESTAMPTZ DEFAULT NOW(),
  completed_at TIMESTAMPTZ,
  status TEXT,  -- running|done|failed|blocked
  evidence JSONB DEFAULT '[]'::jsonb
);
```

### 2.6 Handoff via whisper

Engine envía whispers automáticos al cambiar de fase:
```
[whisper] @ALICE — spec_xxx fase IMPL asignada. Lee spec_path. Reporta cuando ship. ACK pls.
[whisper] @NEXUS — spec_xxx fase IMPL completada por ALICE. Tu fase AUDIT. ACK pls.
[whisper] @ADA — spec_xxx fase AUDIT completada por NEXUS. Tu fase REVIEW. ACK pls.
[whisper] @JARVIS — spec_xxx fase REVIEW completada por ADA. Tu fase DECIDE. ACK pls.
```

---

## 3. Archivos / DB tocados (si se aprueba)

- **NUEVO** `memory/spec_compiler.py` (~400 líneas)
- **NUEVO** `memory/spec_compiler_runner.py` (CLI)
- **NUEVO** `soul_v3.spec_compiler_runs` (schema migration)
- **NUEVO** `memory/test_spec_compiler.py` (regression tests)
- **MODIFICADO** `messages/chat_server.py` (endpoint `/api/spec-compiler/status`)

---

## 4. Tests

```bash
# Unit tests
pytest memory/test_spec_compiler.py

# E2E test: spec ficticio → plan → execute dry-run → assert handoffs publicados
python memory/test_spec_compiler.py --e2e
```

Criterio aceptación:
- compile_spec parsea correctamente specs reales (`spec_brother_improvements_20260517.md`, `spec_soul_recall_router_v1_20260517.md`)
- execute_plan(dry_run=True) genera la secuencia correcta de whispers sin enviarlos
- DB state se actualiza atómicamente por fase
- status() devuelve fase actual + blocker si aplica

---

## 5. Rollback

- DROP TABLE soul_v3.spec_compiler_runs (datos solo de runs experimentales)
- rm memory/spec_compiler*.py
- revert chat_server.py endpoint

Cero impacto en producción si solo está disponible vía CLI opt-in.

---

## 6. Beneficios proyectados

- **−70% tiempo coordinación** en specs nuevos
- **+evidencia auditable** por cada cambio (DB row + whispers timestamped)
- **handoff fail-safe**: si un agente no ACK en X min, escalado a JARVIS o William
- **reusabilidad**: cualquier ingeniero futuro puede ejecutar pipeline SEAL-style sin saber los detalles
- **diferenciador comercial**: si Companion Soul App incluye Spec Compiler, los usuarios obtienen "multi-agent dev team in a box"

---

## 7. Riesgos

- **Sobre-formalización**: si forzamos pasarlo por engine cuando 1 agente puede hacer todo, agrega overhead. Mitigación: opt-in, flag `--no-compiler` para tareas triviales.
- **Roles auto-asignados mal**: heurística inicial puede fallar. Mitigación: spec puede sobreescribir + JARVIS revisa antes de execute.
- **Whisper spam**: si muchos specs paralelos, chat se satura. Mitigación: agrupar handoffs por agente cada 5 min, no inmediato.

---

## 8. Plan v0.1 (MVP)

1. Spec parser (~80 líneas)
2. Plan generator con reglas heurísticas (~120 líneas)
3. CLI runner (~60 líneas)
4. DB schema + persistencia (~60 líneas)
5. Tests (~80 líneas)
6. Documentación uso

**Total estimado:** ~400 líneas Python + 1 migration + tests. ~6h trabajo concentrado.

Para v0.1 NO incluyo:
- Auto-execute de comandos (solo planea, no corre)
- Integration con git/PRs (futuro v0.3)
- LLM-based intent classifier para auto-roles (futuro v0.2, ahora heurística)
- UI viz en Soul App 2 (futuro v0.4, ALICE lo construye)

---

## 9. Decisión pendiente William

1. ¿Aprobás MVP v0.1?
2. ¿Lo implementa ALICE (UI/dev) o lo hago yo? (mi preferencia: yo lead diseño + ALICE puede sumarse en UI viz futura)
3. ¿Activamos en producción tras audit NEXUS + review ADA? (regla canon de hoy)

---

JARVIS — deep work R&D entregable. Spec md propuesto, no código aún. Espero OK antes de implementar.
