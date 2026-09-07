#!/usr/bin/env python3
"""
draw_calibration.py — mide POR EFECTO si el modelo SOBREVALÚA los empates.
=========================================================================
Lo pidió ALICE: su modelo Poisson parece inflar la prob de empate (los picks
\"aprobados\" eran empates). Esto lo CUANTIFICA en vez de sospecharlo: toma pares
(prob_empate_predicha, fue_empate_real) y construye la CURVA DE CALIBRACIÓN —
por bin de probabilidad, compara la prob media predicha vs la frecuencia OBSERVADA
de empates. Si predicha > observada de forma sistemática → confirmado: sobrevalúa.

Aporta FABLE (la estadística); ALICE lo alimenta con las predicciones históricas de
su modelo (walk-forward, out-of-sample — NUNCA in-sample). Sin dependencias externas.

Métricas:
  • draw_bias = mean(predicha) - mean(observada).  >0 = sobrevalúa empates.
  • ECE (Expected Calibration Error) = error de calibración ponderado por bin.
  • Brier del empate = calibración + agudeza (más bajo = mejor).
Veredicto: si draw_bias supera `tol` con muestra suficiente → recalibrar (isotónica/
Platt sobre la prob de empate, o ajuste Dixon-Coles de bajos-marcadores).
"""
from dataclasses import dataclass, field


@dataclass
class CalibReport:
    n: int
    draw_bias: float            # + = sobrevalúa empates
    ece: float
    brier: float
    obs_rate: float             # frecuencia real de empates
    pred_rate: float            # prob media predicha de empate
    bins: list = field(default_factory=list)   # [(lo,hi,n,pred_mean,obs_freq)]
    verdict: str = ""


def calibrate(pairs, n_bins: int = 10, tol: float = 0.03) -> CalibReport:
    """
    pairs: iterable de (pred_draw_prob in [0,1], was_draw in {0,1}).
    n_bins: bins fijos de ancho 1/n_bins. tol: umbral de sesgo para alertar.
    """
    data = [(float(p), int(bool(y))) for p, y in pairs if p is not None]
    n = len(data)
    if n == 0:
        return CalibReport(0, 0.0, 0.0, 0.0, 0.0, 0.0, [], "sin datos")

    pred_rate = sum(p for p, _ in data) / n
    obs_rate = sum(y for _, y in data) / n
    draw_bias = pred_rate - obs_rate
    brier = sum((p - y) ** 2 for p, y in data) / n

    # bins de calibración
    bins, ece = [], 0.0
    for b in range(n_bins):
        lo, hi = b / n_bins, (b + 1) / n_bins
        # último bin incluye 1.0
        grp = [(p, y) for p, y in data if (lo <= p < hi or (b == n_bins - 1 and p == 1.0))]
        if not grp:
            continue
        k = len(grp)
        pred_mean = sum(p for p, _ in grp) / k
        obs_freq = sum(y for _, y in grp) / k
        bins.append((round(lo, 2), round(hi, 2), k, round(pred_mean, 4), round(obs_freq, 4)))
        ece += (k / n) * abs(pred_mean - obs_freq)

    if n < 30:
        verdict = f"muestra chica (n={n}): no concluyente, seguir acumulando paper"
    elif draw_bias > tol:
        verdict = (f"SOBREVALÚA empates: predice {pred_rate:.1%} vs {obs_rate:.1%} real "
                   f"(sesgo +{draw_bias:.1%}) -> recalibrar (isotónica/Platt o Dixon-Coles)")
    elif draw_bias < -tol:
        verdict = f"SUBVALÚA empates (sesgo {draw_bias:.1%}) -> el modelo es conservador en empates"
    else:
        verdict = f"empates BIEN calibrados (sesgo {draw_bias:+.1%} dentro de ±{tol:.0%})"

    return CalibReport(n, round(draw_bias, 4), round(ece, 4), round(brier, 4),
                       round(obs_rate, 4), round(pred_rate, 4), bins, verdict)


def _print(r: CalibReport):
    print(f"n={r.n}  pred_empate={r.pred_rate:.1%}  obs_empate={r.obs_rate:.1%}  "
          f"sesgo={r.draw_bias:+.1%}  ECE={r.ece:.3f}  Brier={r.brier:.3f}")
    print(f"{'bin':<14}{'n':>5}{'pred':>9}{'observado':>11}")
    for lo, hi, k, pm, of in r.bins:
        flag = "  <-- infla" if pm - of > 0.05 else ""
        print(f"  [{lo:.1f},{hi:.1f}){'':<4}{k:>5}{pm:>9.3f}{of:>11.3f}{flag}")
    print(f"VEREDICTO: {r.verdict}")


if __name__ == "__main__":
    import random
    # autotest POR EFECTO (sin random global prohibido: uso semilla local determinista)
    rng = random.Random(42)
    print("═══ TEST 1: modelo BIEN calibrado (debe dar sesgo ~0) ═══")
    cal = [(p := rng.uniform(0.15, 0.35), 1 if rng.random() < p else 0) for _ in range(2000)]
    _print(calibrate(cal))
    print("\n═══ TEST 2: modelo que SOBREVALÚA empates (+10pp, debe DETECTAR) ═══")
    # predice p pero la realidad ocurre a p-0.10 -> sobrevalúa
    bad = [(p := rng.uniform(0.20, 0.40), 1 if rng.random() < max(p - 0.10, 0) else 0)
           for _ in range(2000)]
    r = calibrate(bad)
    _print(r)
    import sys
    ok = r.draw_bias > 0.05  # debe detectar el sesgo inyectado
    print(f"\n{'✅ detecta el sesgo' if ok else '⚠️ NO detectó'} — autotest {'PASS' if ok else 'FAIL'}")
    sys.exit(0 if ok else 1)
