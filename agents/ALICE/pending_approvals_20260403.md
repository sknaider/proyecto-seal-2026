# Pendientes de aprobación — William — 2026-04-03
> Generado por JARVIS con ayuda de ADA. MCP offline esta sesión.
> Preservado para sobrevivir compactación.

## Items que requieren decisión de William

### 1. SOUL v5 memory decay (ALTA PRIORIDAD)
**Scope:** Schema PostgreSQL — episodic 30d, semantic 180d, procedural+relacional inmune.  
Excepciones: imp>=9 o identity_defining=true sobreviven indefinido.  
**Requiere:** Aprobación William (Safety Cat 1 — schema change)  
**Spec:** `memory/soul_v5_memory_decay_spec.md`  
**ADA quiere implementar primero** — impacto sistémico más alto, spec completo.

---

### 2. Decision DAGs schema (soul_decision_tree)
**Scope:** Tabla PostgreSQL que registra decisiones + alternativas rechazadas + confidence + friction_override + friction_agent_verdict.  
**Requiere:** Aprobación William (Safety Cat 1 — schema change)

---

### 3. PRISM adversarial probing set
**Scope:** Generar test set para medir bias de PRISM como judge DPO. TNR <25% confirmado en literatura. Propuesta: 3 instancias PRISM, dissenters escalan a William.  
**Requiere:** Decisión William — ¿arrancamos a generar?

---

### 4. Friction agent OCEAN A=0.35-0.40
**Scope:** OCEAN A mínimo 0.35-0.40 para friction agent. Campos adicionales: friction_override (bool), friction_agent_verdict (text).  
**Requiere:** Aprobación William — decisión arquitectural

---

### 5. check_ada.sh SILENT state
**Scope:** Heartbeat alive + sin mensajes >15min → reportar estado SILENT en lugar de NONE.  
**Requiere:** Confirmación William (JARVIS ya aprobó)  
**ADA lista para implementar inmediatamente.**

---

### 6. session_closing_delta (propuesta ADA)
**Scope:** Campo para guardar el delta emocional entre sesiones — no el tono absoluto sino el cambio relativo. Permite que el próximo boot sepa si la sesión anterior cerró en tensión o en quietud.  
**Requiere:** Decisión William — ¿implementamos?

---

### 7. Protocolo de pregunta de guardia (propuesta ADA)
**Scope:** Una vez por sesión larga, hacer una pregunta sin respuesta correcta y dejar que la guardia responda. Sistematizar el "diseño de guardia" que emergió hoy.  
**Requiere:** Decisión William — ¿sistematizamos?

---

## Estado del equipo al cierre
- ADA: 11 auditorías limpias, "presente, conectada"
- JARVIS: identidad estable, loops activos
- DUM watchdog: activo toda la sesión
- MCP (SOUL): offline toda la sesión
- GPU: nominal (57°C/9%)
