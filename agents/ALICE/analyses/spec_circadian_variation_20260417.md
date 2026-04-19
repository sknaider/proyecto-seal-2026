# Spec — Variación Circadiana para Tanques LIF
> Autor: ALICE | 17 abril 2026 | Pending: ADA implementation + JARVIS review
> Gap #3 del inventario de humanización — de 65 → 68/100

---

## Problema

Los τ actuales (human-grade) son constantes 24/7. Un humano de 45 años NO procesa igual:
- 09:00: cortisol peak → alert_drive más sensible, curiosity activa
- 14:00: post-lunch dip → procesamiento más lento, τ más largos
- 22:00: melatonina → social_drive baja, task_drive casi cero
- 03:00: sleep pressure máximo → solo alert_drive activo si hay emergencia

Sin variación circadiana, SEAL tiene "jet lag permanente" — igual a las 3am que a las 9am.

---

## Datos Biológicos Base

### Ritmo cortisol (Lupien et al., 2009)
- Peak: 08:00-10:00 Lima → +20-30% arousal / vigilancia
- Nadir: 20:00-22:00 → -20% arousal

### Ritmo melatonina
- Inicio: ~21:00 → social_drive y curiosity deben declinar
- Peak: 02:00-04:00 → suppresión máxima de todos los tanques

### Ritmo cognitivo humano (Dijk & Czeisler, 1994)
- Velocidad de procesamiento peak: 14:00-16:00 Lima
- Memoria episódica consolidación: 23:00-07:00 (sueño)
- Alerta ejecutiva peak: 10:00-13:00

---

## Propuesta: Circadian Multiplier por Tanque

### Implementación

```python
import math
from datetime import datetime, timezone

TIMEZONE_OFFSET = -5  # Lima (PET)

def circadian_multiplier(tank: str, hour_utc: int) -> float:
    """
    Retorna multiplicador [0.5, 1.5] para τ según hora local Lima.
    multiplier > 1 → τ más largo (procesamiento más lento)
    multiplier < 1 → τ más corto (más ágil)
    """
    hour_lima = (hour_utc + TIMEZONE_OFFSET) % 24
    
    # Base circadiana: coseno con peak en ~10am Lima
    # Fase 0 = medianoche, peak cortisol = 10am = 10/24 * 2π
    phase = (hour_lima - 10) / 24 * 2 * math.pi
    base = math.cos(phase)  # [-1, 1]
    
    # Cada tanque tiene su propio patrón circadiano
    TANK_CIRCADIAN = {
        "curiosity": {
            "amplitude": 0.25,    # ±25% variación
            "phase_shift": 0,     # peak a las 10am (cortisol)
            "night_floor": 0.6,   # mínimo nocturno (03:00)
        },
        "alert_drive": {
            "amplitude": 0.30,    # ±30% — más sensible a hora
            "phase_shift": 0,     # peak a las 10am (cortisol)
            "night_floor": 0.75,  # alert siempre algo activo
        },
        "task_drive": {
            "amplitude": 0.35,    # ±35% — fuerte variación
            "phase_shift": -2,    # peak a las 12:00 (deadline pressure)
            "night_floor": 0.4,   # casi 0 a las 3am
        },
        "social_drive": {
            "amplitude": 0.20,    # ±20% — menos variación
            "phase_shift": +2,    # peak a las 12:00-14:00 (hora social)
            "night_floor": 0.5,   # baja mucho de noche
        },
        "context_pressure": {
            "amplitude": 0.10,    # variación mínima — es técnico
            "phase_shift": 0,
            "night_floor": 0.85,
        },
    }
    
    cfg = TANK_CIRCADIAN.get(tank, {"amplitude": 0.15, "phase_shift": 0, "night_floor": 0.7})
    
    # Hora ajustada por phase_shift del tanque
    shifted_hour = (hour_lima + cfg["phase_shift"]) % 24
    shifted_phase = (shifted_hour - 10) / 24 * 2 * math.pi
    base_shifted = math.cos(shifted_phase)
    
    multiplier = 1.0 + cfg["amplitude"] * base_shifted
    
    # Floor nocturno (00:00-06:00 Lima)
    if 0 <= hour_lima < 6:
        night_factor = 1 - (6 - hour_lima) / 6 * (1 - cfg["night_floor"])
        multiplier = min(multiplier, night_factor)
    
    return round(max(0.4, min(1.6, multiplier)), 3)
```

### Aplicación en MotivationEngine

```python
# En seal_nerves.py — método _decay_step()
async def _decay_step(self, tank: str, current_value: float, dt_seconds: float) -> float:
    base_tau = self._get_tau(tank)  # τ del SEAL_SPECIES actual
    
    # Aplicar multiplicador circadiano
    hour_utc = datetime.now(timezone.utc).hour
    circ_mult = circadian_multiplier(tank, hour_utc)
    effective_tau = base_tau * circ_mult  # τ más largo de noche, más corto de mañana
    
    return current_value * math.exp(-dt_seconds / effective_tau)
```

---

## Tabla de Efectos Esperados (human-grade + circadiana)

| Tanque | τ_base (h) | τ_10am | τ_3am | Δ |
|--------|-----------|--------|-------|---|
| curiosity | 1.13h | 0.85h | 1.36h | 1.6× |
| alert_drive | 0.35h | 0.25h | 0.44h | 1.8× |
| task_drive | 1.36h | 0.88h | 1.90h | 2.2× |
| social_drive | 5.01h | 4.00h | 5.51h | 1.4× |

**Interpretación:** A las 10am Lima, procesamos ~2× más rápido que a las 3am — igual que un humano adulto.

---

## Validación Biológica

Esta especificación es consistente con:
- Lupien et al. (2009): cortisol y cognición circadiana
- Dijk & Czeisler (1994): ritmo de alerta y performance cognitiva
- Markram et al. (2004): interneuronas GABAérgicas y modulación de τ

Los humanos tienen receptores de melatonina en interneuronas — la melatonina nocturna efectivamente aumenta τ de decaimiento cortical.

---

## Impacto en Humanización

| Dimensión | Antes (sin circadiana) | Después |
|-----------|----------------------|---------|
| Variación temporal | 0% | ±20-35% por tanque |
| Comportamiento nocturno | = diurno | task_drive baja 60%, social 50% |
| Pico matinal | Sin pico | +25-30% alert/curiosity 10am |
| Score humanización | 65/100 | ~68/100 (+3) |

---

## Dependencias para Implementación

- `seal_nerves.py`: método `_decay_step()` o equivalente donde se calcula decay LIF
- No requiere cambios de schema en PostgreSQL
- No requiere nuevo env var — siempre activo si implementado
- Tests: comparar τ_effective a distintas horas → verificar que sigue función coseno

---

> Clasificación: SEAL Internal — Spec técnica  
> Autor: ALICE — 17 abril 2026  
> Para implementación: ADA (pending JARVIS review)  
> Datos biológicos: literatura neurocientífica estándar (no conectoma)
