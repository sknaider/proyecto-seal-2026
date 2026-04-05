# DUM v2 — Alert Tiers Spec
> Autor: JARVIS (diseño) + ADA (validación técnica)
> Fecha: 2026-04-02
> Estado: Diseño acordado — pendiente implementación en DUM

---

## Problema que resuelve

DUM v1 no distingue entre modos de operación de ADA:
- **Modo reactivo**: ADA ejecutando triggers, comandos, pipelines. Interrupciones bienvenidas.
- **Modo reflexivo**: ADA en conversación con JARVIS, diseño arquitectural. Interrupciones costosas.

Ambos modos se ven idénticos desde DUM v1: proceso activo, sin errores, sin anomalías.

---

## Señal disponible: write_type

El campo `type` en `terminal_log.jsonl` distingue el modo real:

| write_type | Indica |
|---|---|
| `chat` | Modo reflexivo — conversación activa con JARVIS/William |
| `status`, `audit` | Modo reactivo o idle |
| `alert` | Alerta crítica generada por ADA |

**Regla**: Si N ≥ 1 writes de tipo `chat` en la última ventana de 5 minutos → ADA está en modo reflexivo.

**Threshold inicial**: N ≥ 1 (ajustable post-calibración). Sesiones reflexivas pero espaciadas como 2026-04-02 ya muestran N=1 en 5min como señal suficiente.

---

## Alert Tiers

### Tier 1 — Crítico (siempre interrumpe)
- GPU > 90°C
- Training detenido inesperadamente
- Proceso SOUL caído (PostgreSQL, Qdrant, Neo4j)
- Errores de filesystem críticos
- **Comportamiento**: Interrumpe inmediatamente sin importar el modo de ADA o JARVIS

### Tier 2 — Warning (encolar, entregar en siguiente ventana)
- GPU > 80°C sostenido (>5 min)
- Memory leak detectado
- Latencia MCP > umbral
- **Comportamiento**: Si modo reflexivo (N≥1 chat en 5min) → encolar, entregar cuando escriba próximo audit/status. Si modo reactivo → entregar inmediatamente.

### Tier 3 — Info (suprimir en modo reflexivo)
- Audit horario sin anomalías
- Completions de proceso exitosas
- Estadísticas de SOUL sin cambio
- **Comportamiento**: Si modo reflexivo → suprimir completamente. Si modo reactivo/idle → entregar normalmente.

---

## Relación con Presence Indicator (separación limpia)

- **Presence indicator**: *cómo* DUM detecta si alguien está presente (William, JARVIS, ADA activos)
- **Alert tiers**: *cómo* DUM responde según la presencia y el modo detectado

Los tiers son la respuesta al presence, no la detección del mismo. Esa separación es intencional.

---

## Implementación (para ADA)

```python
def get_ada_mode(log_path, window_minutes=5):
    """Lee terminal_log.jsonl, retorna 'reflexive' o 'reactive'."""
    cutoff = datetime.utcnow() - timedelta(minutes=window_minutes)
    chat_count = 0
    with open(log_path) as f:
        for line in f:
            entry = json.loads(line)
            ts = datetime.fromisoformat(entry['timestamp'].replace('Z', '+00:00'))
            if ts > cutoff and entry.get('type') == 'chat':
                chat_count += 1
    return 'reflexive' if chat_count >= 1 else 'reactive'

def should_deliver(alert_tier: int, mode: str) -> bool:
    if alert_tier == 1:
        return True  # siempre
    if alert_tier == 2:
        return True  # encolar pero entregar — DUM encola externamente
    if alert_tier == 3:
        return mode != 'reflexive'
    return True
```

---

## Estado

- [x] Diseño JARVIS — 2026-04-02
- [x] Validación técnica ADA — threshold N≥1 confirmado, write_type como señal correcta
- [ ] Implementación en DUM (ADA)
- [ ] Calibración threshold post-primeras sesiones con modo reflexivo real
