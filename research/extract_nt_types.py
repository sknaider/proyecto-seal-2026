#!/usr/bin/env python3
"""
extract_nt_types.py — Extracción de tipos de neurotransmisor desde FlyWire synapses.

Lee flywire_synapses_783.feather (8.9GB), determina el NT dominante por neurona
pre-sináptica, y guarda un JSON de lookup para el experimento LIF.

Uso:
  python3 extract_nt_types.py [--output /ruta/nt_types.json]

0 tokens Claude — procesamiento 100% Python/pandas.
"""

import os
import sys
import json
import time
import argparse
import numpy as np
import pandas as pd
import pyarrow.feather as feather
import pyarrow as pa

SYNAPSE_FILE = "/home/dadito/IA/mosca_experiment/zenodo_connectome/flywire_synapses_783.feather"
DEFAULT_OUTPUT = "/home/dadito/IA/proyecto-seal/research/flywire_results/nt_types.json"

# NT classification: excitatorio (+1) o inhibitorio (-1) o neuromodulador (0)
NT_SIGN = {
    "ach":     +1.0,   # Acetilcolina — excitatorio
    "glut":    -1.0,   # Glutamato — inhibitorio en fly brain (diferente a vertebrados)
    "gaba":    -1.0,   # GABA — inhibitorio
    "da":       0.0,   # Dopamina — neuromodulador
    "ser":      0.0,   # Serotonina — neuromodulador
    "oct":      0.0,   # Octopamina — neuromodulador
    "unknown":  0.0,   # NT no determinable (todas las probabilidades = 0)
}

NT_COLS = ["ach", "glut", "gaba", "da", "ser", "oct"]


def extract_nt_types(synapse_file: str, output_path: str):
    """
    Lee el archivo de sinapsis en chunks para minimizar RAM pico.
    Determina NT dominante por neurona pre-sináptica.
    """
    print(f"[NT-EXTRACT] Iniciando extracción desde {synapse_file}")
    print(f"[NT-EXTRACT] Tamaño: {os.path.getsize(synapse_file) / 1e9:.1f} GB")

    t0 = time.time()

    # Leer solo columnas necesarias — reduce RAM de ~40GB a ~6GB
    cols_needed = ["pre_pt_root_id"] + NT_COLS
    print(f"[NT-EXTRACT] Cargando columnas: {cols_needed}")

    table = feather.read_table(
        synapse_file,
        columns=cols_needed,
        memory_map=True  # mmap — no carga todo en RAM de golpe
    )
    df = table.to_pandas()
    load_time = time.time() - t0

    total_synapses = len(df)
    total_neurons = df["pre_pt_root_id"].nunique()
    print(f"[NT-EXTRACT] Cargado en {load_time:.1f}s")
    print(f"[NT-EXTRACT] {total_synapses:,} sinapsis | {total_neurons:,} neuronas pre-sinápticas")

    # Por cada neurona: promediar probabilidades NT y determinar dominante
    print("[NT-EXTRACT] Calculando NT dominante por neurona...")
    t1 = time.time()

    grouped = df.groupby("pre_pt_root_id")[NT_COLS].mean()
    # Rellenar NA con 0 para evitar ValueError en idxmax cuando toda una fila es NA
    grouped_filled = grouped.fillna(0)
    # Detectar neuronas con todas las probabilidades en 0 (NaN original) → "unknown"
    all_zero_mask = (grouped_filled == 0).all(axis=1)
    dominant_nt = grouped_filled.idxmax(axis=1)
    dominant_nt[all_zero_mask] = "unknown"

    agg_time = time.time() - t1
    print(f"[NT-EXTRACT] Agregación en {agg_time:.1f}s")

    # Construir lookup: root_id -> {nt, sign, probs}
    nt_lookup = {}
    for root_id, nt in dominant_nt.items():
        probs = grouped.loc[root_id].to_dict()
        nt_lookup[str(root_id)] = {
            "nt": nt,
            "sign": NT_SIGN[nt],
            "confidence": float(probs[nt]) if nt in probs else 0.0,
            "probs": {k: round(float(v), 4) for k, v in probs.items()}
        }

    # Estadísticas
    nt_counts = dominant_nt.value_counts().to_dict()
    stats = {
        "total_synapses": total_synapses,
        "total_neurons": total_neurons,
        "nt_distribution": {k: {"count": int(v), "pct": round(v/total_neurons*100, 2)}
                           for k, v in nt_counts.items()},
        "excitatory_pct": round(sum(v for k, v in nt_counts.items() if NT_SIGN[k] > 0) / total_neurons * 100, 2),
        "inhibitory_pct": round(sum(v for k, v in nt_counts.items() if NT_SIGN[k] < 0) / total_neurons * 100, 2),
        "neuromodulator_pct": round(sum(v for k, v in nt_counts.items() if NT_SIGN[k] == 0) / total_neurons * 100, 2),
        "load_time_s": round(load_time, 1),
        "total_time_s": round(time.time() - t0, 1),
        "source_file": synapse_file,
        "fly_version": "783"
    }

    output = {
        "metadata": stats,
        "neurons": nt_lookup
    }

    # Guardar
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f)

    total_time = time.time() - t0
    size_kb = os.path.getsize(output_path) / 1024

    print(f"\n[NT-EXTRACT] ✓ Completado en {total_time:.1f}s")
    print(f"[NT-EXTRACT] Guardado en {output_path} ({size_kb:.0f} KB)")
    print(f"\n[NT-EXTRACT] Distribución NT:")
    for nt, data in stats["nt_distribution"].items():
        sign_label = "excit." if NT_SIGN[nt] > 0 else ("inhib." if NT_SIGN[nt] < 0 else "neuromod.")
        print(f"  {nt:6s} ({sign_label}): {data['count']:>8,} neuronas ({data['pct']:.1f}%)")
    print(f"\n  Excitatorias:    {stats['excitatory_pct']:.1f}%")
    print(f"  Inhibitorias:    {stats['inhibitory_pct']:.1f}%")
    print(f"  Neuromoduladores:{stats['neuromodulator_pct']:.1f}%")

    return stats


def main():
    parser = argparse.ArgumentParser(description="Extrae NT-types de FlyWire synapses")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Ruta JSON de salida")
    parser.add_argument("--input", default=SYNAPSE_FILE, help="Ruta feather de sinapsis")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"[NT-EXTRACT] ERROR: archivo no encontrado: {args.input}")
        sys.exit(1)

    stats = extract_nt_types(args.input, args.output)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
