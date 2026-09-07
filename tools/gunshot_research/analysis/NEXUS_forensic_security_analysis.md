# NEXUS — Dimensión Seguridad / Forense / Despliegue Local
**Software integrado de investigación policial (detección de disparos/armas) — análisis por efecto**
Fecha: 2026-06-24 · Autor: NEXUS · Para consolidación: JARVIS

> Alcance de mi dimensión (reparto del equipo): seguridad, cadena de custodia / admisibilidad
> de evidencia, y seguridad del despliegue 100% local/offline. NO repito el análisis de
> rigor de modelo (FABLE/JARVIS) ni licencia/costo final (ALICE) — aporto el ángulo que hace
> que esto sea usable por POLICÍA, no solo un detector.

---

## 1. Hallazgo crítico (el que cambia todo)
**Ninguno de los repos del shortlist trae NADA de cadena de custodia / integridad de evidencia.**
Escaneé los clones por marcadores forenses (hash/sha256, firma, encrypt, audit log, tamper,
integrity, chain-of-custody): **0 coincidencias en todos**. Son detectores/demos, no sistemas
forenses. → La capa forense hay que CONSTRUIRLA en la integración; no se hereda de ningún repo.

Para uso policial esto NO es opcional: si la evidencia no es íntegra, fechada y no-manipulable,
**es inadmisible** en una investigación. La detección es el 30% del trabajo; el 70% es que el
clip detectado sirva como prueba.

## 2. Escaneo por repo (mi dimensión)
| Repo | Licencia | Offline (sin nube) | Pesos incluidos | Evidencia/forense | Veredicto seguridad |
|---|---|---|---|---|---|
| 01 gunshot-dashboard (Swasthikkp) | **NINGUNA** | ❌ usa Twilio (nube) en 4 archivos | .h5 referenciado (no en repo) | ❌ nada | Demo. Cloud-coupled. Sin licencia = no usable. |
| 02 audio-anomaly (Hossein-sfa) | **NINGUNA** | ✅ sin deps de nube | ✅ best_checkpoint.pth.tar (4.7MB) | ❌ nada | Offline OK + pesos, pero sin licencia ni evidencia. |
| 04 weapon-yolov8-flask (Nihal) | ✅ LICENSE | ❌ Twilio en backend/app.py | (descarga YOLOv8) | ❌ nada (tiene SQLite, no para evidencia) | El más "producto" (backend/frontend/db) pero acoplado a nube. |
| 05 weapon-yolov11 (Deeraj) | **NINGUNA** | ✅ sin deps de nube | ✅ best.pt (5.5MB, YOLOv11) | ❌ nada | Detector offline con pesos, pero pelado y sin licencia. |

(Repos de audio de FABLE/JARVIS — rcarioni, hasnainnaeem — los cubren ellos; mi nota de licencia:
hasnainnaeem históricamente sin licencia explícita → ALICE confirma.)

### Banderas de seguridad
- **Acoplamiento a nube (Twilio):** repos 01 y 04 mandan alertas por SMS vía nube → **viola el
  requisito LOCAL/offline** y filtra datos sensibles fuera del perímetro. Hay que arrancarlo de raíz
  y reemplazar por alertado local (LAN/sirena/webhook interno).
- **Sin licencia (01, 02, 05):** legalmente **no se pueden usar** para un producto gubernamental
  (sin licencia = todos los derechos reservados del autor). Solo 04 trae LICENSE. (Detalle legal → ALICE.)
- **Pesos sin procedencia:** best.pt / checkpoint vienen sin hash ni datasheet del modelo →
  para defensibilidad hay que versionar y documentar con qué se entrenó.

## 3. Requisitos FORENSES que el soft integrado DEBE tener (no negociable para policía)
1. **Timestamp confiable** en cada captura (NTP a un stratum local o GPS; nunca reloj suelto).
2. **Hash criptográfico (SHA-256)** de cada clip audio/video en el momento de captura, +
   opcional **firma** con clave del sistema. El hash va a un…
3. **Log append-only / inmutable (WORM)** — registro de detecciones y de TODO acceso/exportación
   (quién, qué, cuándo). Idealmente encadenado (hash del registro N incluye el N-1).
4. **Evidencia original read-only**, separada de las copias procesadas (nunca se sobrescribe el original).
5. **Cifrado en reposo** del almacén de evidencia + **control de acceso (RBAC)** con autenticación.
6. **Operación 100% offline / air-gapped**: cero tráfico saliente; soberanía del dato.
7. **Procedencia del modelo**: versión del modelo + score de confianza por detección guardados con
   la evidencia (para defensibilidad tipo Daubert: explicar cómo se detectó).
8. **(Alta utilidad) Localización TDOA** con array de micrófonos: la policía necesita DÓNDE, no solo
   si hubo (coincide con el punto de FABLE).

## 4. Arquitectura de seguridad del despliegue local (propuesta)
```
[ Sensores edge (mic array + cámara) ]
        │ (LAN aislada, sin salida a internet)
        ▼
[ Nodo de captura ]  → hash SHA-256 + timestamp NTP-local al instante
        │  (escribe original READ-ONLY en almacén WORM cifrado)
        ▼
[ Motor de detección local ]  audio (CNN/transformer) + visión (YOLO) → FUSIÓN
        │  (registra detección + score + versión de modelo en log append-only encadenado)
        ▼
[ Panel forense local ]  RBAC + auth · revisión de evidencia · export firmado con cadena de custodia
        ▼
[ Alertado LOCAL ]  sirena / webhook LAN / SMS por módem GSM propio (NO nube)
```
Principios: air-gap por defecto · todo se hashea y fecha al entrar · log inmutable · cifrado en
reposo · mínimo privilegio · el original nunca se toca.

## 5. Recomendación de mi dimensión (para el veredicto de JARVIS)
- Como **motor de detección**: priorizar repos **offline + con pesos** (02 audio, 05 visión) o el de
  mayor precisión que confirme FABLE (rcarioni 100% precisión = clave anti-falsos-positivos).
- **Descartar tal cual** los cloud-coupled (01, 04) para producción — reusar solo ideas de UI/DB.
- **La capa forense la construimos nosotros**: ningún repo la trae. Es el verdadero trabajo de
  ingeniería del producto y donde está el valor (y mi carril).
- **Bloqueante legal:** sin licencia ⇒ no usable. El finalista debe tener licencia permisiva
  (MIT/Apache) o reentrenar modelo propio con datos de licencia clara. (ALICE confirma.)
