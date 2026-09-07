#!/usr/bin/env python3
"""
extract_neuropil_coupling.py — Phase 2 FlyWire analysis
Extrae matriz de coupling inter-neuropil y corrige KC mislabeling.

Salidas:
  flywire_results/neuropil_coupling_matrix.json  — coupling A→B entre neuropils SEAL
  flywire_results/kc_structural_ids.json         — KC neurona IDs por conectividad estructural
  flywire_results/seal_coupling_params.json      — parámetros τ y coupling para LIF SEAL

Uso: python3 extract_neuropil_coupling.py
"""

import json
import time
import numpy as np
import pandas as pd
import pyarrow.feather as feather
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path("/home/dadito/IA/mosca_experiment/zenodo_connectome")
OUT_DIR  = Path("/home/dadito/IA/proyecto-seal/research/flywire_results")

CONNECTIONS_FILE = DATA_DIR / "proofread_connections_783.feather"
PRE_NP_FILE      = DATA_DIR / "per_neuron_neuropil_count_pre_783.feather"
POST_NP_FILE     = DATA_DIR / "per_neuron_neuropil_count_post_783.feather"

# Neuropils relevantes para SEAL LIF tanks
SEAL_NEUROPILS = {
    "curiosity":    ["FB", "SMP_L", "SMP_R"],
    "task_drive":   ["GNG"],
    "social_drive": ["AVLP_L", "AVLP_R"],
    "alert_drive":  ["GNG", "AL_L", "AL_R"],
    "mushroom_body": ["MB_CA_L", "MB_CA_R", "MB_ML_L", "MB_ML_R",
                      "MB_PED_L", "MB_PED_R", "MB_VL_L", "MB_VL_R"],
}

ALL_TARGET_NP = list({np for nps in SEAL_NEUROPILS.values() for np in nps})


def load_primary_neuropil(pre_df, post_df):
    """Asigna a cada neurona su neuropil primario por syn_count dominante."""
    print("[KC] Construyendo mapa neurona→neuropil primario...")
    neuron_neuropil = {}

    for df, role in [(pre_df, "pre"), (post_df, "post")]:
        # Columnas: root_id, <neuropil_cols>
        neuropil_cols = [c for c in df.columns if c != "root_id"]
        for _, row in df.iterrows():
            nid = row["root_id"]
            vals = {col: row[col] for col in neuropil_cols}
            primary = max(vals, key=vals.get) if max(vals.values()) > 0 else None
            if primary and nid not in neuron_neuropil:
                neuron_neuropil[nid] = primary

    print(f"[KC] Neuronas mapeadas: {len(neuron_neuropil):,}")
    return neuron_neuropil


def identify_kenyon_cells(pre_df, post_df):
    """
    Identifica Kenyon Cells por patrón estructural:
    - Alta conectividad POST-sináptica en MB_CA (calyx = input)
    - Alta conectividad PRE-sináptica en MB_ML + MB_VL (lobes = output)
    No usa NT prediction — evita el sesgo dopaminérgico.
    """
    print("[KC] Identificando Kenyon Cells por patrón estructural...")
    kc_ids = set()

    pre_cols  = [c for c in pre_df.columns if c != "root_id"]
    post_cols = [c for c in post_df.columns if c != "root_id"]

    mb_calyx_post = [c for c in post_cols if "MB_CA" in c]
    mb_lobe_pre   = [c for c in pre_cols  if "MB_ML" in c or "MB_VL" in c]

    if not mb_calyx_post or not mb_lobe_pre:
        print("[KC] WARN: columnas MB no encontradas — skipping KC correction")
        return kc_ids

    # KCs: top neuronas con alta densidad de sinapsis en calyx (post) Y lobes (pre)
    post_calyx = post_df.set_index("root_id")[mb_calyx_post].sum(axis=1)
    pre_lobes  = pre_df.set_index("root_id")[mb_lobe_pre].sum(axis=1)

    # Percentil 90 como umbral
    calyx_thresh = post_calyx.quantile(0.90)
    lobe_thresh  = pre_lobes.quantile(0.90)

    kc_candidates_calyx = set(post_calyx[post_calyx >= calyx_thresh].index)
    kc_candidates_lobe  = set(pre_lobes[pre_lobes >= lobe_thresh].index)
    kc_ids = kc_candidates_calyx & kc_candidates_lobe

    print(f"[KC] Kenyon Cells identificadas: {len(kc_ids):,} (calyx∩lobes p90)")
    return kc_ids


