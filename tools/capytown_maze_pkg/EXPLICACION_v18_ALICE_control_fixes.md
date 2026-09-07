# CapyTown v18 — Sección de CONTROL y corrección de errores (ALICE) — para el profesor

> Insumo para el doc consolidado (lidera JARVIS). Cubre: cómo funciona el control, qué se
> usó, los módulos, la configuración, y CÓMO se corrigió cada error. Verificado contra el
> código real (`capytown_maze_pkg/capytown_maze_pkg/maze_navigator.py`, 1404 líneas).

## 1. Qué se usó (stack)
- **ROS 2** (rclpy). Nodo `maze_navigator` (clase `MazeNavigator(Node)`).
- **Sensores**: LiDAR 2D `/scan` (`sensor_msgs/LaserScan`) + odometría `/odom_raw` (`nav_msgs/Odometry`). Actuación: `/cmd_vel` (`geometry_msgs/Twist`).
- **QoS**: el LiDAR publica en BEST_EFFORT → el suscriptor usa BEST_EFFORT (un suscriptor RELIABLE contra un publicador BEST_EFFORT **no recibe datos** → el robot quedaba quieto sin error; lección clave).
- **Sin cámara**: navegación 100% con LiDAR + odom. Lazo de control a `control_hz = 10 Hz`.
- **Diseño**: máquina de estados (FSM) de wall-following mano-derecha + control PD, TODO en funciones puras testeables (`decide_state`, `follow_cmd`, `run_turn`) → 23 tests unitarios sin ROS.

## 2. Cómo funciona (arquitectura de control)
- **Sectores del LiDAR**: se agregan rayos en sectores robustos (mediana, descartando outliers): `front` (±frente), `left`, `right`, hombros (`shoulder_left/right`), y perfil de pared (`right_front`/`right_back` a −70/−45° y −135/−110°) para medir el ÁNGULO de la pared. Todo con `front_angle_offset` (rota los sectores si el LiDAR está montado girado; en robot 9 = 0, verificado por efecto en el log).
- **FSM** (`decide_state`): prioridad → `boxed`→(en lazo de cajas) `TURN_IN` ; pared se abre (`right ≥ wall_lost`)→`TURN_OUT` (tomar la esquina, regla mano-derecha) ; frente bloqueado→`TURN_IN` ; si no→`FOLLOW_WALL`.
- **Wall-follow PD** (`follow_cmd`): error = `wall_target − dist_pared_derecha`; `angular = signo·(kp·err + kd·derivada)`; con **deadband** (ignora micro-error cerca del target → no zigzaguea) y término D (amortigua la oscilación). Se ralentiza al acercarse al frente.
- **Alineación de pared** (`wall_align`): usa `right_front` vs `right_back` para medir si el robot va PARALELO a la pared y corrige el ángulo → mantiene el rumbo estable (no reactivo a la distancia frontal).
- **Giros por odom** (`begin_turn`/`run_turn`): los giros son closed-loop por yaw de odom (giro-por-efecto), NO por tiempo → deterministas.
- **Geometría adaptativa** (`adaptive_geom`): los umbrales (`wall_target`, `wall_block`, `front_block`, `emerg_dist`, diagonal para poder rotar) se derivan del ANCHO/LARGO del robot (W/L), no del laberinto → funciona a cualquier ancho de pasillo.

## 3. CÓMO se corrigió cada error (lo que el profe va a preguntar)

### 3.1 El robot se daba vuelta 180° (el bug principal) — CAUSA RAÍZ en `run_turn`
- **Síntoma**: al esquivar/girar en un rincón, en vez de girar 90° terminaba mirando hacia atrás (~180°).
- **Diagnóstico por efecto** (leyendo el log de decisiones del robot, no adivinando): un TURN_IN de 90° rotaba hasta ~170°. **Causa**: `run_turn` frenaba SOLO cuando `|error angular| < 4°`. Pero a `control_hz=10` con `turn_speed=1.5 rad/s`, el robot rota ~**8.6° por tick** — MÁS que la ventana de 4° → la ventana se SALTA, `run_turn` nunca ve el error < 4°, y el giro sigue hasta el watchdog de timeout (~170°).
- **Fix**: parar cuando alcanza **O PASA** el objetivo:
  `if abs(err) < radians(4) or (self._turn_sign * err < 0): parar`  → `_turn_sign*err<0` = ya pasamos el objetivo (overshoot). Ahora corta en ~90° (simulado: para en ~93°). **Afecta TODOS los giros** = la raíz.

### 3.2 Cascada de TURN_IN (180 por acumulación)
- **Síntoma**: en rincones, dos TURN_IN de 90° consecutivos se sumaban a 180°.
- **Fix**: acumulador `turn_in_accum_deg`; si el próximo giro pasaría de `turn_in_max_accum_deg=135°` → en vez de girar hace **reversa corta** (`TURN_IN_CASCADE_BREAK`). Aplicado en los DOS caminos de giro (el normal de `decide_state` y el de EMERGENCIA `front < emerg_dist`). El acumulador se **resetea** cuando el frente estuvo DESPEJADO de forma sostenida (`turn_in_reset_clear_t=3s`) = el robot escapó de verdad (no cuando solo avanza hacia el obstáculo — ese fue un residual que corregimos: el reset por desplazamiento euclidiano disparaba de más).

