# NEXUS — Datasets de AUDIO de disparos (mi slice)
Para integrar y potenciar el motor de detección. Marcados por licencia y uso-en-producto.
Fecha 2026-06-24. Licencia final la confirma ALICE; aquí el mapa técnico audio.

Leyenda uso: ✅ apto producto (policial/gov) · ⚠️ solo prototipo/research · 🔶 caso a caso

| # | Dataset | Contenido disparos | Licencia (aprox.) | Uso | Link |
|---|---|---|---|---|---|
| 1 | **Gunshot Audio Forensics Dataset (NIJ/CADRE)** | ~10,000 disparos, 18 armas | Público, fondos NIJ (Dept. Justicia EE.UU.) | ✅ el más defendible (forense, origen gov) | researchgate / NIJ |
| 2 | **Multi-Firearm, Multi-Orientation Gunshot Audio** (Scientific Data 2023) | multi-arma, multi-orientación | Open (Scientific Data, normalmente CC BY) | ✅ probable apto | PMC10114508 |
| 3 | **FSD50K** (Freesound) | clase gunshot_and_gunfire | CC0 + CC-BY por clip (mixto) | 🔶 filtrar a CC0/CC-BY | zenodo FSD50K |
| 4 | **AudioSet** (Google) | gunshot, machine gun, artillery, fusillade | Labels CC BY; AUDIO = YouTube (no redistribuye) | ⚠️ bajar uno mismo; turbio para producto | research.google/audioset |
| 5 | **MIVIA Audio Events** | gunshot + glass + scream (6000 ev.) | Research, por solicitud | ⚠️ research | MIVIA lab |
| 6 | **UrbanSound8K** | clase gun_shot (374 clips) | CC BY-NC 4.0 (NO comercial) | ⚠️ solo prototipo | urbansounddataset |
| 7 | **ESC-50** | clase gun_shot (40 clips) | CC BY-NC | ⚠️ solo prototipo | github karolpiczak/ESC-50 |
| 8 | **Free Firearm Sound Effects Library** | FX de armas | Varía (a menudo royalty-free) | 🔶 revisar términos | online SFX libs |
| 9 | **Gunshot/Gunfire Audio (Kaggle)** | sets varios de disparos | Términos Kaggle por dataset | 🔶 caso a caso | kaggle |

## Notas técnicas (para que el modelo sea POTENTE)
- **El reto #1 = falsos positivos**: petardos, globos, portazos, backfire suenan como disparo.
  → entrenar con NEGATIVOS difíciles a propósito (bag-pops, fuegos artificiales, cohetes). UrbanSound8K
  ayuda con clases urbanas; agregar set de globos/petardos baja el FP (confirmado en la literatura).
- **Combinación ganadora** (lo que ya hizo hasnainnaeem y recomienda la práctica): base urbana +
  inyectar disparos reales de AudioSet + MIVIA. Para producto, reemplazar las partes NC por
  NIJ-Forensics + Multi-Firearm (licencia limpia).
- **Para localización (TDOA)** la policía querrá multi-micrófono → el dataset Multi-Orientation #2
  es valioso porque varía la orientación del arma.
- **Procedencia**: guardar de qué dataset salió cada muestra (para defensibilidad del modelo).

## Búsqueda profunda — datasets de LOCALIZACIÓN/forenses (mi diferencial: la policía necesita DÓNDE)
| Dataset | Por qué importa | Acceso/licencia |
|---|---|---|
| **Zenodo 7004819 — Gunshot/Gunfire (multi-firearm, multi-orientation)** | TIME-SYNCHRONIZED multi-micrófono → permite Direction-of-Arrival (DoA/TDOA). Justo lo que falta para localizar. | Zenodo, abierto (CC), DOI 10.5281/zenodo.7004819 ✅ |
| **ShotSpotter Tech Note 098 (IEEE DataPort)** | Live-fire REAL en entorno URBANO (Pittsburgh PD, 2018) validando localización acústica. Datos de la referencia comercial. | IEEE DataPort (login gratis) 🔶 |
| **SESA — Sound Events for Surveillance (Zenodo 3519845)** | Clases gunshot/explosion/siren/casual; pensado para vigilancia. | Zenodo, de Freesound (CC) ✅ |
| **EU JRC MOBILEMIKES** | Disparos grabados con micrófonos de CELULAR (móvil/campo). | European Commission Data Catalogue 🔶 |
| **Cooper & Shaw** | 88 clips, 7 armas; identificación de micrófono (multi-sensor). | académico 🔶 |
| **Gunshot Recoil Dataset (IEEE DataPort)** | Acelerómetro de muñeca, 15 armas — sensor NO-audio (fusión multimodal). | IEEE DataPort 🔶 |
| Arrays generales (MYRiAD, LOCATA, RealMAN) | No son de disparos, pero sirven para entrenar/validar el motor de localización por array. | abiertos académicos ✅ |

### Localización (TDOA) — método para el módulo "DÓNDE"
- El disparo genera DOS ondas: muzzle blast (baja frec., circular) + shockwave (alta frec., cónica, si bala supersónica).
- Array de ≥3-6 micrófonos sincronizados → TDOA con **GCC-PHAT** → azimut/elevación/rango.
- Robustez: PHAT rinde mejor a bajo SNR; combinar con beamforming + CRNN para discriminar ruido (autos, bocinas, viento).

## Recomendación de mezcla (legal + potente)
- PRODUCTO: NIJ-Forensics (#1) + Multi-Firearm (#2) + FSD50K filtrado CC0/CC-BY (#3) + negativos
  difíciles de licencia limpia.
- PROTOTIPO/benchmark: añadir UrbanSound8K + ESC-50 + MIVIA (solo para medir, no para el modelo final).
