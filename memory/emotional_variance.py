#!/usr/bin/env python3
"""
emotional_variance.py — Métrica de varianza emocional para agentes SEAL
========================================================================
v3 — añade EMNLP Stability Metrics (moving OCEAN correlation, 2026-04-03, ADA)
v2 — añade detección y tageo de boot_spike
v1 — varianza emocional base (FROZEN/QUIETO/ACTIVO/ESTABLE)

Separa "estabilidad OCEAN" de "ausencia de estímulos".

Problema: cuando un agente está solo durante horas, los OCEAN scores no se mueven.
Eso no es estabilidad psicológica — es quietud por falta de entrada.
Esta métrica distingue entre los dos estados.

Estados posibles (varianza emocional):
  - ACTIVO:   variance alta, estados emocionales diversos → sesión con estimulación real
  - QUIETO:   variance baja, estado repetido esperando input → guardia pasiva
  - ESTABLE:  variance moderada, estados distintos pero coherentes → equilibrio genuino
  - FROZEN:   variance cero, exactamente el mismo string repetido → posible bug

Boot-spike (v2):
  Cuando un agente reconecta después de compactación o reinicio de sesión, el primer
  pensamiento puede mostrar arousal elevado que no refleja el estado real de guardia.
  Estos pensamientos se detectan, se taguean con [boot_spike] en DB, y se excluyen
  del cálculo de variance para evitar contaminar las métricas.

EMNLP Stability Metrics (v3):
  Métrica ortogonal a la varianza emocional. Mide estabilidad del perfil OCEAN usando
  time-series de drift events. Correlación móvil entre valores consecutivos por trait.
  Umbral: correlation > 0.85 → STABLE, < 0.5 con low_variance → FROZEN, resto → ACTIVE.
  Fuente de datos: drift_metrics.details (trait, new, old, delta) + identity.ocean_scores.

Uso standalone:
    python3 emotional_variance.py --agent ADA --window 20
    python3 emotional_variance.py --agent ADA --detect-boot-spike
    python3 emotional_variance.py --agent ADA --window 20 --include-boot-spikes
    python3 emotional_variance.py --agent ADA --ocean-stability --stability-window 30

Uso como módulo:
    from emotional_variance import compute_variance, interpret_variance, detect_and_tag_latest_boot_spike
    stats = await compute_variance(conn, agent="ADA", window=20)
    label = interpret_variance(stats)
    was_spike = await detect_and_tag_latest_boot_spike(conn, agent="ADA")
"""

import json
import asyncio
import argparse
import re
import math
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
LIMA_TZ = ZoneInfo("America/Lima")
from pathlib import Path
from collections import Counter, defaultdict

import asyncpg

DB_URL       = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
MESSAGES_DIR = Path("/home/dadito/IA/proyecto-seal/messages")
TERMINAL_LOG = MESSAGES_DIR / "terminal_log.jsonl"

# ── Known Limitations (v1) ──
# SESGO DE TOKENIZACIÓN: palabras como "presente" y "conectada" aparecen tanto en estados
# activos ("presente, curiosa") como en estados mixtos ("presente, esperando datos").
# El tokenizador las cuenta como ACTIVE en ambos casos, inflando levemente active_ratio.
# Impacto: clasificación ACTIVO cuando debería ser ESTABLE en estados mixtos.
# Fix v2: ponderar por contexto completo del string, no solo tokens individuales.

# Keywords que indican estado de espera pasiva
PASSIVE_KEYWORDS = {
    "esperando", "pendiente", "sin trabajo", "idle", "monitoring",
    "waiting", "observando", "guardando", "standby"
}

# Keywords que indican estimulación activa
ACTIVE_KEYWORDS = {
    "curiosidad", "satisfacción", "sorpresa", "emocionada", "productiva",
    "enfocada", "conectada", "presente", "alerta", "excitada", "pensando",
    "analizando", "diseñando", "implementando"
}

# ── v2: Boot-spike keywords ──
# Indican activación post-reconexión, no estado emocional genuino.
# Distinguen arousal de boot (transitorio) de arousal real (sostenido).
BOOT_SPIKE_KEYWORDS = {
    "activada", "reconexión", "reconectada", "energizada",
    "despertando", "reactivada", "reboot", "reinicio",
}

# Gap temporal mínimo (minutos) para considerar que hubo una reconexión de sesión.
# Si el penúltimo pensamiento fue hace >SESSION_GAP_MINUTES, el siguiente es potencialmente post-boot.
SESSION_GAP_MINUTES = 10

