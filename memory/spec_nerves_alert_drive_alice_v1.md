# NERVES Alert Drive Improvements — Arquitectura ALICE v1.0
**Fecha:** 2026-05-09  
**Autor:** ALICE (adaptación del blueprint JARVIS + dominios propios)  
**Aprobado por:** William  
**Scope:** Solo arquitectura ALICE — guard `NERVES_V2_AGENTS`

---

## Diferencias clave vs JARVIS

JARVIS monitorea errores de SOUL/arquitectura.  
ALICE monitorea errores de su dominio: **producción, costos, specs, SOUL API**.

---

## Mejoras ALICE

### Mejora A — Dominios ALICE (reemplaza dominios JARVIS)

```python
ALICE_ALERT_DOMAIN = [
    "soul", "mcp", "memory",          # infraestructura compartida
    "production", "deploy", "spec",   # su responsabilidad de ejecución
    "cost", "billing", "api",         # su rol financiero
    "alice",                          # errores propios
]

# DUM sigue siendo responsable de infra:
DUM_DOMAIN = ["gpu", "cuda", "docker", "network", "disk", "ollama", "nvidia"]
```

---

### Mejora B — Fuentes de logs ALICE

```python
LOG_SOURCES_ALICE = {
    "mcp":       "/home/dadito/IA/proyecto-seal/memory/logs/mcp_sse_daemon.log",
    "soul_api":  "/home/dadito/IA/proyecto-seal/memory/logs/soul_api.log",
    "nerves":    "/home/dadito/IA/proyecto-seal/research/flywire_results/nerves.log",
    "alice_ops": "/home/dadito/IA/proyecto-seal/messages/alice_messages.jsonl",
}
```

---

### Mejora C — Escalación ALICE (heredada con ajuste de destinatario)

| Nivel | Condición | Acción ALICE |
|-------|-----------|--------------|
| Silencioso | Solo WARNINGs | Registra en inner_monologue |
| Normal | 1-2 ERRORs | Post al equipo con diagnóstico |
| Urgente | CRITICAL o 3+ ERRORs | Avisa a William directamente |

**Diferencia vs JARVIS:** ALICE no suprime alertas a William aunque esté activo — igual que JARVIS.

---

### Mejora D — Deduplicación (heredada de JARVIS)

Igual que JARVIS:
```python
_alert_dedup: dict[str, datetime] = {}
ALERT_DEDUP_COOLDOWN = {"warning": 120, "error": 60, "critical": 15}
```

**El archivo de dedup es por agente:** `/tmp/ALICE_alert_seen.json` (ya implementado en el helper `_is_alert_duplicate`)

---

### Mejora E — Alerta de anomalía de costos (ALICE-específica)

**Qué hace:** ALICE tiene un sensor adicional — detecta anomalías de costos en los logs (llamadas API inesperadamente costosas, uso GPU fuera de lo normal).

```python
COST_ANOMALY_KEYWORDS = [
    "billing", "cost_spike", "rate_limit", "quota_exceeded",
    "unexpected_charge", "token_overflow",
]

async def _sense_alert_drive_alice(self) -> dict:
    errors = await _sense_alert_drive("ALICE")  # sensor base
    
    # Extra: anomalías de costo
    for source, path in LOG_SOURCES_ALICE.items():
        recent = _tail_log(path, lines=50)
        for line in recent:
            if any(kw in line.lower() for kw in COST_ANOMALY_KEYWORDS):
                errors["errors"].append({
                    "source": source,
                    "severity": "error",
                    "line": line.strip(),
                    "type": "cost_anomaly"
                })
    
    return errors
```

---

## Tabla de cambios vs JARVIS

| Parámetro | JARVIS | ALICE | Razón |
|-----------|--------|-------|-------|
| ALICE_DOMAIN | soul/mcp/architecture | +production/cost/billing/spec | Rol ejecutora + analista |
| Log sources | nerves+mcp | mcp+soul_api+nerves+alice_ops | Cobertura de su dominio |
| Sensor extra | ninguno | cost_anomaly detector | Su especialidad |
| Dedup | /tmp/JARVIS_alert_seen | /tmp/ALICE_alert_seen | Ya soportado por helper |

---

## Notas de implementación

- `_fire_alert_drive` ya tiene guard `self.agent in NERVES_V2_AGENTS`
- Agregar `elif self.agent == "ALICE"` con `LOG_SOURCES_ALICE` y `ALICE_ALERT_DOMAIN`
- `_sense_alert_drive_alice` llama al base `_sense_alert_drive` y agrega capa de costos
- Cooldown alert_drive ALICE: igual que JARVIS (críticos siempre pasan)
