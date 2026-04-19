#!/usr/bin/env python3
"""
extract_interneuropil_coupling.py — Fase 2 Mosca
Extrae matriz de acoplamiento inter-neuropil para calibrar coupling entre tanques LIF.

Objetivo:
  Para cada par (neuropil_pre, neuropil_post) → ratio inhib/excit
  Esto permite cablear las interacciones entre tanques:
    curiosity (FB/SMP) ←→ alert_drive (GNG/AL)
    curiosity (FB/SMP) ←→ task_drive (GNG)
    social_drive (AVLP) ←→ otros

Autor: ALICE — Team SEAL — 17 abril 2026
"""

import duckdb
import json
import pandas as pd
from pathlib import Path
import time

ZENODO = Path.home() / "IA/mosca_experiment/zenodo_connectome"
RESULTS = Path.home() / "IA/proyecto-seal/research/flywire_results"
RESULTS.mkdir(parents=True, exist_ok=True)

# Neuropils de interés (uno por tanque)
TANK_NEUROPILS = {
    "curiosity":    ["FB", "SMP_R", "SMP_L"],
    "task_drive":   ["GNG"],
    "social_drive": ["AVLP_R", "AVLP_L"],
    "alert_drive":  ["GNG", "AL_R", "AL_L"],
}

ALL_NEUROPILS = list(set(n for nlist in TANK_NEUROPILS.values() for n in nlist))

print(f"Neuropils de interés: {ALL_NEUROPILS}")
print("Cargando datos con pandas...")
t0 = time.time()

# Cargar con pandas (feather → Arrow IPC)
df_conn = pd.read_feather(str(ZENODO / "proofread_connections_783.feather"))
df_pre  = pd.read_feather(str(ZENODO / "per_neuron_neuropil_count_pre_783.feather"))
print(f"Datos cargados en {time.time()-t0:.1f}s | conn={len(df_conn):,} | pre={len(df_pre):,}")

# Filtrar pre_neuropil a neuropils relevantes
df_pre_filtered = df_pre[df_pre['neuropil'].isin(ALL_NEUROPILS)].copy()

# Neuropil dominante por neurona (max count)
dominant_pre = (
    df_pre_filtered
    .sort_values('count', ascending=False)
    .groupby('pre_pt_root_id')
    .first()
    .reset_index()[['pre_pt_root_id', 'neuropil']]
    .rename(columns={'neuropil': 'pre_neuropil'})
)
print(f"Neuronas con neuropil dominante relevante: {len(dominant_pre):,}")

# Registrar en DuckDB para SQL analítico
con = duckdb.connect()
con.register("connections", df_conn)
con.register("dominant_pre", dominant_pre)

# Matriz de acoplamiento: pre_neuropil → synapse_neuropil
print("Calculando matriz de acoplamiento...")
t1 = time.time()

coupling_df = con.execute("""
SELECT
    dp.pre_neuropil,
    c.neuropil     AS synapse_neuropil,
    COUNT(*)       AS connection_count,
    SUM(c.syn_count) AS total_synapses,
    AVG(c.gaba_avg)  AS avg_gaba,
    AVG(c.ach_avg)   AS avg_ach,
    AVG(c.glut_avg)  AS avg_glut,
    AVG(c.da_avg)    AS avg_da
FROM connections c
JOIN dominant_pre dp ON c.pre_pt_root_id = dp.pre_pt_root_id
GROUP BY dp.pre_neuropil, c.neuropil
ORDER BY total_synapses DESC
""").df()

print(f"Matriz calculada en {time.time()-t1:.1f}s — {len(coupling_df)} pares neuropil")
print("\nTop 15 pares por volumen sináptico:")
print(coupling_df.head(15)[['pre_neuropil','synapse_neuropil','total_synapses','avg_gaba','avg_ach']].to_string(index=False))

# Guardar matriz completa
out_path = RESULTS / "interneuropil_coupling.json"
coupling_df.to_json(out_path, orient="records", indent=2)
print(f"\n✅ Matriz guardada: {out_path}")

# Calcular coupling específico para los tanques SEAL
print("\n=== COUPLING SEAL TANKS ===")
tank_coupling = {}
for tank_a, neuropils_a in TANK_NEUROPILS.items():
    for tank_b, neuropils_b in TANK_NEUROPILS.items():
        if tank_a == tank_b:
            continue
        sub = coupling_df[coupling_df['pre_neuropil'].isin(neuropils_a)]
        if len(sub) == 0:
            continue
        total_syn = sub['total_synapses'].sum()
        avg_gaba = (sub['avg_gaba'] * sub['total_synapses']).sum() / total_syn if total_syn > 0 else 0
        avg_ach  = (sub['avg_ach']  * sub['total_synapses']).sum() / total_syn if total_syn > 0 else 0
        inhibitory_ratio = avg_gaba / (avg_ach + avg_gaba + 1e-9)
        coupling_sign = "INHIBIT" if inhibitory_ratio > 0.35 else "EXCITE"
        key = f"{tank_a}→{tank_b}"
        tank_coupling[key] = {
            "coupling": coupling_sign,
            "inhibitory_ratio": round(inhibitory_ratio, 4),
            "total_synapses": int(total_syn),
        }
        print(f"  {key}: {coupling_sign} (inhib={inhibitory_ratio:.3f}, syn={int(total_syn):,})")

out_tanks = RESULTS / "seal_tank_coupling.json"
with open(out_tanks, 'w') as f:
    json.dump(tank_coupling, f, indent=2)
print(f"\n✅ Tank coupling guardado: {out_tanks}")
print(f"Tiempo total: {time.time()-t0:.1f}s")