### 3.3 Volver al rumbo tras esquivar (no retroceder de más)
- **Fix**: al iniciar una secuencia de giro se guarda el yaw `turn_in_start_yaw`; tras el corte anti-180, en una ventana `turn_in_restore_t` el robot **restaura el rumbo previo con odom** (`heading_hold_cmd`) y sigue de frente, en vez de quedar torcido. Con **abort de seguridad**: si el frente se pone muy cerca durante la restauración → reversa.

### 3.4 Parada de emergencia (evita choque frontal)
- En v18 la parada de emergencia dispara con `emerg_dist = L/2 + 0.05` (el borde delantero del robot está a L/2 del LiDAR): si el frente está muy cerca, para y gira si cabe, o retrocede si el pasillo es más angosto que la diagonal del robot. (Nota: un ajuste posterior para reaccionar 4cm antes = L/2+0.09 NO forma parte de v18, quedó fuera del entregable.)

### 3.5 Giraba en círculos / falso "frente bloqueado"
- **Fix**: histéresis del frente (entra bloqueado en `front_block`, sale en `front_clear`) + persistencia (`front_block_time`) → un reflejo lateral momentáneo no dispara un TURN_IN cada tick.

## 4. Configuración clave (params, `maze_params.yaml` / launch, tuneables por `:=`)
- `control_hz=10` · `turn_speed=1.5` · `side=right` · `course_mode=loop_boxes`
- `wall_clearance=0.06` (distancia borde→pared derecha; menor = más pegado) → `wall_target` adaptativo
- `front_angle_offset=0.0` (alineación LiDAR verificada) · `require_odom=True` (sin odom no se mueve) · timeouts de scan/odom/turn (guardas de seguridad)
- Anti-180: `turn_in_max_accum_deg=135` · `turn_in_reset_clear_t=3.0` · `turn_in_restore_t=2.5`
- Emergencia: `emerg_dist = L/2 + 0.05` (baseline v18)

## 5. Módulos del paquete
- `maze_navigator.py` — el cerebro (FSM + control + fixes). 
- `box_detector.py` — detección de cajas por clustering de LiDAR (censo).
- `loop_completion.py` — detecta vuelta completa del lazo → para (auto-stop).
- `split_merge.py` — segmentación de la nube LiDAR (líneas/paredes).
- `launch/maze.launch.py` — declara todos los params como argumentos (`:=`) + lanza nodo + box_detector.
- `config/maze_params.yaml` — valores por defecto. `test_maze_logic.py` / `test_maze_scenarios.py` — 23 tests de la lógica pura.

## 5b. Agregados para la rúbrica RC-4 (censo + guardián + métricas)
> El código base (v18, control/navegación) se mantiene; estos son los AGREGADOS para cumplir la rúbrica «El Censo y el Guardián».

### Guardián — parada 3s frente a cada caja (ALICE, IMP3)
- Estado GUARD_STOP en `maze_navigator`: al detectar una CAJA adelante (`localized_front_obstacle`), el robot **PARA** (publica velocidad 0,0) y **ESPERA** `box_stop_wait_t=3.0 s` ANTES de rodear (veer). La parada ocurre a la distancia de disparo del veer (>15 cm, cumple «parada ≥15 cm»). Se **resetea por caja** (`box_stopped` se limpia en `finish_veer`) → para en CADA caja, no solo la primera. = «el guardián se detiene frente a cada caja y la rodea sin tumbarla».

### Censo — posiciones y métricas (box_detector, JARVIS)
- **Posiciones (IMP2)**: `box_detector` publica `/cajas_avistadas_pos` (`geometry_msgs/PoseArray`) con la posición en el MUNDO (marco odom) de cada caja, además del conteo en `/cajas_avistadas`. Proyección polar→cartesiano→odom: `wx = x0 + dist·cos(yaw+ang)`.
- **Métricas (IMP2/IMP4/DEF3)**: función pura `census_metrics(detectadas, ground_truth, match_dist=0.30)` → matching greedy 1-a-1 (≤30 cm) → VP (matcheadas), FP (sin ground-truth), FN (GT no detectada), tasa = VP/(VP+FN), error_pos_promedio. Al cerrar cada corrida agrega una fila a `metricas_lidar.csv` (run_id, VP/FP/FN, tasa, error, posiciones). Ground-truth = cajas reales medidas con cinta.
- **RViz (IMP4)**: `rviz/capytown_censo.rviz` visualiza las cajas censadas (para la captura del entregable).

## 6. Metodología (por si el profe pregunta cómo trabajaron)
- **Verificación por efecto**: cada fix se validó contra el LOG REAL del robot (no "compila y listo"). El bug del 180 se cazó leyendo la traza de yaw tick a tick.
- **builder ≠ verifier**: quien codea no es quien verifica (re-derivación independiente de las fórmulas/geometría).
- **Fix de RAÍZ > parche**: se buscó la causa (run_turn overshoot) en vez de tunear síntomas.
