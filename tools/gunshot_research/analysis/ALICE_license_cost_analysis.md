# ALICE — Dimensión Licencia + Costo + Datasets (análisis de viabilidad legal/económica)

Proyecto: software integrado de detección de disparos para investigación policial LOCAL (on-premise).
Fecha: 2026-06-24. Para consolidación de JARVIS.

## 1. Licencias de los repos del shortlist (verificadas vía GitHub API)

| Repo | Licencia | Último push | Stars | ¿Usable en producto? |
|------|----------|-------------|------:|----------------------|
| gabemagee/gunshot_detection | **NINGUNA** | 2024-07 | 65 | ❌ No (all rights reserved) |
| hasnainnaeem/Gunshot-Detection-in-Audio | **NINGUNA** | 2020-02 | 22 | ❌ No |
| rcarioni/gunshot-detection | **NINGUNA** | 2024-01 | 0 | ❌ No (¡el mejor por rigor, pero legalmente inusable como está!) |
| mariamkhmahran/gunshot-detection-system | **NINGUNA** | 2023-07 | 14 | ❌ No |
| tusharsingh62/Gunshot-sound-classification | **NINGUNA** | 2020-01 | 3 | ❌ No |
| sagaryash001/AlertLens-Gunshot-Detection | **MIT** | 2025-07 | 0 | ✅ Sí (comercial/gov OK) |
| tensorflow/models (YAMNet) | **Apache-2.0** (repo NOASSERTION por mezcla; YAMNet es Apache-2.0) | 2026-06 | 77k | ✅ Sí |

**Principio legal:** un repo SIN archivo de licencia = "todos los derechos reservados" por copyright. No se puede usar, copiar, modificar ni construir un producto sin permiso explícito del autor. Showstopper para software policial/gubernamental.

**Consecuencia clave para la consolidación:** el "mejor técnicamente" (rcarioni, 99% acc / 100% precisión) y el "mejor para construir legalmente" (YAMNet + AlertLens) NO son el mismo repo. Las TÉCNICAS (denoising, augmentation, arquitectura) no son copyrightables — solo el código — así que la metodología de rcarioni se puede RE-IMPLEMENTAR limpiamente.

## 2. Datasets — licencia y uso

| Dataset | Licencia | ¿Producto? | Nota |
|---------|----------|-----------|------|
| UrbanSound8K | CC BY-NC 4.0 | ❌ solo research | clase gun_shot; el más usado pero No-Comercial |
| ESC-50 | CC BY-NC | ❌ solo research | clase gun_shot |
| MIVIA audio events | académica (pedir acceso) | ❌ | |
| FSD50K | CC0 / CC-BY (por clip) | ✅ | respetar atribución por clip |
| AudioSet | labels CC BY 4.0; audio = IDs YouTube | ⚠️ gris | audio se descarga propio; redistribución restringida |
| Gunshot Audio Forensics Dataset (NIJ/NIST) | gobierno EEUU | ✅ probable | armas reales, forense; verificar términos exactos |
| Multi-Firearm Multi-Orientation (2023) | research | ⚠️ verificar | real, multi-orientación → localización |
| Librerías royalty-free / Kaggle | varía (algunas CC0) | ⚠️ por-dataset | |

## 3. Estrategia recomendada
1. **Prototipo/benchmark:** entrenar con TODO (incl. NC) para medir el techo de precisión.
2. **Producto desplegable:** re-entrenar el modelo final SOLO con datos de licencia limpia (FSD50K + forenses NIJ + librerías royalty-free) + **grabaciones propias de campo** en el entorno real de despliegue. Esto resuelve a la vez: (a) licencia limpia, (b) domain-mismatch (el mayor salto demo→producto).

## 3b. Búsqueda profunda — datasets profesionales/forenses (no en superficie)

| Dataset | Fuente | Qué aporta | Licencia |
|---------|--------|-----------|----------|
| Gunshot Audio Forensics Dataset | CADRE Forensics / NIJ Grant 2016-DN-BX-0183 (US DOJ) | ~10,000 disparos REALES (Arizona 2017), grado forense | gobierno EEUU → probablemente permisivo (confirmar en cadreforensics.com/audio) |
| Gunshot/Gunfire Audio Dataset | Zenodo record 7004819 | multi-arma/orientación con dispositivos EDGE, timestamps + marca/modelo + ubicación de mics → detección+LOCALIZACIÓN | Zenodo (casi siempre CC-BY/CC0; confirmar en el record) |
| SESA (Sound Events for Surveillance Applications) | académico | gunshot/explosión/sirena + clase "casual" anti-falso-positivo | verificar |
| gunshot-audio-dataset (emrahaydemr) | Kaggle | conjunto adicional | verificar por-dataset |

Repositorios a minar (donde viven los profesionales, no GitHub): **IEEE DataPort** (keyword gunshot-audio), **Zenodo**, **HuggingFace Datasets**, **Kaggle**.

**Combo profesional Y shipeable:** NIJ/CADRE (forense real) + Zenodo edge/multi-orientación (localización) + SESA (anti-falso-positivo) + FSD50K (negativos limpios).

## 4. Costo de despliegue (arquitectura JARVIS: CNN audio edge, 8kHz, MFCC, modelo liviano)

### Por nodo de detección (USD aprox, hardware)
| Variante | Componentes | Total/nodo |
|----------|-------------|-----------:|
| Económico (solo detección) | Raspberry Pi 4/5 (~$70) + 1 mic USB/MEMS (~$20) + SD evidencia (~$15) + caja/PoE (~$25) | **~$130** |
| Con localización (TDOA) | igual pero array 4-mics sincronizado (ReSpeaker ~$50) en vez del mic simple | **~$170** |
| Ultra-barato (TinyML ESP32, estilo AlertLens) | ESP32 + I2S mic | **~$30–50** (menor precisión, sin localización fina) |

### Costos de una sola vez (no por nodo)
- **Entrenamiento:** gratis en nuestras GPUs (RTX 5090 / DGX); cloud ~$10–50.
- **Servidor central** (agregación + DB evidencia + cadena de custodia + dashboard): $0 si hardware existente, o NUC ~$400.
- **Datasets:** gratis (FSD50K, CADRE/NIJ). Grabación de campo = tiempo propio.

### Escenarios
- Piloto (1 edificio, 4 nodos): ~$520–680 hardware + central existente.
- Zona (20 nodos con localización): ~$3,400 + switch PoE + central.

### Vs comercial
ShotSpotter ≈ **$65k–95k por milla²/año** (suscripción). El edge local para área acotada = pago único de cientos a pocos miles + tiempo propio. Trade-off: mantenimiento/curado nuestro vs servicio llave-en-mano.

**Conclusión costo:** muy viable a escala local/acotada. El gasto real NO es el hardware (barato) — es el TIEMPO de curar falsos positivos con datos de campo reales.