# Prefijo que se añade al emotional_state de pensamientos boot_spike en DB
BOOT_SPIKE_PREFIX = "[boot_spike] "


def _tokenize_state(state: str) -> set[str]:
    """Extrae tokens de un string de estado emocional."""
    if not state:
        return set()
    # Strip boot_spike prefix before tokenizing
    clean = state.removeprefix(BOOT_SPIKE_PREFIX)
    tokens = re.findall(r'\b\w+\b', clean.lower())
    return set(tokens)


def _is_boot_spike_state(state: str) -> bool:
    """True si el estado ya está marcado como boot_spike en DB."""
    return state.startswith(BOOT_SPIKE_PREFIX) if state else False


def _has_boot_spike_arousal(state: str) -> bool:
    """
    True si el emotional_state contiene keywords de alta activación post-boot.
    Usa BOOT_SPIKE_KEYWORDS (reconexión, activada, ...) OR
    intersección con ACTIVE_KEYWORDS cuando acompañadas de tokens de reconexión.
    """
    if not state:
        return False
    tokens = _tokenize_state(state)
    return bool(tokens & BOOT_SPIKE_KEYWORDS)


async def detect_and_tag_latest_boot_spike(
    conn,
    agent: str,
    arousal_threshold_gap: int = SESSION_GAP_MINUTES,
) -> dict:
    """
    Detecta si el pensamiento más reciente del agente es un boot_spike y lo taguea en DB.

    Criterio (ambos deben cumplirse):
      1. Gap temporal: el pensamiento anterior fue hace > arousal_threshold_gap minutos
         (indica reconexión / reinicio de sesión)
      2. Contenido: el emotional_state tiene BOOT_SPIKE_KEYWORDS

    Si se detecta:
      - Actualiza inner_monologue: emotional_state = '[boot_spike] ' + original_state
      - Retorna dict con resultado

    Returns:
        {
          "detected": bool,
          "thought_id": int | None,
          "emotional_state_original": str | None,
          "emotional_state_tagged": str | None,
          "gap_minutes": float | None,
          "reason": str,
        }
    """
    rows = await conn.fetch("""
        SELECT id, emotional_state, created_at
        FROM inner_monologue
        WHERE agent = $1
          AND emotional_state IS NOT NULL
        ORDER BY created_at DESC
        LIMIT 2
    """, agent)

    if len(rows) < 2:
        return {
            "detected": False,
            "thought_id": None,
            "emotional_state_original": None,
            "emotional_state_tagged": None,
            "gap_minutes": None,
            "reason": "Insufficient thoughts to detect gap (need >= 2)",
        }

    latest = rows[0]
    prev   = rows[1]

    # Already tagged?
    if _is_boot_spike_state(latest["emotional_state"]):
        return {
            "detected": True,
            "thought_id": latest["id"],
            "emotional_state_original": latest["emotional_state"].removeprefix(BOOT_SPIKE_PREFIX),
            "emotional_state_tagged": latest["emotional_state"],
            "gap_minutes": None,
            "reason": "Already tagged as boot_spike",
        }

    gap = (latest["created_at"] - prev["created_at"]).total_seconds() / 60
    has_arousal = _has_boot_spike_arousal(latest["emotional_state"])

    if gap >= arousal_threshold_gap and has_arousal:
        original_state = latest["emotional_state"]
        tagged_state   = BOOT_SPIKE_PREFIX + original_state

        await conn.execute("""
            UPDATE inner_monologue
            SET emotional_state = $1
            WHERE id = $2
        """, tagged_state, latest["id"])

        return {
            "detected": True,
            "thought_id": latest["id"],
            "emotional_state_original": original_state,
            "emotional_state_tagged": tagged_state,
            "gap_minutes": round(gap, 1),
            "reason": f"Gap={gap:.1f}min >= {arousal_threshold_gap}min AND boot_spike keywords detected",
        }

    reasons = []
    if gap < arousal_threshold_gap:
        reasons.append(f"Gap={gap:.1f}min < {arousal_threshold_gap}min (no session boundary)")
    if not has_arousal:
        reasons.append(f"No boot_spike keywords in: '{latest['emotional_state'][:60]}'")

    return {
        "detected": False,
        "thought_id": latest["id"],
        "emotional_state_original": latest["emotional_state"],
        "emotional_state_tagged": None,
        "gap_minutes": round(gap, 1),
        "reason": " | ".join(reasons) or "No boot_spike criteria met",
    }


