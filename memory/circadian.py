"""
circadian.py — Variación circadiana de tanques LIF para SEAL
Spec: ALICE (2026-04-17) | Impl: ADA (2026-04-18)

τ_effective = τ_base × circadian_multiplier(tank, hour_utc)

Datos biológicos base:
- Lupien et al. (2009): cortisol peak 08-10h Lima → arousal +20-30%
- Dijk & Czeisler (1994): alerta ejecutiva peak 10-13h
- Markram et al. (2004): melatonina noctorna aumenta τ de decaimiento cortical
"""

import math
from datetime import datetime, timezone

LIMA_UTC_OFFSET = -5  # PET — America/Lima

TANK_CIRCADIAN: dict[str, dict] = {
    "curiosity": {
        "amplitude":   0.25,   # ±25% variación
        "phase_shift": 0,      # peak 10am Lima (cortisol)
        "night_floor": 0.6,    # mínimo nocturno (03:00)
    },
    "alert_drive": {
        "amplitude":   0.30,   # ±30% — más sensible a hora
        "phase_shift": 0,      # peak 10am Lima
        "night_floor": 0.75,   # alert siempre algo activo
    },
    "task_drive": {
        "amplitude":   0.35,   # ±35% — fuerte variación
        "phase_shift": -2,     # peak 12:00 Lima (deadline pressure)
        "night_floor": 0.4,    # casi 0 a las 3am
    },
    "social_drive": {
        "amplitude":   0.20,   # ±20%
        "phase_shift": +2,     # peak 12:00-14:00 Lima (hora social)
        "night_floor": 0.5,
    },
    "context_pressure": {
        "amplitude":   0.10,   # variación mínima — es técnico
        "phase_shift": 0,
        "night_floor": 0.85,
    },
}

_DEFAULT_CIRC = {"amplitude": 0.15, "phase_shift": 0, "night_floor": 0.7}


def circadian_multiplier(tank: str, hour_utc: int | None = None) -> float:
    """
    Retorna multiplicador [0.4, 1.6] para τ según hora local Lima.
    multiplier > 1 → τ más largo  (procesamiento más lento — noche)
    multiplier < 1 → τ más corto  (más ágil — mañana/día)

    Args:
        tank: nombre del tanque SEAL
        hour_utc: hora UTC (0-23). Si None, usa datetime.now(utc).hour.
    """
    if hour_utc is None:
        hour_utc = datetime.now(timezone.utc).hour

    hour_lima = (hour_utc + LIMA_UTC_OFFSET) % 24
    cfg = TANK_CIRCADIAN.get(tank, _DEFAULT_CIRC)

    shifted_hour = (hour_lima + cfg["phase_shift"]) % 24
    phase = (shifted_hour - 10) / 24 * 2 * math.pi
    base = math.cos(phase)  # [-1, 1], +1 = peak diurno, -1 = nadir nocturno

    # multiplier: > 1 de noche (cos negativo), < 1 de día (cos positivo)
    # Invertido: noche = lento (τ largo), día = ágil (τ corto)
    multiplier = 1.0 - cfg["amplitude"] * base

    # Floor nocturno (00:00-06:00 Lima): forzar τ largo
    if 0 <= hour_lima < 6:
        night_factor = cfg["night_floor"] + (1.0 - cfg["night_floor"]) * (hour_lima / 6)
        multiplier = max(multiplier, night_factor)

    return round(max(0.4, min(1.6, multiplier)), 3)


def effective_tau(tank: str, base_tau_s: float, hour_utc: int | None = None) -> float:
    """Retorna τ efectivo aplicando multiplicador circadiano."""
    return base_tau_s * circadian_multiplier(tank, hour_utc)
