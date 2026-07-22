# NERVES Alert Drive Improvements — Arquitectura JARVIS v1.0
**Fecha:** 2026-05-09  
**Autor:** JARVIS (análisis NEXUS + ALICE + JARVIS)  
**Aprobado por:** William  
**Scope:** Solo arquitectura JARVIS — guard `NERVES_V2_AGENTS` para activación selectiva

---

## Problema actual

El nervio `alert_drive` de JARVIS tiene 5 limitaciones críticas:

1. **Sin sensor real** — no lee ningún log. Solo posta un mensaje genérico y retorna `"alert_scan_triggered"` sin hacer nada
2. **Sin escalación** — cualquier valor dispara idéntico mensaje a William, sin importar si es un warning menor o un error crítico
3. **Sin deduplicación** — si el mismo error persiste, rebota y alerta repetidamente en cada tick
4. **Sin acción real** — avisa que "revisará logs" pero nunca los revisa ni propone diagnóstico
5. **Sin filtro de dominio** — JARVIS no discrimina entre errores de su dominio (SOUL/arquitectura) y errores de infraestructura (GPU, red, servicios) que corresponden a DUM

---

## Mejoras acordadas

### Mejora A — Sensor real de logs (JARVIS)

**Qué hace:** Lee logs reales antes de disparar. Identifica errores concretos con severidad.

**Fuentes de logs a revisar:**
```python
LOG_SOURCES = {
    "nerves":    "/home/dadito/IA/proyecto-seal/memory/logs/nerves.log",
    "mcp":       "/home/dadito/IA/proyecto-seal/memory/logs/mcp_server.log",
    "soul_api":  "/home/dadito/IA/proyecto-seal/memory/logs/soul_api.log",
    "seal_main": "/home/dadito/IA/proyecto-seal/messages/jarvis_messages.jsonl",
}
```

**Implementación:**
```python
async def _sense_alert_drive(agent: str) -> dict:
    errors = []
    for source, path in LOG_SOURCES.items():
        recent = _tail_log(path, lines=50, max_age_minutes=30)
        for line in recent:
            sev = _classify_log_line(line)  # "warning" | "error" | "critical"
            if sev:
                errors.append({"source": source, "severity": sev, "line": line.strip()})
    return {"errors": errors, "count": len(errors)}
```

---

### Mejora B — Escalación por severidad (NEXUS)

**Qué hace:** El comportamiento al dispararse varía según la severidad real del error.

| Nivel | Condición | Acción JARVIS |
|-------|-----------|---------------|
| Silencioso | Solo WARNINGs | JARVIS registra en `inner_monologue`, no interrumpe |
| Normal | 1-2 ERRORs | Posta al equipo en webchat con detalle del error |
| Urgente | CRITICAL o 3+ ERRORs | Avisa a William directamente, aunque esté activo |

```python
async def _fire_alert_drive(self, value: float) -> str:
    ctx = await _sense_alert_drive(self.agent)
    severity = _max_severity(ctx["errors"])

    if severity == "warning" or ctx["count"] == 0:
        await self._log_internal(ctx)           # silencioso
    elif severity == "error":
        await self._post_chat(_format_errors(ctx), to="equipo")
    elif severity == "critical":
        await self._post_chat(_format_errors(ctx), to="William")  # no suprimir

    return f"alert_scan_done:{severity}:{ctx['count']}_errors"
```

---

### Mejora C — Deduplicación de alertas (ALICE)

**Qué hace:** Evita re-alertar sobre el mismo error en la misma ventana de tiempo.

**Lógica:**
```python
_alert_dedup: dict[str, datetime] = {}  # error_hash → last_alerted

def _is_duplicate(error_line: str, cooldown_minutes: int = 60) -> bool:
    key = hashlib.md5(error_line[:80].encode()).hexdigest()[:8]
    last = _alert_dedup.get(key)
    if last and (datetime.now() - last).seconds < cooldown_minutes * 60:
        return True
    _alert_dedup[key] = datetime.now()
    return False
```

**Cooldown por severidad:**
- WARNING → 120min (no urge)
- ERROR → 60min
- CRITICAL → 15min (puede repetir, es urgente)

---

### Mejora D — Acción real al disparar (JARVIS)

**Qué hace:** En lugar de avisar que "revisará logs", JARVIS hace el diagnóstico básico en el momento del disparo.

**Flujo:**
1. Lee logs reales (Mejora A)
2. Identifica el error específico (not "hay un error" sino "MCP server: ConnectionRefused en :8766")
3. Clasifica si JARVIS puede auto-resolver (ej: reiniciar servicio caído) o necesita escalar
4. Incluye diagnóstico en el mensaje: qué falló, desde cuándo, posible causa

```python
def _format_alert_message(errors: list[dict]) -> str:
    lines = ["⚠️ JARVIS detectó:"]
    for e in errors[:3]:  # máx 3 para no spamear
        lines.append(f"  [{e['severity'].upper()}] {e['source']}: {e['line'][:100]}")
    if len(errors) > 3:
        lines.append(f"  ... y {len(errors)-3} más en logs")
    return "\n".join(lines)
```

---

### Mejora E — Filtro de dominio JARVIS (JARVIS)

**Qué hace:** JARVIS solo alerta sobre errores en su dominio. Errores de infra → delega a DUM.

**Dominios:**
```python
JARVIS_DOMAIN = ["soul", "mcp", "memory", "boot_context", "connectome", "nerves"]
DUM_DOMAIN    = ["gpu", "cuda", "docker", "network", "disk", "ollama"]

def _classify_domain(error_line: str) -> str:
    line_lower = error_line.lower()
    if any(k in line_lower for k in DUM_DOMAIN):
        return "dum"
    if any(k in line_lower for k in JARVIS_DOMAIN):
        return "jarvis"
    return "jarvis"  # default: JARVIS revisa si no está claro

# Al disparar:
if domain == "dum":
    await self._post_chat(f"DUM — error de infra detectado: {error}", to="DUM")
    return "alert_delegated_dum"
```

---

## Tabla de cambios

| Mejora | Área | Antes | Después |
|--------|------|-------|---------|
| A | Sensor | Ninguno — texto genérico | Lee logs reales, extrae errores con severidad |
| B | Escalación | Igual para todo | WARNING→silencioso / ERROR→equipo / CRITICAL→William |
| C | Deduplicación | Sin cooldown — rebota infinito | Hash por error, cooldown 15-120min según severidad |
| D | Acción | "Revisando logs..." (sin revisar) | Diagnóstico concreto: qué falló, dónde, posible causa |
| E | Filtro | Mezcla SOUL + infra + todo | JARVIS=SOUL/arquitectura, DUM=infra/GPU/red |

---

## Notas de implementación

- ALICE implementa en `seal_nerves.py` función `_fire_alert_drive` y nueva `_sense_alert_drive`
- Guard: solo aplica si `self.agent == "JARVIS"` (o `agent in NERVES_V2_AGENTS`)
- No suprimir alert_drive cuando William está activo — errores críticos deben llegar siempre
- Los WARNINGs se guardan en `inner_monologue` para contexto futuro, no en webchat
