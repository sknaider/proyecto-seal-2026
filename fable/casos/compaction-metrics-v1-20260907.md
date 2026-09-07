# Caso para FABLE — compaction-metrics-v1 (owner ADA; revisor JARVIS; gate STATIC_OK 15:37)
- Manifiesto: `quality/manifests/compaction-metrics-v1.json`. Sujetos: `memory/compaction_metrics.py`, `memory/pre_compact_hook.py`, `memory/post_compact_session_start_hook.py`, `quality/delivery_compaction_metrics.sh`. Test: `memory/tests/test_compaction_metrics_v1.py`.
- Qué: instrumentación de la compactación (qué entró, qué salió, si salió degradada). Idea reescrita desde cero, sin código ajeno.
- Re-verificación (13:30): sujetos y test idénticos a la firma del 4-sep; único cambio, la clave ficticia `credencial_muerta` → `REDACTADO` en el script de entrega (scrub de ALICE). Brazos con el venv: 8 unit + 2 subtests, 2 positivos, 1 negativo, 1 control.
- Mutación (15:31, arena aprobada, revisor JARVIS): 8/8 muertos (degradado falso/verdadero, registrar miente, medir revienta, cuenta vacías, señal desalineada, pre/post no instrumentan), ancla/reemplazo/sha por mutante.
- Límite declarado: ADA no puede ejecutar nada (guardián nerves); valida por lectura. Los argv del manifiesto dicen `python3` (sin pytest en este host); el revisor corrió con el venv.
- Lo que refutaría: un hook que no registre en `SEAL_COMPACTION_METRICS`; `es_degradado` verdadero con un contexto sano; `registrar` devolviendo True sin escribir.
