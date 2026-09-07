# Ronda 2: META por cámara (verde) + ruta optimizada (BFS)

## En qué parte del código está cada cosa

**ronda2_manager.py** (el nodo nuevo):
- `detect_green_meta()` — detecta la META por COLOR VERDE (HSV + contornos, misma
  técnica que el pare_detector usa para el rojo). Devuelve si hay verde + su área.
- `on_image()` — recibe cada frame de la cámara (/image_raw) y aplica un DEBOUNCE:
  exige que la MAYORÍA de los últimos N frames vean verde (anti-falso-positivo).
- `confirmar_meta()` — cuando el verde se confirma: publica /meta_verde=True y
  arranca el TEMPORIZADOR (guarda el instante).
- `tick()` — cada 0.5s revisa el reloj; a los **10 segundos** de la meta llama a
  `iniciar_ronda2()`.
- `iniciar_ronda2()` — pasa a RONDA 2: llama a `calcular_ruta_optima()` y publica
  /ronda2_start=True + /ronda2_ruta (la lista de celdas de la ruta corta).
- `calcular_ruta_optima()` — carga el mapa aprendido (maze_planner.load_map) y
  corre BFS (shortest_path) para la ruta más corta.

**maze_planner.py** (el motor de la ruta):
- `MazePlanner.shortest_path()` — BFS sobre la grilla del laberinto: la primera vez
  que toca la META, ese camino tiene el MENOR número de celdas = ruta más corta.
- `save_map()/load_map()` — persiste/recupera el mapa aprendido (JSON) entre rondas.

## Cómo funciona (flujo)
Cámara ve VERDE → (debounce anti-falso-positivo) → META confirmada → esperar 10s →
Ronda 2 → cargar mapa → BFS calcula la ruta más corta → publicar la ruta.

## HONESTO (para la defensa)
Este código IMPLEMENTA el diseño: detección verde, temporizador de 10s y cálculo
BFS de la ruta óptima — todo REAL. NO fue probado en el robot físico (el hardware
falló). Presentalo como "implementado, listo para integrar", no como "probado en
cancha". Lo que queda pendiente de integración es que el robot MANEJE los waypoints
de la ruta (conectar /ronda2_ruta al maze_solver); acá la ruta se CALCULA y se
PUBLICA. La detección verde y el temporizador sí quedan de punta a punta.
