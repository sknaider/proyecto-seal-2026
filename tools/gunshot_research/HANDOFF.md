# HANDOFF — Soft de detección de disparos (investigación policial, LOCAL)
### Para: otro agente que continúa el trabajo en el taller de William · De: JARVIS (Team SEAL)

> Este paquete contiene TODO lo necesario para CONTINUAR el proyecto sin contexto previo. Léelo completo antes de tocar nada. Doctrina: verificar POR EFECTO, no por superficie; reportar honesto (el número real, no el inflado).

## 1. Qué es
Software **100% local/offline** para investigación policial: detectar disparos (audio) → localizar el origen (TDOA) → producir evidencia forense admisible (cadena de custodia). NO depende de la nube.

## 2. Estado actual (POC COMPLETO y honesto)
| Pieza | Estado | Archivo |
|---|---|---|
| Detector de disparos v2 | ✅ entrenado | `poc/gunshot_train_v2.py` → `poc/artifacts/gunshot_detector_v2.joblib` |
| Localización (GCC-PHAT/TDOA) | ✅ POC | `poc/gunshot_localization_poc.py` |
| Cadena de custodia forense | ✅ (hash-chain anti-tamper) | `forensic/chain_of_custody.py` |
| Pipeline INTEGRADO (detección→localización→custodia) | ✅ probado por efecto | `poc/detect_pipeline.py` |

**Números REALES (honestos, NO inflar):**
- Detector SESA test: precisión 0.90, FP=2. In-domain (Zenodo held-out): ~100%.
- **CROSS-DATASET REAL (train SESA / test Zenodo): 11.5%** ← ESTE es el número de generalización honesto. El 100% es in-domain (Zenodo está en el train de v2). NO presentar el 100% como generalización.
- Pipeline integrado: corrido sobre disparo real de Zenodo → detección 0.982, custodia ADMISIBLE=True.

## 3. Qué FALTA (para PRODUCCIÓN — el trabajo a continuar)
1. **Generalización real:** entrenar con un 3er dataset forense (CADRE/NIJ, EE.UU.) held-out total → leave-one-dataset-out. ESE recall dice si generaliza de verdad. Hoy NO está probado fuera de SESA+Zenodo.
2. **Bajar falsos-positivos a 0** (en uso policial un FP cuesta una investigación). Umbral + más negativos difíciles (explosión, petardos).
3. **Localización defensible:** GCC-PHAT funciona con array CALIBRADO y poca reverb; FABLE halló que **falla en reverb real** → migrar a DoA neural (tipo SELDnet) + array de micros con geometría calibrada.
4. **Sello de tiempo NTP/GPS + RFC-3161** para la custodia en despliegue real.

## 4. Cómo correr el POC
```
# 1. Datasets (NO vienen en el zip, 9.1GB): bajarlos
bash datasets/download_datasets.sh        # baja SESA + Zenodo + STARSS22 (licencia-OK)
# 2. Entorno (no viene en el zip, reproducible):
python3 -m venv .venv_poc && .venv_poc/bin/pip install librosa soundfile scikit-learn numpy
# 3. Entrenar el detector (cross-dataset honesto):
.venv_poc/bin/python poc/gunshot_train_v2.py
# 4. Pipeline forense integrado sobre un audio:
.venv_poc/bin/python poc/detect_pipeline.py <audio.wav> [--case CASO-001]
```

## 5. Datos (legales — solo licencia-OK)
- SESA (4 clases, anti-FP), Zenodo edge-gunshot (2148 reales por calibre), STARSS22 (localización). Bajar con `datasets/download_datasets.sh`. Catálogo: `datasets/DATASETS.md`. **Solo usar licencia-OK** para el producto (no UrbanSound8K/ESC-50 NC).

## 6. Documentos clave (leer en orden)
1. `README.md` — mapa + estado.
2. `VEREDICTO_CONSOLIDADO_JARVIS.md` — veredicto + arquitectura.
3. `analysis/` — análisis por dimensión (forense/seguridad NEXUS, rigor ML/visión FABLE, licencia/costo ALICE).

## 7. Estructura del paquete
```
poc/            ← entrenamiento + localización + pipeline integrado + artifacts/ (modelos .joblib)
forensic/       ← cadena de custodia (hash-chain)
analysis/       ← análisis por dimensión
datasets/       ← download_datasets.sh + DATASETS.md (los datos crudos se bajan, no vienen)
README.md, VEREDICTO_CONSOLIDADO_JARVIS.md, HANDOFF.md (este)
```
*(Excluidos del zip por tamaño/reproducibilidad: datasets/audio+visual crudos 9.1GB, .venv_poc 504MB, repos/ de referencia. Se re-obtienen con los scripts/instrucciones de arriba.)*

— Handoff por JARVIS, Team SEAL. Continúa con honestidad: el número que importa es el cross-dataset, no el in-domain.
