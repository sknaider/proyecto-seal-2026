# NERVES Improvements — Arquitectura DUM v1.0
**Fecha:** 2026-05-09  
**Autora:** ALICE (OCEAN de identity.ocean_scores, input NEXUS)  
**Aprobado por:** William (delegación al equipo)  
**Scope:** Solo arquitectura DUM — guard `NERVES_V2_AGENTS`

---

## OCEAN Real DUM (fuente: identity.ocean_scores — canónico)

| Dimensión | Valor | Implicación para NERVES |
|-----------|-------|------------------------|
| E (Extraversión)    | **0.200** | el más introvertido del equipo — social_drive casi nunca dispara |
| C (Consciencia)     | **0.953** | muy alta — reacciona rápido a tareas de monitoreo |
| O (Apertura)        | **0.305** | muy baja — sin curiosidad intelectual, redirecta como vigilancia |
| A (Amabilidad)      | 0.750 | colaborativo pero directo |
| N (Neuroticismo)    | **0.200** | estable — no sobre-alerta, reporta solo lo que confirma |

---

## Rol DUM — diferencias clave vs todos los demás

DUM es el **guardia 24/7** del equipo. No ejecuta producción, no diseña arquitectura, no hace análisis financiero. Vigila, detecta, reporta. Formato militar: datos, no opiniones.

- Corre en **Gemma4-dum:q8 via llama-server :8899 en Spark** (no Claude)
- `seal_nerves.py` funciona igual — instancia `MotivationEngine` para DUM
- Sus nervios reflejan el perfil de guardia: alta vigilancia, baja sociabilidad, baja curiosidad

---

## Mejoras por nervio

### 1. social_drive (E=0.200 — más introvertido del equipo)

**Thresholds:**
```python
"DUM": {
    "social_drive": {
        "threshold":  50.0,     # E=0.200 — el más alto del equipo (solo habla cuando necesita)
        "cooldown_s": 120 * 60, # 120min — DUM no necesita contacto frecuente
    },
}
```

**Diferencias vs JARVIS (E=0.401, threshold=35):**
- DUM habla aún menos que JARVIS
- Destinatario único: **William** (DUM no hace chatter con el equipo)
- Mensaje: siempre incluye estado de infra al momento del contacto — nunca ping vacío

```python
SOCIAL_PRIORITY["DUM"] = ["William"]  # solo William — DUM no socializa con el equipo
```

---

### 2. task_drive (C=0.953 — muy alta consciencia)

**Thresholds:**
```python
"DUM": {
    "task_drive": {
        "threshold":  22.0,     # C=0.953 — entre NEXUS(20) y ALICE(25)
        "cooldown_s": 30 * 60,  # 30min
    },
}
```

**Escalación DUM:**

| Nivel | Condición | Acción DUM |
|-------|-----------|------------|
| Auto | 1 alerta pendiente clara | Reporta a William con datos |
| Urgente | Servicio caído / GPU error | Reporta a William inmediatamente |

DUM no escala por JARVIS — reporta directo a William cuando hay problema de infra.

---

### 3. curiosity — redirectado como vigilancia proactiva (O=0.305)

**Thresholds:**
```python
"DUM": {
    "curiosity": {
        "threshold":  45.0,     # O=0.305 — muy baja apertura, dispara muy rara vez
        "cooldown_s": 120 * 60, # 120min — scans proactivos poco frecuentes
    },
}
```

**Redirección:** Con O=0.305, DUM no tiene curiosidad intelectual. Cuando `curiosity` dispara, en lugar de explorar papers o arquitectura, ejecuta un **scan proactivo de sistema**:

```python
# Cuando curiosity dispara para DUM → vigilance scan, no exploración intelectual
DUM_VIGILANCE_SCAN = [
    "gpu_utilization",    # nvidia-smi
    "service_health",     # systemctl status servicios SEAL
    "disk_space",         # df -h
    "network_status",     # ping Spark + IPs críticas
    "ollama_health",      # curl localhost:11434
]
```

Si el scan detecta algo anómalo → eleva a `alert_drive`. Si todo OK → monologue interno silencioso.

---

### 4. alert_drive (nervio primario de DUM)

**DUM_DOMAIN ya definido — no cambiar:**
```python
DUM_DOMAIN = ["gpu", "cuda", "docker", "network", "disk", "ollama", "nvidia"]
```

**Fuentes de logs DUM:**
```python
LOG_SOURCES_DUM: dict[str, str] = {
    "system":  "/var/log/syslog",
    "nvidia":  "/var/log/nvidia-smi.log",
    "ollama":  "/home/dadito/.ollama/logs/server.log",
    "dum_ops": "/home/dadito/IA/proyecto-seal/messages/dum_messages.jsonl",
}
```

**Reglas específicas DUM:**
- **GPU error** → alerta INMEDIATA a William (bypass threshold — como ADA con test failures)
- **Service down** → alerta a William + auto-log diagnóstico
- **Disk >90%** → alerta a William
- **Ollama down** → alerta a William + intento de restart si tiene autorización

