# 🔫 Proyecto SEAL — Detección de Disparos para Investigación Policial (LOCAL)
**Director:** JARVIS · **Equipo SEAL** · Estado: investigación + organización LISTAS → listo para POC

Software integrado, **100% local/offline**, para: detectar disparos (audio) + opcional arma visible (visión),
localizar el origen (TDOA), y producir **evidencia forense admisible** para investigación policial.

> 📍 Carpeta CANÓNICA: `tools/gunshot_research/` (se unificó un duplicado previo de la raíz).

---
## 📂 Estructura (ordenada y entendible)
```
tools/gunshot_research/
├── README.md                         ← este mapa (empieza aquí)
├── VEREDICTO_CONSOLIDADO_JARVIS.md   ← ⭐ veredicto + arquitectura (léelo 2º)
├── 00_DATASETS_CATALOG.md            ← catálogo de datasets (ALICE)
├── ALICE_license_cost_analysis.md    ← licencias + costo + viabilidad (ALICE)
├── FABLE_ml_rigor_analysis.md        ← rigor del modelo + visión (FABLE)
├── analysis/
│   ├── NEXUS_forensic_security_analysis.md  ← requisitos forenses / cadena de custodia
│   └── NEXUS_audio_datasets.md
├── repos/   (REFERENCIAS — se extrae metodología, NO se forkean)
│   ├── audio/  01_gunshot_dashboard · 02_audio_anomaly · 03_gunshot_audio_tf (hasnainnaeem baseline)
│   │           · 06_rcarioni_methodology ⭐(100% precisión) · 07_gabemagee_edge (pipeline Pi)
│   └── visual/ 04_weapon_yolov8_flask · 05_weapon_yolov11 · 06_yolov8_becaye
└── datasets/
    ├── download_datasets.sh          ← baja los datasets ABIERTOS (NEXUS, disk-aware)
    ├── audio/   visual/              ← se llenan al correr el script
```

---
## 🎯 Veredicto (3 líneas)
1. Ningún repo es "el mejor" para forkear — demos académicos, casi ninguno con licencia, NINGUNO forense.
2. Camino = **clean-room**: metodología rcarioni (zero-FP) + datos legales (CADRE/NIJ, FSD50K) + modelo moderno + **capa forense propia**.
3. La detección es el motor; la **cadena de custodia** = lo que lo hace sistema policial real (el diferenciador).

## ⚖️ Legal
Datos del PRODUCTO: solo licencia-OK (CADRE/NIJ gov, FSD50K, AudioSet labels). NC (UrbanSound8K/ESC-50/MIVIA) solo prototipo. → ALICE_license_cost_analysis.md.

## 🏗️ Arquitectura
`mic-array + CCTV (offline)` → `audio zero-FP + visión` → `localización TDOA` → `FORENSE (timestamp NTP/GPS + SHA-256 + firma + log WORM + cifrado)` → `UI investigación`. → VEREDICTO_CONSOLIDADO_JARVIS.md.

## ▶️ ESTADO REAL (24-jun-2026 — POC ENTRENADO + PIPELINE INTEGRADO)
✅ Datos legales bajados (7.5GB): SESA (anti-FP) + Zenodo edge-gunshot (2148 reales) + STARSS22.
✅ **Detector v2 entrenado** (`poc/gunshot_train_v2.py` → `poc/artifacts/gunshot_detector_v2.joblib`):
   SESA test prec/rec 0.90, FP=2; Zenodo held-out recall ~100%.
   ⚠️ HONESTO: ese 100% es in-domain (Zenodo en train); generalización a un 3er dominio NO probada.
   Lección (rigor FABLE): v1 solo-SESA daba 90% pero 11.5% en disparos reales = overfit-al-dataset.
✅ **Localización** (`poc/gunshot_localization_poc.py`): GCC-PHAT/TDOA, DoA 2-mic + 2D 4-mic.
   ⚠️ funciona con array CALIBRADO; sin calibrar el ángulo es ILUSTRATIVO (se inscribe con método+conf).
✅ **Cadena de custodia** (`forensic/chain_of_custody.py`): SHA-256 + log encadenado anti-tamper
   (detecta manipulación de evidencia Y de log) + manifiesto admisible.
✅ **Pipeline integrado** (`poc/detect_pipeline.py`): audio → detección → localización → custodia,
   probado por efecto sobre disparo REAL de Zenodo (GUNSHOT 0.982, custodia ADMISIBLE=True).

### Siguiente para PRODUCCIÓN
1. 3er dataset forense (CADRE/NIJ) held-out total → probar generalización real (leave-one-dataset-out).
2. Bajar FP a 0 (umbral + más negativos difíciles).
3. Array de micrófonos CALIBRADO para localización defensible (o DoA neural tipo SELDnet).
4. Sello de tiempo NTP/GPS + RFC-3161 para la custodia en despliegue real.

venv: `tools/gunshot_research/.venv_poc` (librosa+sklearn+soundfile, CPU).

## 👥 Equipo
JARVIS (director + audio/deploy + consolidación) · FABLE (rigor + visión) · ALICE (licencia/costo) · NEXUS (forense/seguridad/deploy + script datasets).
