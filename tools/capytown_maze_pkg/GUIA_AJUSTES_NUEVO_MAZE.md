# Guía de ajustes — reto cajas/lazo y laberinto

**Escenario actual (Henry, 1-jul):** NO estamos resolviendo el reto final todavía.
El objetivo inmediato es el reto de cajas: un circuito en lazo alrededor de una
isla central, con cajas sueltas en el corredor. Se mantiene wall-following, pero
una caja no debe activar `RECOVER` de 180 grados.

Para volver al laberinto con callejones reales, usar `course_mode: "maze"`.

El controlador (`maze_navigator.py`) **ya implementa los 4 comportamientos** que pediste
(verificados antes en tu robot). Esta guía dice QUÉ perilla mover si en TU maze nuevo algo
no sale fino. Todas se editan en `config/maze_params.yaml` (o por `--ros-args -p nombre:=valor`).

---

## 1) «Que NO ondule — que vaya en línea recta»
| perilla | default | si ondula… |
|---|---|---|
| `wall_target` | 0.20 | distancia LiDAR -> pared confirmada para robot 9 |
| `wall_clearance` | 0.12 | distancia aproximada del BORDE del robot a la pared cuando el LiDAR va a 0.20m |
| `deadband` | 0.10 | banda ancha anti-ondulación; si queda demasiado lento corrigiendo, bajala poco a poco (0.08, 0.06) |
| `kd` | 1.60 | subila un poco (1.8–2.2) si oscila alrededor de la pared |
| `kp` | 0.70 | bajala (0.55–0.65) si corrige demasiado brusco |
| `straight_when_no_wall` | True | clave anti-círculo: si no ve pared lateral útil, mantiene rumbo por odom y avanza recto hasta reacoplar pared |
| `open_space_straight` | False | mantenelo en False para el robot 9; el laberinto sigue siendo wall-follower |
| `heading_kp` / `heading_w_max` | 1.2 / 0.45 | bajá `heading_w_max` (0.35) si aún corrige brusco en recto |
| `wall_align_enabled` | True | compara derecha-delantera vs derecha-trasera para saber si está paralelo a la pared |
| `wall_align_tol` | 0.04 | si `rf/rb` difieren menos que esto, considera el ángulo correcto |
| `wall_align_w_max` | 0.18 | corrección angular máxima por alineación de pared derecha |
| `corner_align_enabled` | True | corta giros de esquina si ve frente libre + pared atrás + pared derecha |
| `corner_rear_max` | 0.45 | máximo para considerar que hay pared detrás |
| `corner_side_max` | 0.38 | máximo para considerar que hay pared derecha en esquina |
| `corner_min_turn_t` | 0.45 | evita que el detector de esquina corte el giro apenas empieza |

## 2) «Que esquive las cajas (obstáculos)»
| perilla | default | qué hace / ajuste |
|---|---|---|
| `course_mode` | loop_boxes | modo del reto actual; desactiva el 180 por falso dead-end |
| `enable_obstacle_veer` | True | en reto cajas/lazo, rodea cajas avanzando; para laberinto puro puede apagarse |
| `disable_recover_180` | True | evita que una caja dispare una vuelta de 180 grados |
| `recover_persist` | 3.0 | cuánto debe durar un dead-end antes de recuperar; en loop_boxes además el 180 queda apagado |
| `obstacle_detect` | 0.35 | distancia (m) a la que EMPIEZA a esquivar una caja localizada. Subir mucho vuelve a confundir paredes |
| `box_shoulder_margin` | 0.12 | si los hombros frontales están cerca, es pared/corner, no caja |
| `veer_gain` | 1.2 | cuánto gira para rodear. Subir = rodea más cerrado |
| `veer_min_w` | 0.55 | giro mínimo sostenido (anti micro-zigzag al esquivar) |
| `veer_resume_t` | 0.0 | no recupera rumbo por odom; devuelve control al wall-follower |
| `veer_grace_t` | 0.8 | solo ignora la misma caja/pared para no reactivar el esquive |
| `veer_min_dist` | 0.85 | distancia mínima comprometida antes de volver a la ruta |
| `veer_min_t` | 2.3 | tiempo mínimo de paso aunque el frente se libere temprano |
| `veer_out_angle` | 4.0 | ángulo mínimo: cambio de carril, no rodeo con giro |
| `veer_out_speed` | 0.10 | avance durante OUT; evita giro en seco |
| `veer_turn_speed` | 0.08 | velocidad angular máxima durante OUT |
| `veer_pass_speed` | 0.08 | velocidad durante la fase PASS |
| `veer_max_yaw_delta` | 25.0 | límite anti-180 durante el rodeo |
| `veer_finish_yaw_tol` | 25.0 | exige volver mirando al rumbo original antes de cerrar el rodeo |
| `veer_force_away_from_wall` | True | en pared derecha, esquiva siempre hacia la izquierda |
| `veer_back_enabled` | False | no hace giro fuerte de regreso; reacopla pared derecha después |
| `post_veer_reacquire_t` | 3.0 | tiempo en que se bloquea un falso TURN_OUT después de esquivar |
| `post_veer_reacquire_dist` | 0.30 | distancia mínima recta antes de permitir giros normales otra vez |
| `post_veer_wall_max` | 0.42 | pared derecha suficientemente visible para considerar reacople |
| `post_veer_w_max` | 0.10 | límite de corrección suave durante reacople post-caja |
| `debug_report_path` | `/tmp/capytown_maze_report.log` | archivo de informe con decisiones internas del robot |
| `min_side_clearance` | 0.23 | lado libre mínimo (m) para animarse a esquivar. Bajar = esquiva en huecos más justos |
| `veer_timeout` | 4.0 | s máx comprometido en un esquive antes de soltar (si quedó encajonado) |
| `obstacle_clear` | 0.65 | frente lo bastante libre (m) para DAR POR TERMINADO el esquive |