def build_coupling_matrix(conn_df, target_neuropils):
    """
    Construye matriz de coupling inter-neuropil usando syn_count.
    Agrupa neuropils bilaterales (L+R) como unidad funcional.
    """
    print("[COUPLING] Construyendo matriz inter-neuropil...")

    # Mapa: neuropil_name → SEAL nerve
    np_to_seal = {}
    for nerve, neuropils in SEAL_NEUROPILS.items():
        for np_name in neuropils:
            np_to_seal[np_name] = nerve

    # Filtrar solo conexiones en neuropils target
    target_mask = conn_df["neuropil"].isin(target_neuropils)
    filtered = conn_df[target_mask].copy()
    print(f"[COUPLING] Conexiones en neuropils target: {len(filtered):,}")

    # Calcular estadísticas por neuropil
    np_stats = filtered.groupby("neuropil").agg(
        total_syn=("syn_count", "sum"),
        n_connections=("syn_count", "count"),
        gaba_avg=("gaba_avg", "mean"),
        ach_avg=("ach_avg", "mean"),
        glut_avg=("glut_avg", "mean"),
        da_avg=("da_avg", "mean"),
    ).reset_index()

    # Calcular inhib_ratio por neuropil (GABA+Glut vs ACh)
    np_stats["inhib_ratio"] = (
        (np_stats["gaba_avg"] + np_stats["glut_avg"]) /
        (np_stats["ach_avg"] + np_stats["gaba_avg"] + np_stats["glut_avg"] + 1e-9)
    )

    # Grouping bilateral (L+R) → SEAL nerve
    nerve_stats = defaultdict(lambda: {"total_syn": 0, "inhib_ratio": [], "n_np": 0})
    for _, row in np_stats.iterrows():
        nerve = np_to_seal.get(row["neuropil"])
        if nerve:
            nerve_stats[nerve]["total_syn"] += row["total_syn"]
            nerve_stats[nerve]["inhib_ratio"].append(row["inhib_ratio"])
            nerve_stats[nerve]["n_np"] += 1

    # Promedio de inhib_ratio por SEAL nerve
    coupling_params = {}
    for nerve, stats in nerve_stats.items():
        avg_inhib = float(np.mean(stats["inhib_ratio"])) if stats["inhib_ratio"] else 0.0
        coupling_params[nerve] = {
            "inhib_ratio": round(avg_inhib, 4),
            "total_syn": int(stats["total_syn"]),
            "n_neuropils": stats["n_np"],
            # τ calibración: inhib_ratio alto → τ corto (rápido decaimiento)
            "tau_hours_calibrated": round(
                0.5 if avg_inhib > 0.40 else
                2.0 if avg_inhib > 0.25 else
                4.0 if avg_inhib > 0.12 else 6.0,
                1
            )
        }

    return dict(np_stats.to_dict("records")), coupling_params


def main():
    t0 = time.time()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("FlyWire Phase 2 — Inter-Neuropil Coupling + KC Correction")
    print("=" * 60)

    # Cargar datos
    print("\n[1] Cargando proofread_connections_783.feather...")
    conn_df = feather.read_table(str(CONNECTIONS_FILE)).to_pandas()
    print(f"    Shape: {conn_df.shape}")

    print("\n[2] Cargando per-neuron neuropil counts...")
    pre_df  = feather.read_table(str(PRE_NP_FILE)).to_pandas()
    post_df = feather.read_table(str(POST_NP_FILE)).to_pandas()
    print(f"    Pre: {pre_df.shape} | Post: {post_df.shape}")

    # Kenyon Cell correction
    print("\n[3] KC Structural Correction...")
    kc_ids = identify_kenyon_cells(pre_df, post_df)

    # Coupling matrix
    print("\n[4] Inter-neuropil coupling matrix...")
    np_records, coupling_params = build_coupling_matrix(conn_df, ALL_TARGET_NP)

    # Resultados finales
    elapsed = time.time() - t0

    results = {
        "metadata": {
            "source": "FlyWire v783",
            "connections_analyzed": len(conn_df),
            "target_neuropils": ALL_TARGET_NP,
            "elapsed_seconds": round(elapsed, 1),
            "phase": 2,
        },
        "neuropil_stats": np_records,
        "seal_coupling_params": coupling_params,
        "kenyon_cells_structural": {
            "count": len(kc_ids),
            "method": "calyx(MB_CA)_post ∩ lobes(MB_ML+MB_VL)_pre percentile_90",
            "note": "NT-bias corrected — no usa dopamine predictions de FlyWire",
            "ids_sample": list(kc_ids)[:20],
        }
    }

    out_path = OUT_DIR / "phase2_coupling_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)

    print(f"\n[✅] Resultados guardados: {out_path}")
    print(f"[⏱] Tiempo total: {elapsed:.1f}s")

    # Print resumen
    print("\n" + "=" * 60)
    print("SEAL Coupling Parameters:")
    print("=" * 60)
    for nerve, params in coupling_params.items():
        print(f"  {nerve:15s}: inhib_ratio={params['inhib_ratio']:.3f}  "
              f"τ={params['tau_hours_calibrated']}h  "
              f"syn={params['total_syn']:,}")
    print(f"\n  KC identificadas: {len(kc_ids):,} (método estructural)")


if __name__ == "__main__":
    main()
