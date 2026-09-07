#!/usr/bin/env python3
"""
flywire_intertank_coupling.py — Extrae coupling inter-neuropil para LIF Fase 2
SEAL Team | ADA — 2026-04-17

Objetivo: calcular la fuerza de conexión entre los neuropils que mapean a
cada tanque de motivación SEAL, para calibrar el acoplamiento inter-tank.

Tanques SEAL → Neuropils FlyWire:
  curiosity   → FB (Fan-body), SMP
  task_drive  → GNG
  social_drive→ AVLP
  alert_drive → GNG, AL
"""

import pandas as pd
import json
from pathlib import Path
from itertools import product

FLYWIRE_DIR = Path.home() / "IA/datasets/flywire"
RESULTS_DIR = Path.home() / "IA/proyecto-seal/research/flywire_results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# Mapeo tanque → neuropils primarios (prefijo, case-insensitive)
TANK_NEUROPILS = {
    "curiosity":    ["FB", "SMP"],
    "task_drive":   ["GNG"],
    "social_drive": ["AVLP"],
    "alert_drive":  ["GNG", "AL"],
}

def neuropil_to_tank(neuropil: str) -> list[str]:
    """Determina a qué tanques pertenece un neuropil (puede ser múltiple)."""
    tanks = []
    np_upper = neuropil.upper()
    for tank, prefixes in TANK_NEUROPILS.items():
        for prefix in prefixes:
            if np_upper.startswith(prefix):
                tanks.append(tank)
                break
    return tanks

def main():
    print("=== FlyWire Inter-Tank Coupling Analysis ===")

    # Cargar connectome
    conn_file = FLYWIRE_DIR / "proofread_connections_783.feather"
    print(f"Cargando {conn_file}...")
    df = pd.read_feather(str(conn_file))
    print(f"  {len(df):,} sinapsis cargadas")
    print(f"  Columnas: {list(df.columns)}")

    # Ver neuropils únicos
    unique_neuropils = df['neuropil'].unique()
    print(f"\nNeuropils únicos: {len(unique_neuropils)}")

    # Filtrar neuropils relevantes para nuestros tanques
    relevant_neuropils = []
    for np_name in unique_neuropils:
        tanks = neuropil_to_tank(str(np_name))
        if tanks:
            relevant_neuropils.append((np_name, tanks))

    print(f"\nNeuropils relevantes: {len(relevant_neuropils)}")
    for np_name, tanks in sorted(relevant_neuropils):
        print(f"  {np_name} → {tanks}")

    # Calcular sinapsis por neuropil
    np_stats = df.groupby('neuropil').agg(
        total_synapses=('syn_count', 'sum'),
        connections=('syn_count', 'count'),
        mean_ach=('ach_avg', 'mean'),
        mean_gaba=('gaba_avg', 'mean'),
        mean_da=('da_avg', 'mean'),
    ).reset_index()

    # Calcular inhibitory ratio por neuropil (gaba / (ach + gaba + glut))
    if 'gaba_avg' in df.columns and 'ach_avg' in df.columns:
        np_stats2 = df.groupby('neuropil').agg(
            mean_gaba=('gaba_avg', 'mean'),
            mean_ach=('ach_avg', 'mean'),
            mean_glut=('glut_avg', 'mean'),
        ).reset_index()
        np_stats2['inhib_ratio'] = np_stats2['mean_gaba'] / (
            np_stats2['mean_gaba'] + np_stats2['mean_ach'] + np_stats2['mean_glut'] + 1e-9
        )
        np_stats = np_stats.merge(np_stats2[['neuropil', 'inhib_ratio']], on='neuropil', how='left')

    # Calcular coupling entre tanques basado en sinapsis compartidas
    # Para cada par de neuropils (A→B), cuántas sinapsis cruzan
    # Nota: en este dataset el neuropil es donde está la sinapsis.
    # Usamos la coocurrencia de neuropils relevantes como proxy de coupling.

    tank_coupling = {}
    tank_names = list(TANK_NEUROPILS.keys())

    for tank_a, tank_b in product(tank_names, tank_names):
        if tank_a == tank_b:
            continue

        nps_a = [np for np, tanks in relevant_neuropils if tank_a in tanks]
        nps_b = [np for np, tanks in relevant_neuropils if tank_b in tanks]

        if not nps_a or not nps_b:
            continue

        # Sinapsis en neuropils del tank_a que conectan hacia neuronas cuyos
        # neuropils relevantes incluyen tank_b — proxy: syn count en nps_a ∩ nps_b
        shared_nps = set(nps_a) & set(nps_b)
        syns_a = df[df['neuropil'].isin(nps_a)]['syn_count'].sum()
        syns_b = df[df['neuropil'].isin(nps_b)]['syn_count'].sum()
        syns_shared = df[df['neuropil'].isin(shared_nps)]['syn_count'].sum() if shared_nps else 0

        # Inhibitory ratio en neuropils de tank_a
        inhib_a = df[df['neuropil'].isin(nps_a)]['gaba_avg'].mean() if nps_a else 0

        coupling_key = f"{tank_a}→{tank_b}"
        tank_coupling[coupling_key] = {
            "tank_a": tank_a,
            "tank_b": tank_b,
            "neuropils_a": nps_a,
            "neuropils_b": nps_b,
            "shared_neuropils": list(shared_nps),
            "synapses_a": int(syns_a),
            "synapses_b": int(syns_b),
            "synapses_shared": int(syns_shared),
            "mean_inhib_a": float(inhib_a),
            "has_direct_coupling": len(shared_nps) > 0,
        }

    # Neuropil stats para los tanques SEAL
    print("\n=== NEUROPIL STATS POR TANQUE ===")
    for tank, neuropils in TANK_NEUROPILS.items():
        mask = df['neuropil'].apply(lambda x: any(str(x).upper().startswith(p) for p in neuropils))
        tank_df = df[mask]
        if len(tank_df) == 0:
            print(f"\n{tank}: SIN DATOS")
            continue
        total_syn = tank_df['syn_count'].sum()
        inhib_ratio = tank_df['gaba_avg'].mean() / (
            tank_df['gaba_avg'].mean() + tank_df['ach_avg'].mean() + tank_df['glut_avg'].mean() + 1e-9
        )
        print(f"\n{tank} ({neuropils}):")
        print(f"  Sinapsis totales: {total_syn:,}")
        print(f"  Conexiones: {len(tank_df):,}")
        print(f"  Inhib ratio (gaba/total): {inhib_ratio:.3f}")
        print(f"  Mean ACh: {tank_df['ach_avg'].mean():.3f}")
        print(f"  Mean GABA: {tank_df['gaba_avg'].mean():.3f}")
        print(f"  Mean DA: {tank_df['da_avg'].mean():.3f}")

    print("\n=== COUPLING INTER-TANK ===")
    for key, data in sorted(tank_coupling.items()):
        direct = "DIRECTO" if data["has_direct_coupling"] else "indirecto"
        print(f"\n{key} [{direct}]:")
        print(f"  Shared neuropils: {data['shared_neuropils']}")
        print(f"  Syns shared: {data['synapses_shared']:,}")
        print(f"  Inhib ratio origen: {data['mean_inhib_a']:.3f}")

    # Guardar resultados
    out_file = RESULTS_DIR / "intertank_coupling_v1.json"
    results = {
        "tank_coupling": tank_coupling,
        "tank_neuropil_map": TANK_NEUROPILS,
        "date": "2026-04-17",
        "source": "FlyWire v783 (proofread_connections_783.feather)",
    }
    with open(out_file, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\n✅ Resultados guardados: {out_file}")

if __name__ == "__main__":
    main()
