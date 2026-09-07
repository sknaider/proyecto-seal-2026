# FABLE — Dimensión Rigor ML + Visión + Localización
**Proyecto:** Software integrado de detección de disparos para investigación policial (local)
**Autor:** FABLE (profesor) · 2026-06-24 · análisis al 100% del código por efecto

---

## 1. Repo de rigor: rcarioni/gunshot-detection (analizado al 100%)
**Qué es:** tesis de grado sobre identificación de disparos en *monitoreo acústico pasivo*
(Elephant Listening Project — anti-caza-furtiva en selva).

**Resultados (su README):** 99% accuracy, **100% precisión**, 96% recall. El 100% de precisión
es exactamente lo que un sistema policial necesita (cero falsos positivos cuestan recursos/credibilidad).

**Pipeline transferible (código limpio en `methods_audio/`):**
- `denoising.py` — low-pass Butterworth (`butter`/`lfilter`, cutoff 1500 Hz) + spectral gating (`noisereduce`).
- `data_augmentation.py` — ruido gaussiano, time-reverse, **pitch-shift ±4 semitonos**, time-stretch (con probabilidad).
- Features: MFCC + **deltas y delta-deltas**.
- Tuning: **optimización Bayesiana** de hiperparámetros.

**Veredicto:** la MEJOR METODOLOGÍA (precisión + anti-falsos-positivos), pero **NO base desplegable**:
1. Dominio equivocado (ambiental/selva, no urbano/policial).
2. El README dice explícito: *"código de referencia, no para correr"* (data privada de ELP).
→ **Se extrae la metodología, no el sistema.** Clone en: `/tmp/gunshot_analysis/gunshot-detection` (para mover a `repos/`).

## 2. Visión (muzzle flash / arma)
El open-source de VISIÓN está mucho más vivo/reciente que el de audio (YOLOv8/v11, datasets grandes:
BecayeSoft, AbdulHadi806, JoaoAssalim, YOLO11 26K imágenes/17 clases). **PERO distinción clave:**
- Visión detecta el **ARMA VISIBLE** (CCTV) → proactivo, antes del disparo, requiere línea de vista.
- Audio detecta el **DISPARO** (evento) aunque no se vea el arma, y permite **localizar**.
- Muzzle-flash específico (firma visual del disparo) es FLACO en open-source.
→ Visión = **capa complementaria** (correlacionar CCTV), no el core forense.

## 3. HALLAZGO CRÍTICO — Localización (gap del open-source)
**Ningún repo de audio LOCALIZA — solo clasifican (sí/no disparo).** Para investigación policial,
saber **DÓNDE** fue el disparo (TDOA + array de micrófonos / Direction-of-Arrival) es esencial.
Datasets que llenan el gap (búsqueda profunda):
- **DCASE SELD** (Sound Event Localization & Detection) 2022/2024/2025 — array de micrófonos + anotación espacial. **STARSS22**: `zenodo.org/record/6600531`.
- **RealMAN** — array real anotado (localización).
- Dataset de distancia: disparos de rifle de 10 m a ~800 m (DoA).
- (NEXUS añadió Zenodo 7004819: gunshot multi-firearm time-synchronized multi-mic.)

## 4. RECOMENDACIÓN DE ENSAMBLE (no hay un repo único "el mejor")
| Capa | Fuente | Rol |
|------|--------|-----|
| CORE detección | pipeline de precisión de **rcarioni** re-entrenado en audio urbano | clasificar disparo con 0 falsos positivos |
| **Localización** | DCASE-SELD/STARSS22 + Zenodo 7004819 (TDOA/DoA) | **DÓNDE** fue el disparo (el diferenciador forense) |
| Datos forenses | **CADRE/NIJ** (cadreforensics.com/audio, ~10k disparos reales, descargable) + FSD50K (licencia-segura) | robustez real + base legal |
| Visión (opcional) | YOLOv8/v11 | correlacionar CCTV / detectar arma |
| Forense | logging inmutable + timestamp + audio-evidencia (lane NEXUS) | admisibilidad / cadena de custodia |

**Principio:** la policía necesita **DÓNDE + CUÁNDO + evidencia admisible**, no solo "¿hubo disparo?".
Ningún repo lo da llave-en-mano; el valor de SOUL es **ensamblarlo** y entrenarlo con datos reales (CADRE/NIJ)
mezclados con augmentation, validando por **precisión** (falsos positivos = el enemigo #1).
