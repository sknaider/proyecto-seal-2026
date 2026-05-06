# PENDIENTE — Paper arxiv 2604.15597 "LLMs Corrupt Your Documents When You Delegate"

**Asignado por:** William
**Fecha asignación:** 2026-04-27 18:49 Lima
**Status:** PENDING
**Memory DB ID:** soul_v3.memories id=49880 (scope=team, importance=10)

## Paper info
- **Authors:** Philippe Laban, Tobias Schnabel, Jennifer Neville (Microsoft Research)
- **Published:** 17 Apr 2026
- **Repo/dataset:** microsoft/DELEGATE52, datasets/microsoft/DELEGATE52
- **URL:** https://arxiv.org/abs/2604.15597

## Hallazgos clave
- Benchmark DELEGATE-52: 310 work environments, 52 dominios profesionales
- Metodología round-trip relay (backtranslation, reference-free)
- Frontier models (Claude 4.6 Opus, GPT 5.4, Gemini 3.1 Pro): ~25% corruption en 20 interacciones
- Promedio todos los modelos: 50% degradación
- Python ÚNICO dominio (de 52) donde modelos están "ready" (98%+)
- Agentic tool use NO mejora performance
- Performance @2 NO predice @20
- Errores SPARSE pero SEVEROS, compounding

## Acciones pendientes para SEAL
1. **Round-trip validation suite** para refactors masivos (los 67 archivos search_path de hoy son candidatos)
2. **Pre/post hash + sample comparison** para data migrations (rules, event_log, memories ya migradas)
3. **Long-horizon evaluation** de Soul memory (no solo @1 query, sino @20 consecutive)
4. **Citar este paper** en paper ICTSE 2026 sobre Soul memory como referencia metodológica
5. **Revisar el código de DELEGATE-52** en GitHub microsoft/DELEGATE52 — adaptar a nuestro use case

## Notas
- Este pendiente se guardó en Soul DB id=49880 con importance=10 para sobrevivir compactación
- Tomar acción cuando el equipo libere capacidad post-cutover v3