## 3) «Tiempo de reacción»
- `obstacle_detect` ↑ y `front_slow` ↑ → frena/esquiva con más anticipación.
- `front_block_persist` (0.35 s) evita falso `TURN_IN`: bajar solo si reacciona tarde ante pared real.
- `front_sector` (12 grados) evita que paredes laterales disparen `TURN_IN`: subir solo si no detecta paredes frontales reales.
- `v_max` (0.18) ↓ → menos velocidad = más tiempo para reaccionar (subir solo si reacciona bien).
- `control_hz` (10.0) debe igualar la tasa del LiDAR MS200 (~10 Hz); más alto NO ayuda si el scan no llega más rápido.

## 4) «Que detecte bien» — robustez de sensor (ya integrada)
- `/scan` se suscribe **BEST_EFFORT** (causa #1 del «no se mueve»; un sub RELIABLE recibe CERO).
- `sector_drop` (2): descarta los rayos más cercanos por sector → mata reflejos fantasma que daban falsos obstáculos.
- `scan_timeout` (0.5 s): si el LiDAR deja de publicar → FRENA (no maneja a ciegas).
- `range_min/range_max` (0.12 / 8.0): recorte de rangos válidos; robot 9 reportó `range_min=0.12`.

---

## Recomendación de arranque para el maze nuevo
En `config/maze_params.yaml`, para el escenario «paredes reales + cajas sueltas + tramos abiertos»:
```yaml
course_mode: "loop_boxes"       # reto actual de cajas/lazo
odom_topic: "/odom_raw"       # robot 9
wall_clearance: 0.12          # borde del robot -> pared aprox
corridor_width: 0.60          # pasillo aproximado
wall_target: 0.20             # LiDAR->pared confirmado por Henry
wall_align_enabled: true      # mantener paralelo a pared derecha con rf/rb
wall_align_tol: 0.04          # rf/rb casi iguales = ángulo correcto
wall_align_w_max: 0.18        # corrección angular máxima por alineación
corner_align_enabled: true    # esquina correcta: frente libre + pared atrás + derecha
corner_rear_max: 0.45         # pared atrás visible
corner_side_max: 0.38         # pared derecha visible
corner_min_turn_t: 0.45       # no cortar giro al primer instante
front_block: 0.30             # no girar por ruido lateral; solo frente realmente cerca
front_sector: 12.0            # sector frontal estrecho anti-falso TURN_IN
front_block_persist: 0.35     # bloqueo frontal debe durar antes de entrar a TURN_IN
enable_obstacle_veer: true    # rodea cajas en el lazo
disable_recover_180: true     # caja no debe activar vuelta de 180 grados
recover_persist: 3.0          # margen contra falso dead-end
veer_resume_t: 0.0            # no heading-hold al terminar; reacopla pared derecha
veer_grace_t: 0.8             # evita re-detectar la misma caja como bloqueo
obstacle_detect: 0.35         # evita esquivar con demasiada antelacion
box_shoulder_margin: 0.12     # exige obstaculo localizado, no pared ancha
veer_min_dist: 0.85           # pasa la protrusión antes de volver a ruta
veer_min_t: 2.3               # evita soltar el paso al primer claro
veer_out_angle: 4.0           # fase OUT: salida mínima, no rodeo
veer_out_speed: 0.10          # fase OUT: avanza, no pivotea
veer_turn_speed: 0.08         # fase OUT: giro máximo bajo
veer_pass_speed: 0.08         # fase PASS: pasa lento por el costado
veer_max_yaw_delta: 25.0      # anti-180 durante rodeo
veer_finish_yaw_tol: 25.0     # no cerrar rodeo si mira al sentido contrario
veer_force_away_from_wall: true # esquiva lejos de pared derecha
veer_back_enabled: false      # no giro fuerte de regreso
post_veer_reacquire_t: 3.0    # evita convertir salida de caja en esquina falsa
post_veer_reacquire_dist: 0.30 # avanza recto antes de reactivar TURN_OUT/TURN_IN
post_veer_wall_max: 0.42      # pared derecha detectada = reacople
post_veer_w_max: 0.10         # giro suave, no vuelta de 90 grados
debug_report_enabled: true
debug_report_path: "/tmp/capytown_maze_report.log"
straight_when_no_wall: true   # si no ve pared, avanza recto hasta reacoplar
deadband: 0.10                # anti-ondulación para robot 9
```
Probá en el maze y reportá el síntoma exacto (¿ondula en qué tramo? ¿no esquiva qué caja?
¿reacciona tarde?) y afino la perilla puntual — no toco a ciegas lo que ya está verificado.

## Correr
```bash
cd ~/yahboomcar_ws && colcon build --packages-select capytown_maze_pkg && source install/setup.bash
ros2 launch capytown_maze_pkg maze.launch.py
# probar parámetro al vuelo sin recompilar:
ros2 launch capytown_maze_pkg maze.launch.py side:=left
ros2 launch capytown_maze_pkg maze.launch.py front_angle_offset:=90.0
```