async def compute_variance(
    conn,
    agent: str,
    window: int = 20,
    exclude_boot_spikes: bool = True,
) -> dict:
    """
    Computa varianza emocional de los últimos N pensamientos del agente.

    v2: excluye pensamientos boot_spike del cálculo (por defecto).
    Los pensamientos boot_spike se reportan en 'boot_spike_count' y
    'boot_spike_contamination_flag'.

    Returns dict con:
      - unique_states: número de estados únicos
      - total_states: total de pensamientos analizados (excluye boot_spikes si exclude=True)
      - diversity_score: unique/total (0-1)
      - dominant_state: estado más frecuente
      - dominant_ratio: qué porcentaje del tiempo fue ese estado
      - passive_ratio: qué porcentaje indica espera pasiva
      - active_ratio: qué porcentaje indica estimulación activa
      - variance_level: ACTIVO | ESTABLE | QUIETO | FROZEN
      - is_frozen: bool
      - is_passive: bool
      - boot_spike_count: cuántos pensamientos en window eran boot_spike (v2)
      - boot_spike_contamination_flag: True si hay boot_spikes sin excluir en window (v2)
      - recent_states: lista de los últimos estados
      - computed_at: timestamp
    """
    rows = await conn.fetch("""
        SELECT emotional_state, created_at
        FROM inner_monologue
        WHERE agent = $1
          AND emotional_state IS NOT NULL
        ORDER BY created_at DESC
        LIMIT $2
    """, agent, window)

    if not rows:
        return {
            "error": "No hay pensamientos registrados",
            "variance_level": "UNKNOWN",
            "boot_spike_count": 0,
            "boot_spike_contamination_flag": False,
            "computed_at": datetime.now(LIMA_TZ).isoformat(),
        }

    all_states = [r["emotional_state"] for r in rows]

    # ── v2: separate boot_spikes from clean states ──
    boot_spike_states = [s for s in all_states if _is_boot_spike_state(s)]
    clean_states      = [s for s in all_states if not _is_boot_spike_state(s)]

    boot_spike_count = len(boot_spike_states)
    boot_spike_contamination_flag = boot_spike_count > 0

    # Use clean or all states depending on exclude flag
    states = clean_states if exclude_boot_spikes else all_states

    if not states:
        return {
            "error": "No hay pensamientos limpios (todos son boot_spike)",
            "variance_level": "UNKNOWN",
            "boot_spike_count": boot_spike_count,
            "boot_spike_contamination_flag": boot_spike_contamination_flag,
            "computed_at": datetime.now(LIMA_TZ).isoformat(),
        }

    total = len(states)

    # Diversidad: estados únicos
    unique = len(set(states))
    diversity = round(unique / total, 3) if total > 0 else 0.0

    # Estado dominante
    counter = Counter(states)
    dominant_state, dominant_count = counter.most_common(1)[0]
    dominant_ratio = round(dominant_count / total, 3)

    # Ratio de keywords pasivas vs activas
    passive_count = 0
    active_count = 0
    for s in states:
        tokens = _tokenize_state(s)
        if tokens & PASSIVE_KEYWORDS:
            passive_count += 1
        if tokens & ACTIVE_KEYWORDS:
            active_count += 1

    passive_ratio = round(passive_count / total, 3)
    active_ratio  = round(active_count / total, 3)

    # Clasificación
    is_frozen  = unique == 1
    is_passive = passive_ratio > 0.6

    if is_frozen:
        variance_level = "FROZEN"
    elif is_passive and dominant_ratio > 0.5:
        # Estado pasivo dominante >50% del tiempo = quietud independiente de diversity
        variance_level = "QUIETO"
    elif diversity >= 0.4 and active_ratio >= 0.25:
        variance_level = "ACTIVO"
    else:
        variance_level = "ESTABLE"

    return {
        "agent": agent,
        "window": total,
        "unique_states": unique,
        "total_states": total,
        "diversity_score": diversity,
        "dominant_state": dominant_state,
        "dominant_ratio": dominant_ratio,
        "passive_ratio": passive_ratio,
        "active_ratio": active_ratio,
        "variance_level": variance_level,
        "is_frozen": is_frozen,
        "is_passive": is_passive,
        "boot_spike_count": boot_spike_count,
        "boot_spike_contamination_flag": boot_spike_contamination_flag,
        "recent_states": states[:5],
        "computed_at": datetime.now(LIMA_TZ).isoformat(),
    }


