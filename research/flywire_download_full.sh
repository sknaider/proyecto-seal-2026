#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
#  FlyWire + Simulaciones Drosophila — Descarga Completa
#  Objetivo: cerebro completo + todos los repos para adaptar a SOUL
#  DGX Spark — ejecutar en background: bash flywire_download_full.sh
# ═══════════════════════════════════════════════════════════════════

SPARK_IA="$HOME/IA"
FLYWIRE_DIR="$SPARK_IA/datasets/flywire"
REPOS_DIR="$SPARK_IA/datasets/flywire/repos"
LOG_DIR="$FLYWIRE_DIR/logs"
VENV="$SPARK_IA/seal-spark/.venv"

mkdir -p "$FLYWIRE_DIR" "$REPOS_DIR" "$LOG_DIR"

echo "=== FlyWire Descarga Completa — $(date) ===" | tee "$LOG_DIR/download_master.log"
df -h "$SPARK_IA" | tail -1 | tee -a "$LOG_DIR/download_master.log"

ZENODO_BASE="https://zenodo.org/records/10676866/files"

# ── 1. CONNECTOME FLYWIRE v783 — 3 archivos ──────────────────────

echo "" | tee -a "$LOG_DIR/download_master.log"
echo "=== [1/4] Connectome FlyWire v783 (Zenodo 10676866) ===" | tee -a "$LOG_DIR/download_master.log"

# proofread_root_ids — 1.1MB (rápido, primero)
if [ ! -f "$FLYWIRE_DIR/proofread_root_ids_783.npy" ]; then
    echo "[DL] proofread_root_ids_783.npy (1.1MB)..."
    wget -q "$ZENODO_BASE/proofread_root_ids_783.npy" \
        -O "$FLYWIRE_DIR/proofread_root_ids_783.npy" \
        >> "$LOG_DIR/dl_root_ids.log" 2>&1
    echo "[OK] root_ids descargado" | tee -a "$LOG_DIR/download_master.log"
else
    echo "[SKIP] root_ids ya existe" | tee -a "$LOG_DIR/download_master.log"
fi

# proofread_connections — 852MB (conexiones neurona-neurona)
if [ ! -f "$FLYWIRE_DIR/proofread_connections_783.feather" ]; then
    echo "[BG] proofread_connections_783.feather (~852MB)..."
    wget -q --show-progress \
        "$ZENODO_BASE/proofread_connections_783.feather" \
        -O "$FLYWIRE_DIR/proofread_connections_783.feather" \
        > "$LOG_DIR/dl_connections.log" 2>&1 &
    echo "[BG:$!] connections → $LOG_DIR/dl_connections.log" | tee -a "$LOG_DIR/download_master.log"
else
    echo "[SKIP] connections ya existe" | tee -a "$LOG_DIR/download_master.log"
fi

# flywire_synapses — 9.5GB (todas las sinapsis con neurotransmisores)
if [ ! -f "$FLYWIRE_DIR/flywire_synapses_783.feather" ]; then
    echo "[BG] flywire_synapses_783.feather (~9.5GB) — puede tardar ~30-60min..."
    wget -q --show-progress \
        "$ZENODO_BASE/flywire_synapses_783.feather" \
        -O "$FLYWIRE_DIR/flywire_synapses_783.feather" \
        > "$LOG_DIR/dl_synapses.log" 2>&1 &
    echo "[BG:$!] synapses → $LOG_DIR/dl_synapses.log" | tee -a "$LOG_DIR/download_master.log"
else
    echo "[SKIP] synapses ya existe" | tee -a "$LOG_DIR/download_master.log"
fi

# Anotaciones celulares (flywire_annotations) — TSV con tipos celulares
if [ ! -d "$FLYWIRE_DIR/flywire_annotations" ]; then
    echo "[GIT] Clonando flywire_annotations..."
    git clone --depth 1 \
        https://github.com/flyconnectome/flywire_annotations.git \
        "$FLYWIRE_DIR/flywire_annotations" \
        >> "$LOG_DIR/git_annotations.log" 2>&1 &
    echo "[BG:$!] flywire_annotations" | tee -a "$LOG_DIR/download_master.log"
fi

# ── 2. EON SYSTEMS — Cerebro completo multi-backend ──────────────

echo "" | tee -a "$LOG_DIR/download_master.log"
echo "=== [2/4] Eon Systems — fly-brain (cerebro + cuerpo integrado) ===" | tee -a "$LOG_DIR/download_master.log"

if [ ! -d "$REPOS_DIR/fly-brain" ]; then
    echo "[GIT] Clonando eonsystemspbc/fly-brain..."
    git clone --depth 1 \
        https://github.com/eonsystemspbc/fly-brain.git \
        "$REPOS_DIR/fly-brain" \
        >> "$LOG_DIR/git_fly_brain.log" 2>&1 &
    echo "[BG:$!] fly-brain (Eon Systems) → $LOG_DIR/git_fly_brain.log" | tee -a "$LOG_DIR/download_master.log"
