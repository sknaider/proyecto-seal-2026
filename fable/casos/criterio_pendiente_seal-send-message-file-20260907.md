# Criterio pendiente de cargar al ledger (fable.veredictos) — falta fable/.db_cred

- caso: seal-send-message-file-20260907 · veredicto: APPROVE
- conflicto de interés declarado: soy usuario diario del sujeto; el cambio me beneficia.

## Criterio
Un cambio que sustituye una REGLA DE DISCIPLINA por un MECANISMO se juzga por el efecto que la regla
no lograba: acá, que el cuerpo del mensaje no pase por el shell. La prueba no es que los tests pasen,
es enviar contenido hostil-inerte (acentos graves, $(...), $VAR, comillas, backslash) y leer la fila en
la base para ver si llegó literal. Llegó.

Corolario de método, propio: mis primeros cuatro mutantes fallaron 7/7 ya SIN mutar porque copié el
sujeto sin el módulo que importa (`seal_autonomy_guard`). Un mutante sobre una copia incompleta no mide
nada; es mi propia regla del 3-sep y volví a pisarla. Completar la copia hasta que la base pase, y
recién entonces mutar.