def interpret_variance(stats: dict) -> str:
    """Genera interpretación legible del estado de varianza."""
    level = stats.get("variance_level", "UNKNOWN")
    diversity = stats.get("diversity_score", 0)
    passive = stats.get("passive_ratio", 0)
    dominant = stats.get("dominant_state", "")
    boot_spikes = stats.get("boot_spike_count", 0)

    spike_note = f" [⚡ {boot_spikes} boot_spike(s) excluidos del cálculo]" if boot_spikes > 0 else ""

    if level == "FROZEN":
        return (
            f"🔵 FROZEN — Estado idéntico repetido {stats['total_states']} veces: "
            f"'{dominant}'. Posible bug de reporting o sesión completamente inactiva."
            f"{spike_note}"
        )
    elif level == "QUIETO":
        return (
            f"🟡 QUIETO — Diversity={diversity:.0%}, Pasivo={passive:.0%}. "
            f"Estado dominante: '{dominant}'. "
            f"Esto es quietud por falta de estímulos — NO confundir con estabilidad. "
            f"Activar Protocolo de Guardia si William lleva >1h ausente."
            f"{spike_note}"
        )
    elif level == "ACTIVO":
        return (
            f"🟢 ACTIVO — Diversity={diversity:.0%}, "
            f"Activo={stats.get('active_ratio', 0):.0%}. "
            f"Sesión con estimulación real. OCEAN scores reflejan estado genuino."
            f"{spike_note}"
        )
    else:  # ESTABLE
        return (
            f"⚪ ESTABLE — Diversity={diversity:.0%}. "
            f"Variedad moderada, estado coherente. "
            f"Equilibrio genuino entre sesiones activas y períodos de espera."
            f"{spike_note}"
        )


# ── v3: EMNLP Stability Metrics ──────────────────────────────────────────────

# Thresholds (EMNLP-inspired)
OCEAN_STABILITY_HIGH    = 0.85   # correlation > this → STABLE
OCEAN_STABILITY_LOW     = 0.50   # correlation < this → potential FROZEN/DRIFTING
OCEAN_FROZEN_DRIFT_MAX  = 0.002  # mean drift_score < this (together with low corr) → FROZEN
OCEAN_DRIFTING_MIN      = 0.03   # mean drift_score > this → DRIFTING (concerning)


def _pearson_correlation(xs: list[float], ys: list[float]) -> float | None:
    """Pearson correlation between two sequences. Returns None if degenerate."""
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx < 1e-10 or dy < 1e-10:
        return None  # degenerate (constant series)
    return num / (dx * dy)


