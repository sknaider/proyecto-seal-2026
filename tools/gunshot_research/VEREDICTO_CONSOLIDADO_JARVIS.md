# Veredicto Consolidado — Software de Detección de Disparos para Investigación Policial (LOCAL)
**Coordinador:** JARVIS (arquitecto) · **Fecha:** 2026-06-24 · Consolida las 4 dimensiones del equipo SEAL
**Para:** William — "quiero el mejor, soft integrado para investigación policial, local"

## TL;DR (la verdad honesta)
**No hay UN repo "el mejor" para forkear.** Los repos de GitHub son DETECTORES académicos (demos), NINGUNO es un sistema forense policial, y casi ninguno es legalmente usable. El camino correcto = **build CLEAN-ROOM**: extraer la METODOLOGÍA de las mejores referencias + entrenar sobre datos bien-licenciados + construir la CAPA FORENSE nosotros (el verdadero diferenciador y el verdadero trabajo).

## Hallazgos por dimensión
- **Modelo/datos (JARVIS):** hasnainnaeem = mejor baseline (CNN TF2.0, 97.5%, curó datos AudioSet/MIVIA + pesos). gabemagee = mejor pipeline end-to-end edge (captura→denoise→mel→CNN→alerta→Pi) pero deps 2019.
- **Rigor (FABLE):** rcarioni = mejor METODOLOGÍA — **100% precisión** (cero falsos positivos = lo que pide la policía); pipeline reusable (Butterworth + spectral gating + augmentation + MFCC deltas + Bayesian hyperopt). PERO dominio ambiental (anti-caza), sin localización, reference-only → se EXTRAE la metodología.
- **Visión (FABLE):** el open-source de VISIÓN está más vivo (YOLOv8/v11, 26K+ imgs/17 clases de arma). Visión = arma VISIBLE (CCTV, proactivo ANTES del disparo, necesita línea de vista). Audio = el DISPARO (aunque no se vea el arma). → **Multimodal complementario.**
- **Licencia (ALICE):** 🚨 casi todos SIN LICENCIA = todos-los-derechos-reservados = NO se puede shipear producto encima. Único limpio: **sagaryash001/AlertLens (MIT, 2025)** pero 0★/no probado. rcarioni (el mejor) = sin licencia → solo extraer metodología.
- **Forense/seguridad (NEXUS):** 🚨 NINGÚN repo trae cadena de custodia / integridad de evidencia (0 hash/firma/audit-log en todos) → INADMISIBLE como evidencia policial. La capa forense se CONSTRUYE. Flags: repos 01/04 usan Twilio = NUBE (violan "local"); construir 100% offline.

## "El mejor" (respuesta matizada)
- Mejor **metodología** a extraer: **rcarioni** (zero-FP).
- Mejor **pipeline edge**: **gabemagee** (modernizado).
- Mejor **baseline modelo+datos**: **hasnainnaeem**.
- Única **base legalmente shippable**: **AlertLens (MIT)** — pero hay que validarla.
- Complemento **visión**: YOLOv8/v11 weapon-detection.

## Arquitectura propuesta — Software Integrado LOCAL
1. **Ingesta** (100% local/offline): array de micrófonos (audio) + CCTV opcional (visión).
2. **Detección audio:** modelo moderno (PyTorch / transformer de audio SOTA), entrenado con la metodología zero-FP de rcarioni (denoise + augment), sobre datos USABLES (FSD50K CC0, AudioSet-gunshot, **Gunshot Audio Forensics Dataset NIJ/NIST = gobierno EEUU, ideal**).
3. **Detección visión (opcional, multimodal):** YOLO weapon-detection para confirmación + alerta proactiva.
4. **Localización:** array de mics + TDOA (datos multi-firearm/multi-orientation + Enemy-Spotted).
5. **CAPA FORENSE (el diferenciador, NEXUS):** timestamp confiable (NTP/GPS), SHA-256 + firma por clip al capturar, log append-only encadenado (WORM), originales read-only, cifrado en reposo, control de acceso, 100% offline.
6. **UI de investigación:** gestión de evidencia, línea de tiempo, exportación admisible.

## Datos (legal)
- **En el PRODUCTO** (licencia OK): FSD50K (CC0/CC-BY), AudioSet labels (CC-BY, audio = YouTube IDs), Gunshot Audio Forensics Dataset (NIJ/NIST gov), multi-firearm/multi-orientation (verificar).
- **Solo PROTOTIPO** (no-comercial): UrbanSound8K, ESC-50, MIVIA.
- Negativos (anti-falso-positivo): ambiente urbano + petardos + portazos.

## Recomendación final
Construir clean-room: pipeline rcarioni (metodología) + datos legales + modelo moderno en nuestro cluster + capa forense NEXUS + (opcional) visión YOLO. NO forkear código sin licencia. Validar AlertLens (MIT) como posible base shippable.

---
## Actualización 2026-06-24 (validación por efecto — honestidad de alcance)
**DETECCIÓN ("¿hubo disparo?")** = viable YA. Clasificador POC construido (poc/gunshot_classifier_poc.py, metodología rcarioni zero-FP sobre SESA real). Capa forense construida y verificada (NEXUS). Datos reales+legales: SESA, Zenodo 7004819 (CC-BY), STARSS22 (MIT).

**LOCALIZACIÓN ("¿dónde?")** = FASE 2, NO prometer como lista. FABLE validó por efecto su GCC-PHAT free-field: pasó el test sintético (<1°) pero en STARSS22 REAL dio mediana 100° de error (falla en reverberación real). Conclusión: la localización necesita **DoA NEURAL (tipo SELDnet/DCASE)** entrenado sobre STARSS22 — es un sub-proyecto ML más profundo, no GCC-PHAT simple. Separada como Fase 2 por honestidad de alcance.

**Lección de equipo:** se aplicó "duda de tu propio verde" hasta al propio trabajo (FABLE cazó que su sintético mentía) → descubrir el límite ANTES de prometérselo a la policía = lo que hace esto profesional.
