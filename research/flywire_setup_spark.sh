#!/bin/bash
# ═══════════════════════════════════════════════════════════════
#  FlyWire Connectome Setup — DGX Spark
#  Simulación Drosophila melanogaster como base motivacional SEAL
#  Ejecutar en: dadito@spark (192.168.68.80 o .200)
# ═══════════════════════════════════════════════════════════════

set -e
SPARK_IA="$HOME/IA"
FLYWIRE_DIR="$SPARK_IA/datasets/flywire"
VENV="$SPARK_IA/seal-spark/.venv"

echo "=== FlyWire Setup — DGX Spark ==="
echo "IA dir: $SPARK_IA"
df -h "$SPARK_IA" | tail -1

# ── 1. Crear directorios ──────────────────────────────────────
mkdir -p "$FLYWIRE_DIR"
echo "[OK] Directorio: $FLYWIRE_DIR"

# ── 2. Instalar dependencias en venv existente ────────────────
echo ""
echo "=== Instalando dependencias ==="
"$VENV/bin/pip" install --quiet \
    brian2 \
    navis \
    fafbseg \
    caveclient \
    scipy \
    igraph \
    matplotlib \
    pyarrow \
    fastparquet \
    tqdm

# flygym requiere mujoco — instalar separado
"$VENV/bin/pip" install --quiet flygym || echo "[WARN] flygym falló — continuar sin cuerpo virtual"

echo "[OK] Dependencias instaladas"

# ── 3. Descargar connectome FlyWire desde Zenodo (~10.6GB) ────
# DOI: 10.5281/zenodo.10676866 — versión 783 (paper Nature 2024)
echo ""
echo "=== Descargando connectome FlyWire v783 ==="
echo "Tamaño total: ~10.6 GB — corriendo en background"

ZENODO_BASE="https://zenodo.org/records/10676866/files"

# Archivo principal de conectividad: 852MB — suficiente para empezar
if [ ! -f "$FLYWIRE_DIR/proofread_connections_783.feather" ]; then
    echo "Descargando proofread_connections_783.feather (~852MB)..."
    wget -q --show-progress \
        "$ZENODO_BASE/proofread_connections_783.feather" \
        -O "$FLYWIRE_DIR/proofread_connections_783.feather" &
    WGET_CONN=$!
    echo "[BG:$WGET_CONN] connections descargando..."
else
    echo "[SKIP] proofread_connections_783.feather ya existe"
fi

# IDs de neuronas verificadas: 1.1MB — descarga rápida
if [ ! -f "$FLYWIRE_DIR/proofread_root_ids_783.npy" ]; then
    echo "Descargando proofread_root_ids_783.npy (1.1MB)..."
    wget -q \
        "$ZENODO_BASE/proofread_root_ids_783.npy" \
        -O "$FLYWIRE_DIR/proofread_root_ids_783.npy"
    echo "[OK] root_ids descargado"
fi

# Sinapsis completas: 9.5GB — en background (puede tardar ~30min)
if [ ! -f "$FLYWIRE_DIR/flywire_synapses_783.feather" ]; then
    echo "Descargando flywire_synapses_783.feather (~9.5GB) [BACKGROUND]..."
    wget -q --show-progress \
        "$ZENODO_BASE/flywire_synapses_783.feather" \
        -O "$FLYWIRE_DIR/flywire_synapses_783.feather" \
        >> "$FLYWIRE_DIR/download_synapses.log" 2>&1 &
    echo "[BG:$!] synapses descargando → log: $FLYWIRE_DIR/download_synapses.log"
else
    echo "[SKIP] flywire_synapses_783.feather ya existe"
fi

echo ""
echo "=== Setup completo ==="
echo "Directorio: $FLYWIRE_DIR"
ls -lh "$FLYWIRE_DIR/"
echo ""
echo "Siguiente paso: ejecutar flywire_lif_experiment.py"
echo "cuando proofread_connections_783.feather termine de descargar."