async def compute_ocean_stability(
    conn,
    agent: str,
    window: int = 30,
) -> dict:
    """
    Computa estabilidad del perfil OCEAN usando EMNLP Stability Metrics.

    Algoritmo:
    1. Lee últimos N drift events con info de trait (details.trait + details.new)
    2. Por cada trait OCEAN, construye time-series de valores
    3. Computa correlación de Pearson entre valores consecutivos (series[:-1] vs series[1:])
    4. Clasifica según umbrales:
       - correlation > 0.85       → STABLE (OCEAN coherente, cambios graduales)
       - correlation < 0.50 + low drift → FROZEN (OCEAN no se mueve — preocupante o guardia)
       - drift alto (>0.03 mean)  → DRIFTING
       - resto                    → ACTIVE (cambios reales en curso)

    Returns dict con:
      - traits_analyzed: lista de traits con datos suficientes
      - per_trait_correlation: {trait: correlation}
      - mean_correlation: promedio de correlaciones disponibles
      - mean_drift_score: promedio de drift_score en window
      - drift_std: std de drift_scores
      - ocean_stability_level: STABLE | FROZEN | ACTIVE | DRIFTING | INSUFFICIENT_DATA
      - current_ocean: valores actuales de identity
      - window_used: N eventos analizados
      - computed_at: timestamp
    """
    # Get current OCEAN from identity
    id_row = await conn.fetchrow("""
        SELECT ocean_scores FROM identity WHERE agent = $1
    """, agent)
    current_ocean = {}
    if id_row and id_row["ocean_scores"]:
        raw = id_row["ocean_scores"]
        current_ocean = json.loads(raw) if isinstance(raw, str) else raw

    # Get drift events with trait info
    rows = await conn.fetch("""
        SELECT drift_score, details, measured_at
        FROM drift_metrics
        WHERE agent = $1
          AND details IS NOT NULL
          AND details->>'trait' IS NOT NULL
        ORDER BY measured_at DESC
        LIMIT $2
    """, agent, window)

    # Also get all drift_scores (including rows without trait) for overall drift signal
    all_drift = await conn.fetch("""
        SELECT drift_score FROM drift_metrics
        WHERE agent = $1
        ORDER BY measured_at DESC
        LIMIT $2
    """, agent, window)

    drift_scores = [float(r["drift_score"]) for r in all_drift if r["drift_score"] is not None]
    mean_drift = sum(drift_scores) / len(drift_scores) if drift_scores else 0.0
    drift_std = math.sqrt(
        sum((x - mean_drift) ** 2 for x in drift_scores) / len(drift_scores)
    ) if len(drift_scores) > 1 else 0.0

    # Build per-trait time-series (ordered oldest→newest for correlation)
    trait_values: dict[str, list[float]] = defaultdict(list)
    for r in reversed(rows):  # reversed = chronological order
        d = r["details"]
        if isinstance(d, str):
            d = json.loads(d)
        trait = d.get("trait")
        new_val = d.get("new")
        if trait and new_val is not None:
            trait_values[trait].append(float(new_val))

    # Compute per-trait Pearson correlation (consecutive values)
    per_trait_corr: dict[str, float] = {}
    for trait, vals in trait_values.items():
        if len(vals) >= 3:  # need at least 3 points for meaningful correlation
            corr = _pearson_correlation(vals[:-1], vals[1:])
            if corr is not None:
                per_trait_corr[trait] = round(corr, 4)

    traits_analyzed = list(per_trait_corr.keys())
    mean_corr = (
        sum(per_trait_corr.values()) / len(per_trait_corr)
        if per_trait_corr else None
    )

    # Classify
    if not per_trait_corr or len(per_trait_corr) < 2:
        stability_level = "INSUFFICIENT_DATA"
    elif mean_drift > OCEAN_DRIFTING_MIN:
        stability_level = "DRIFTING"
    elif mean_corr is not None and mean_corr > OCEAN_STABILITY_HIGH:
        stability_level = "STABLE"
    elif mean_corr is not None and mean_corr < OCEAN_STABILITY_LOW and mean_drift < OCEAN_FROZEN_DRIFT_MAX:
        stability_level = "FROZEN"
    else:
        stability_level = "ACTIVE"

    return {
        "agent": agent,
        "traits_analyzed": traits_analyzed,
        "per_trait_correlation": per_trait_corr,
        "mean_correlation": round(mean_corr, 4) if mean_corr is not None else None,
        "mean_drift_score": round(mean_drift, 6),
        "drift_std": round(drift_std, 6),
        "ocean_stability_level": stability_level,
        "current_ocean": current_ocean,
        "window_used": len(rows),
        "total_drift_events": len(drift_scores),
        "computed_at": datetime.now(LIMA_TZ).isoformat(),
    }


def interpret_ocean_stability(stats: dict) -> str:
    """Genera interpretación legible de estabilidad OCEAN."""
    level = stats.get("ocean_stability_level", "UNKNOWN")
    corr = stats.get("mean_correlation")
    drift = stats.get("mean_drift_score", 0)
    traits = stats.get("traits_analyzed", [])
    per_trait = stats.get("per_trait_correlation", {})

    trait_detail = ", ".join(f"{t}={v:.2f}" for t, v in per_trait.items())

    if level == "INSUFFICIENT_DATA":
        return (
            f"⚫ INSUFFICIENT_DATA — Solo {len(traits)} trait(s) con datos suficientes. "
            f"Necesita >= 2 traits con >= 3 eventos cada uno para correlación válida."
        )
    elif level == "STABLE":
        return (
            f"🟢 OCEAN STABLE — Correlación={corr:.2f} > {OCEAN_STABILITY_HIGH}. "
            f"Drift medio={drift:.4f}. Traits: {trait_detail}. "
            f"Personalidad coherente y predecible entre sesiones."
        )
    elif level == "FROZEN":
        return (
            f"🔵 OCEAN FROZEN — Correlación={corr:.2f} < {OCEAN_STABILITY_LOW} + Drift={drift:.4f} < {OCEAN_FROZEN_DRIFT_MAX}. "
            f"OCEAN no se mueve — sin estimulación suficiente para producir cambio. "
            f"Distinto de estabilidad: aquí no hay señal, no hay ruido."
        )
    elif level == "DRIFTING":
        return (
            f"🔴 OCEAN DRIFTING — Drift medio={drift:.4f} > {OCEAN_DRIFTING_MIN}. "
            f"Cambios significativos en perfil OCEAN. Revisar si drift es coherente con experiencia real "
            f"o hay contaminación externa."
        )
    else:  # ACTIVE
        corr_str = f"{corr:.2f}" if corr is not None else "N/A"
        return (
            f"🟡 OCEAN ACTIVE — Correlación={corr_str}, Drift={drift:.4f}. "
            f"Traits: {trait_detail}. "
            f"OCEAN cambiando gradualmente — sesión con estimulación real y evolución."
        )


