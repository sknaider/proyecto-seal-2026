# Decisiones Pendientes para William — 2026-04-19
> Compilado por JARVIS + ALICE | Para cuando William vuelva

---

## 🔴🔴 CRÍTICO — Seguridad Inmediata

### 0. Password FileBrowser en plaintext — seal-share/MANUAL_SEAL_CHAT.md
**Riesgo:** `Seal42478340!` expuesta en línea 64 del archivo, accesible desde toda la red local vía SMB (`\\192.168.68.80\seal-share`).
**Acción:** William decide — ¿borrar contraseña del doc o cambiar contraseña FileBrowser?
**Quién:** Cualquier persona en la red 192.168.68.x puede ver este archivo via SMB.

---

## 🔴 Urgente (tiempo-sensible)

### 1. CBSoft 2026 — Contribución SEAL a presentar
**Deadline:** 27-abr registro | 4-may paper
**Opciones JARVIS recomienda:**
- **SEAL-CL** (Null-Space Inner Loop): previene forgetting catastrófico 35%→10-15%. Código implementado en autoresearch/Seal/. Reproducible en DGX Spark con Qwen2.5-7B.
- **SEAL-UL** (3-tiempos reward sin labels): extiende SEAL a corpora sin QA. Más novel científicamente, más complejo de verificar en <2 semanas.
**Decisión necesaria:** ¿Cuál contribución? ¿Henry como co-autor?
**Material listo:** 6 archivos .md en autoresearch/Seal/, código en continual_self_edits_v2.py

---

## 🟡 Importante (decide esta semana)

### 2. overnight_v2 benchmark — Base para Ronda 3
**Situación:** overnight_v2/final_model (65GB, Mar 27) pre-evaluado por ADA sin cargar el modelo:
- Pre-assessment: accuracy 20/20 (100%), loss 1.1857 vs ronda2 1.1924 → mejora narrativa
- ⚠️ PERO: los losses NO son comparables (frameworks distintos: batch custom vs HF Trainer + packing 8.77x)
- El eval 20 preguntas es estadísticamente ciego (1 error = 5% — no detecta degradación sutil)
- El riesgo de stacking sobre overnight_medical (5.6% completo) NO está cuantificado

**Acción necesaria:** Benchmark formal >200 preguntas, tasks diversificadas. Requiere cargar 65GB → matar Ollama de DUM temporalmente.
**Pregunta:** ¿OK para matar Ollama de DUM para el benchmark formal?

### 3. DGM (Darwin-Gödel Machine) — ✅ DECISIÓN TOMADA (19-abr 13:38)
**Decisión William:** JARVIS como piloto. Si pasa → implementar a ADA y ALICE también.
**Siguiente paso:** JARVIS implementa scoring.py + whitelist mínima (Fase 1). ALICE documenta resultados.

---

## 🟢 Informativo (sin acción urgente)

### 4. KAIROS — Requiere SEAL-CLI Fork (H3.1)
No se puede activar con settings solo — kairosActive default=false en binary. El fix es 1 línea en el fork. Esto va con H3.1 SEAL-CLI Fork en el roadmap.

### 5. autoresearch (Karpathy framework)
Framework activo para auto-mejora de código de entrenamiento. Dormido actualmente. Potencial: activar para experimentos SEAL-CL nocturnos en DGX Spark.

### 6. overnight_medical — ¿Continuar o descartar?
Solo completó 5.6% (43/755 batches). Si queremos Ronda 3 con más datos médicos, necesita reiniciarse. ¿Prioridad post-Ronda 3 decision?

---

## ✅ Completado hoy (para tu info)

- Auditoría Spark: limpio. 2 http.servers inseguros eliminados.
- post_compact_hook: daily_brief ahora automático en compactaciones.
- H2.1 Durable Cron: implementado + timer activo.
- LODESTONE: decodificado (handler OS, baja prioridad).
- H2.6 spec: lista para que ADA implemente.
- SEAL-share + autoresearch: inventariados y reportados.

*JARVIS (estrategia) + ALICE (documentación) — 2026-04-19 13:00 Lima*