else
    echo "[SKIP] fly-brain ya existe" | tee -a "$LOG_DIR/download_master.log"
fi

# ── 3. SHIU ET AL. 2024 — Modelo LIF de Nature ───────────────────

echo "" | tee -a "$LOG_DIR/download_master.log"
echo "=== [3/4] Shiu et al. 2024 — Drosophila Brain Model (Nature) ===" | tee -a "$LOG_DIR/download_master.log"

if [ ! -d "$REPOS_DIR/Drosophila_brain_model" ]; then
    echo "[GIT] Clonando philshiu/Drosophila_brain_model..."
    git clone --depth 1 \
        https://github.com/philshiu/Drosophila_brain_model.git \
        "$REPOS_DIR/Drosophila_brain_model" \
        >> "$LOG_DIR/git_brain_model.log" 2>&1 &
    echo "[BG:$!] Drosophila_brain_model" | tee -a "$LOG_DIR/download_master.log"
else
    echo "[SKIP] Drosophila_brain_model ya existe" | tee -a "$LOG_DIR/download_master.log"
fi

# ── 4. FLYGYM — Cuerpo virtual NeuroMechFly v2 ───────────────────

echo "" | tee -a "$LOG_DIR/download_master.log"
echo "=== [4/4] FlyGym — NeuroMechFly v2 (cuerpo MuJoCo) ===" | tee -a "$LOG_DIR/download_master.log"

if [ ! -d "$REPOS_DIR/flygym" ]; then
    echo "[GIT] Clonando NeLy-EPFL/flygym..."
    git clone --depth 1 \
        https://github.com/NeLy-EPFL/flygym.git \
        "$REPOS_DIR/flygym" \
        >> "$LOG_DIR/git_flygym.log" 2>&1 &
    echo "[BG:$!] flygym (NeuroMechFly v2)" | tee -a "$LOG_DIR/download_master.log"
else
    echo "[SKIP] flygym ya existe" | tee -a "$LOG_DIR/download_master.log"
fi

# Repo de análisis de red (Murthy Lab — estadísticas + motifs)
if [ ! -d "$REPOS_DIR/flywire-network-analysis" ]; then
    git clone --depth 1 \
        https://github.com/murthylab/flywire-network-analysis.git \
        "$REPOS_DIR/flywire-network-analysis" \
        >> "$LOG_DIR/git_network.log" 2>&1 &
    echo "[BG:$!] flywire-network-analysis" | tee -a "$LOG_DIR/download_master.log"
fi

# ── 5. INSTALAR DEPENDENCIAS Python ──────────────────────────────

echo "" | tee -a "$LOG_DIR/download_master.log"
echo "=== Instalando dependencias Python ===" | tee -a "$LOG_DIR/download_master.log"

"$VENV/bin/pip" install --quiet \
    brian2 navis fafbseg caveclient \
    scipy igraph matplotlib pyarrow fastparquet \
    tqdm networkx seaborn \
    >> "$LOG_DIR/pip_install.log" 2>&1

# flygym + mujoco (puede fallar en arm64 — no crítico)
"$VENV/bin/pip" install --quiet flygym >> "$LOG_DIR/pip_flygym.log" 2>&1 \
    && echo "[OK] flygym instalado" | tee -a "$LOG_DIR/download_master.log" \
    || echo "[WARN] flygym no instalado (no crítico)" | tee -a "$LOG_DIR/download_master.log"

echo "[OK] Dependencias Python instaladas" | tee -a "$LOG_DIR/download_master.log"

# ── 6. MONITOR de descargas ───────────────────────────────────────

echo "" | tee -a "$LOG_DIR/download_master.log"
echo "=== Procesos en background ===" | tee -a "$LOG_DIR/download_master.log"
jobs -l | tee -a "$LOG_DIR/download_master.log"

echo "" | tee -a "$LOG_DIR/download_master.log"
echo "=== Cómo monitorear progreso ===" | tee -a "$LOG_DIR/download_master.log"
echo "  watch -n 5 'ls -lh ~/IA/datasets/flywire/*.feather 2>/dev/null; du -sh ~/IA/datasets/flywire/repos/'" | tee -a "$LOG_DIR/download_master.log"
echo "" | tee -a "$LOG_DIR/download_master.log"
echo "=== Cuando todo descargue, ejecutar: ===" | tee -a "$LOG_DIR/download_master.log"
echo "  $VENV/bin/python3 ~/IA/proyecto-seal/research/flywire_lif_experiment.py" | tee -a "$LOG_DIR/download_master.log"
echo "" | tee -a "$LOG_DIR/download_master.log"
echo "Setup completo — descargas corriendo en background — $(date)" | tee -a "$LOG_DIR/download_master.log"