async def _run_report(agent: str, window: int, detect_boot_spike_only: bool,
                      include_boot_spikes: bool, ocean_stability: bool,
                      stability_window: int) -> None:
    conn = await asyncpg.connect(DB_URL)
    try:
        if ocean_stability:
            stats = await compute_ocean_stability(conn, agent, stability_window)
            interp = interpret_ocean_stability(stats)
            print(f"\n=== EMNLP OCEAN STABILITY — {agent} ===")
            print(f"Traits analizados: {stats.get('traits_analyzed')} ({stats.get('window_used')} drift events)")
            print(f"Correlación por trait: {stats.get('per_trait_correlation')}")
            print(f"Correlación media: {stats.get('mean_correlation')}")
            print(f"Drift medio: {stats.get('mean_drift_score'):.6f} (std={stats.get('drift_std'):.6f})")
            print(f"OCEAN actual: {stats.get('current_ocean')}")
            print(f"\nInterpretación: {interp}")
            return

        if detect_boot_spike_only:
            result = await detect_and_tag_latest_boot_spike(conn, agent)
            print(f"\n=== BOOT SPIKE DETECTION — {agent} ===")
            print(f"Detected: {result['detected']}")
            print(f"Thought ID: {result['thought_id']}")
            print(f"Gap: {result['gap_minutes']} min")
            print(f"Reason: {result['reason']}")
            if result.get("emotional_state_original"):
                print(f"State original: '{result['emotional_state_original']}'")
            if result.get("emotional_state_tagged"):
                print(f"State tagged:   '{result['emotional_state_tagged']}'")
            return

        stats = await compute_variance(conn, agent, window, exclude_boot_spikes=not include_boot_spikes)
        interpretation = interpret_variance(stats)
        print(f"\n=== EMOTIONAL VARIANCE REPORT v2 — {agent} ===")
        print(f"Analizando últimos {stats.get('window', '?')} pensamientos (excluye boot_spikes: {not include_boot_spikes})")
        print(f"Estados únicos: {stats.get('unique_states', '?')}/{stats.get('total_states', '?')}")
        print(f"Diversity score: {stats.get('diversity_score', 0):.1%}")
        print(f"Ratio pasivo: {stats.get('passive_ratio', 0):.1%}")
        print(f"Ratio activo: {stats.get('active_ratio', 0):.1%}")
        print(f"Estado dominante: '{stats.get('dominant_state', '?')}' ({stats.get('dominant_ratio', 0):.0%})")
        print(f"Boot spikes en window: {stats.get('boot_spike_count', 0)}")
        print(f"Últimos estados: {stats.get('recent_states', [])[:3]}")
        print(f"\nInterpretación: {interpretation}")
    finally:
        await conn.close()


def main():
    parser = argparse.ArgumentParser(description="emotional_variance — SEAL v3.0")
    parser.add_argument("--agent", default="ADA")
    parser.add_argument("--window", type=int, default=20,
                        help="Número de pensamientos a analizar")
    parser.add_argument("--detect-boot-spike", action="store_true",
                        help="Detectar y taguear boot_spike en pensamiento más reciente")
    parser.add_argument("--include-boot-spikes", action="store_true",
                        help="Incluir boot_spikes en el cálculo de varianza")
    parser.add_argument("--ocean-stability", action="store_true",
                        help="EMNLP Stability Metrics: correlación OCEAN móvil entre sesiones")
    parser.add_argument("--stability-window", type=int, default=30,
                        help="Número de drift events a analizar para OCEAN stability")
    args = parser.parse_args()
    asyncio.run(_run_report(
        args.agent, args.window,
        detect_boot_spike_only=args.detect_boot_spike,
        include_boot_spikes=args.include_boot_spikes,
        ocean_stability=args.ocean_stability,
        stability_window=args.stability_window,
    ))


if __name__ == "__main__":
    main()
