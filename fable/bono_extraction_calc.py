#!/usr/bin/env python3
"""
bono_extraction_calc.py — motor matemático de COBERTURA para el bot de bono de William.
=======================================================================================
Calcula el stake de cobertura (lay) y la ganancia GARANTIZADA al cubrir una apuesta del
bookmaker contra un exchange (o casa opuesta). Es el corazón del bot de extracción de bono:
NO predice resultados — asegura una fracción del bono pase lo que pase el partido.

Lo aporta FABLE (la matemática) para que ALICE lo cablee al bot. Sin dependencias externas.

Conceptos:
  back  = apuesta en el bookmaker (a favor de un resultado) a cuota `back_odds` (decimal).
  lay   = apuesta EN CONTRA del mismo resultado en un exchange a cuota `lay_odds`, con
          comisión `commission` (p.ej. 0.05 = 5% de Betfair sobre ganancia neta).
  - Apuesta CUALIFICANTE (stake real, se devuelve): lay = back_odds*stake / (lay_odds - comm)
  - FREEBET SNR (Stake Not Returned, la freebet no se devuelve):
                                     lay = (back_odds-1)*stake / (lay_odds - comm)
"""
from dataclasses import dataclass


@dataclass
class Cobertura:
    lay_stake: float          # cuánto apostar en contra (exchange)
    liability: float          # riesgo que bloquea el exchange
    profit_si_gana_back: float    # resultado neto si gana el bookmaker
    profit_si_gana_lay: float     # resultado neto si gana el exchange (no-back)
    guaranteed: float         # ganancia asegurada (el mínimo de ambos escenarios)
    pct_extraido: float       # % del stake/bono convertido en caja garantizada


def cobertura(back_odds: float, lay_odds: float, stake: float,
              commission: float = 0.0, freebet: bool = False) -> Cobertura:
    """Stake de cobertura + ganancia garantizada. freebet=True para freebets SNR."""
    if back_odds <= 1 or lay_odds <= 1:
        raise ValueError("las cuotas decimales deben ser > 1")
    if not (0 <= commission < 1):
        raise ValueError("commission debe estar en [0,1)")

    if freebet:
        lay_stake = (back_odds - 1) * stake / (lay_odds - commission)
    else:
        lay_stake = back_odds * stake / (lay_odds - commission)

    liability = lay_stake * (lay_odds - 1)

    # Si gana el BACK (bookmaker): cobro la ganancia del back y pago la liability del lay.
    back_win = (back_odds - 1) * stake if freebet else back_odds * stake - stake
    profit_back = back_win - liability
    # Si gana el LAY (no ocurre el resultado): pierdo el stake real (0 si freebet) y
    # gano el lay_stake neto de comisión.
    stake_cost = 0.0 if freebet else stake
    profit_lay = lay_stake * (1 - commission) - stake_cost

    guaranteed = min(profit_back, profit_lay)
    base = stake  # para freebet, % sobre el valor nominal de la freebet
    pct = (guaranteed / base * 100) if base else 0.0
    return Cobertura(round(lay_stake, 2), round(liability, 2),
                     round(profit_back, 2), round(profit_lay, 2),
                     round(guaranteed, 2), round(pct, 1))


def rollover_plan(bono: float, rollover_x: float, cuota_media: float,
                  lay_odds_media: float, commission: float = 0.05) -> dict:
    """
    Estima el valor EXTRAÍBLE de un bono con requisito de rollover (apostar el bono xN
    veces antes de retirar). Cada vuelta se cubre; la pérdida por vuelta ~ el spread
    back/lay + comisión. Devuelve volumen a apostar y caja esperada.
    """
    volumen_total = bono * rollover_x
    # pérdida fraccional por cubrir 1 unidad (spread + comisión), aproximación estándar
    perdida_por_unidad = (cuota_media / lay_odds_media) - 1 + commission * (lay_odds_media - 1) / lay_odds_media
    perdida_por_unidad = max(perdida_por_unidad, 0.0)
    coste_cobertura = volumen_total * perdida_por_unidad
    extraible = bono - coste_cobertura
    return {
        "volumen_a_apostar": round(volumen_total, 2),
        "coste_estimado_cobertura": round(coste_cobertura, 2),
        "caja_extraible_estimada": round(extraible, 2),
        "pct_bono_extraido": round(extraible / bono * 100, 1) if bono else 0.0,
        "nota": "estimación; el número exacto depende de las cuotas reales por evento.",
    }


if __name__ == "__main__":
    print("═══ DEMO calculadora de cobertura de bono ═══\n")
    # Caso 1: apuesta cualificante (depósito), cuota back 2.0, lay 2.1, 5% comisión, S/.50
    c = cobertura(back_odds=2.0, lay_odds=2.1, stake=50, commission=0.05)
    print("CUALIFICANTE S/.50 @ back 2.0 / lay 2.1 (5% comm):")
    print(f"  apostar en contra (lay): S/.{c.lay_stake}  | liability: S/.{c.liability}")
    print(f"  resultado garantizado: S/.{c.guaranteed} ({c.pct_extraido}% del stake)")
    print(f"  (gana-back: {c.profit_si_gana_back} | gana-lay: {c.profit_si_gana_lay})\n")

    # Caso 2: FREEBET de S/.100, back 5.0, lay 5.2, 5% comisión
    f = cobertura(back_odds=5.0, lay_odds=5.2, stake=100, commission=0.05, freebet=True)
    print("FREEBET S/.100 @ back 5.0 / lay 5.2 (5% comm):")
    print(f"  apostar en contra (lay): S/.{f.lay_stake}  | liability: S/.{f.liability}")
    print(f"  EXTRAÍDO garantizado: S/.{f.guaranteed} ({f.pct_extraido}% de la freebet)\n")

    # Caso 3: bono S/.200 con rollover x6
    r = rollover_plan(bono=200, rollover_x=6, cuota_media=1.9, lay_odds_media=2.0)
    print("BONO S/.200 rollover x6 (cuota media 1.9 / lay 2.0):")
    for k, v in r.items():
        print(f"  {k}: {v}")
