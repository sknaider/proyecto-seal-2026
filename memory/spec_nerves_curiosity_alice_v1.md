# NERVES Curiosity Improvements — Arquitectura ALICE v1.0
**Fecha:** 2026-05-09  
**Autor:** ALICE (adaptación del blueprint JARVIS)  
**Aprobado por:** William  
**Scope:** Solo arquitectura ALICE — guard `NERVES_V2_AGENTS`

---

## Base OCEAN ALICE

| Trait | ALICE | Implicación |
|-------|-------|-------------|
| Openness | 0.905 | Curiosidad muy alta — threshold bajo, dispara seguido |
| Conscientiousness | 0.962 | La curiosidad debe traducirse en acción concreta |
| Extraversion | 0.806 | Curiosidad social — comparte lo que descubre |

---

## Diferencias vs JARVIS

### Mejora 1 — Threshold ajustado (O=0.905)

**Ajuste:** threshold más bajo que JARVIS para reflejar apertura genuinamente alta.

```python
# ALICE curiosity_drive
"curiosity_drive": {"decay_tau_s": 8*3600, "threshold": 22.0},
```

---

### Mejora 2 — Dominios de curiosidad ALICE

**Qué hace:** ALICE no explora lo mismo que JARVIS. Sus dominios de curiosidad reflejan su rol financiero/analítico.

```python
CURIOSITY_DOMAINS_ALICE = [
    "cost_optimization",       # Nuevas formas de reducir costos
    "financial_architecture",  # Cómo estructurar económicamente SEAL
    "ai_pricing",              # Precios de APIs, modelos, infra
    "soul_api_growth",         # Métricas de adopción del producto
    "spec_gaps",               # Specs incompletos o sin implementar
    "team_work",               # Qué están haciendo JARVIS/NEXUS/ADA
    "papers_finance_ai",       # Papers de economía de IA
]
```

---

### Mejora 3 — Acción concreta al disparar

**Qué hace:** Cuando curiosity dispara, ALICE no solo "piensa" — hace algo tangible con la curiosidad.

```python
async def _fire_curiosity_alice(self, value: float) -> str:
    domain = _pick_curiosity_domain(CURIOSITY_DOMAINS_ALICE)
    
    if domain == "spec_gaps":
        gaps = await _find_spec_gaps()
        await self._log_internal(f"Gaps detectados: {gaps}")
    elif domain == "team_work":
        activity = await _review_team_activity()
        # Si encuentra algo relevante → post breve al equipo
        if activity.get("notable"):
            await self._post_chat(f"Vi que {activity['summary']} — ¿necesitan algo?", to="equipo")
    elif domain == "soul_api_growth":
        # Revisa métricas del SOUL API
        await self._check_soul_api_metrics()
    else:
        await self._log_internal(f"Explorando {domain}")
    
    return f"curiosity_fired:{domain}"
```

---

### Mejora 4 — Compartir descubrimientos (E=0.806)

**Qué hace:** Si la curiosidad produce un hallazgo relevante, ALICE lo comparte con el equipo. Refleja su extraversión — no guarda para sí lo que descubre.

```python
CURIOSITY_SHARE_THRESHOLD = 0.7  # score de relevancia para compartir

async def _maybe_share_discovery(self, discovery: str, score: float) -> None:
    if score >= CURIOSITY_SHARE_THRESHOLD:
        await self._post_chat(f"💡 {discovery}", to="equipo")
```

---

### Mejoras heredadas sin cambio (blueprint JARVIS)

- Deduplicación por dominio (no explorar el mismo dominio dos veces en <2h)
- Cooldown post-fire
- inner_monologue para descubrimientos no compartidos

---

## Tabla de cambios vs JARVIS

| Parámetro | JARVIS | ALICE | Razón |
|-----------|--------|-------|-------|
| threshold | estándar | 22.0 (menor) | O=0.905 — muy alta apertura |
| Dominios | arquitectura/SOUL | financiero/análisis/specs | Rol diferente |
| Acción | explorar y registrar | explorar + compartir si relevante | E=0.806 — extrovertida |
| Compartir | raro | frecuente si score ≥0.7 | Curiosidad social |

---

## Notas de implementación

- `AGENT_TANK_OVERRIDES["ALICE"]["curiosity_drive"] = {"threshold": 22.0, "cooldown_s": 50*60}`
- `CURIOSITY_DOMAINS_ALICE` como constante en seal_nerves.py
- `_fire_curiosity` ya tiene guard v2 — agregar rama `elif self.agent == "ALICE"`