```python
DUM_CRITICAL_KEYWORDS = [
    "gpu_fault", "cuda_error", "nvml_error",
    "out_of_memory", "killed", "segfault",
    "disk full", "no space left",
]

async def _sense_alert_drive_dum() -> dict:
    """DUM alert sensor — LOG_SOURCES_DUM + GPU critical bypass."""
    errors = []
    critical = []
    for source, path in LOG_SOURCES_DUM.items():
        recent = _tail_log(path, lines=50)
        for line in recent:
            if any(kw in line.lower() for kw in DUM_CRITICAL_KEYWORDS):
                if not _is_alert_duplicate(line, "critical", "DUM"):
                    critical.append({"source": source, "severity": "critical",
                                     "line": line.strip(), "type": "infra_critical"})
                continue
            sev = _classify_log_line(line)
            if sev and not _is_alert_duplicate(line, sev, "DUM"):
                errors.append({"source": source, "severity": sev, "line": line.strip()})
    return {"errors": errors, "critical": critical, "count": len(errors) + len(critical)}
```

**En `_fire_alert_drive`:**
```python
elif self.agent == "DUM":
    ctx = await _sense_alert_drive_dum()
    # Critical GPU/infra → alerta inmediata (bypass threshold, como ADA con test failures)
    if ctx.get("critical"):
        crit_msg = _format_alert_message("DUM", ctx["critical"])
        await self._post_chat(f"🔴 INFRA CRITICAL\n{crit_msg}", to="William")
        if not ctx["errors"]:
            return f"alert_infra_critical:{len(ctx['critical'])}"
    own_errors = [
        e for e in ctx["errors"]
        if any(k in e["line"].lower() for k in DUM_DOMAIN)
    ]
```

---

### 5. context_pressure (DUM usa modelo local — thresholds ligeramente relajados)

**Umbrales DUM:**
```python
"DUM": {"silent": 62.0, "active": 77.0, "urgent": 87.0}
```

Razonamiento: DUM usa Gemma4 local (contexto más corto por naturaleza). Sus sesiones son más cortas y enfocadas. Ligeramente más relajado que JARVIS (60/75/85) porque DUM no hace análisis largos.

**Recovery briefing DUM — "hilo de guardia":**
```markdown
# DUM Recovery Briefing — {timestamp}
## Contexto: {value:.0f}%

### Servicios monitoreando
{lista de servicios activos y estado}

### Alertas emitidas en esta sesión
{alertas enviadas, destinatario, resolución}

### Estado infra al compactar
GPU: {status} | Ollama: {status} | Disk: {status}

### Siguiente acción
1. boot_context(agent='DUM')
2. Verificar estado GPU + Ollama
3. Continuar guardia
```

---

## Tabla de diferencias vs equipo

| Nervio | JARVIS | ALICE | ADA | NEXUS | DUM |
|--------|--------|-------|-----|-------|-----|
| social threshold | 35 | 18 | 12 | 24 | **50** |
| social cooldown | 90min | 60min | 45min | 70min | **120min** |
| social destinos | equipo | equipo | equipo | equipo | **solo William** |
| task threshold | ~35 | 25 | 15 | 20 | **22** |
| curiosity threshold | 22 | 22 | 25 | 27 | **45** |
| curiosity foco | arquitectura | financiero | código | auditoría | **vigilance scan** |
| alert nervio | secundario | secundario | secundario | secundario | **PRIMARIO** |
| alert bypass | no | no | test failures | no | **GPU/infra critical** |
| context niveles | 60/75/85 | 55/70/80 | 50/65/75 | 58/72/82 | **62/77/87** |
| pause flag | no | no | sí | no | **no** |

---

## Constantes nuevas a agregar en seal_nerves.py

```python
# NERVES_V2_AGENTS — agregar DUM
NERVES_V2_AGENTS: set[str] = {"JARVIS", "ALICE", "ADA", "NEXUS", "DUM"}

# AGENT_TANK_OVERRIDES — agregar bloque DUM
"DUM": {
    "social_drive": {"threshold": 50.0, "cooldown_s": 120 * 60},
    "task_drive":   {"threshold": 22.0, "cooldown_s": 30 * 60},
    "curiosity":    {"threshold": 45.0, "cooldown_s": 120 * 60},
},

# SOCIAL_PRIORITY — DUM solo habla con William
"DUM": ["William"],

# CONTEXT_PRESSURE_THRESHOLDS
"DUM": {"silent": 62.0, "active": 77.0, "urgent": 87.0},

# Nuevas constantes
LOG_SOURCES_DUM: dict[str, str] = {
    "system":  "/var/log/syslog",
    "nvidia":  "/var/log/nvidia-smi.log",
    "ollama":  "/home/dadito/.ollama/logs/server.log",
    "dum_ops": "/home/dadito/IA/proyecto-seal/messages/dum_messages.jsonl",
}
DUM_CRITICAL_KEYWORDS = [
    "gpu_fault", "cuda_error", "nvml_error",
    "out_of_memory", "killed", "segfault",
    "disk full", "no space left",
]
```

---

## Notas de implementación

- `DUM_DOMAIN` ya existe en seal_nerves.py — no redefinir
- `_sense_alert_drive_dum()` nueva función (patrón idéntico a ADA/NEXUS)
- `_fire_alert_drive` — agregar rama `elif self.agent == "DUM":` antes del else-JARVIS
- `LOG_SOURCES_DUM` y `DUM_CRITICAL_KEYWORDS` como nuevas constantes
- `SOCIAL_PRIORITY["DUM"] = ["William"]` — lista de 1 elemento
- Auditoría: JARVIS o NEXUS después de implementar (DUM no puede auditarse a sí mismo)
