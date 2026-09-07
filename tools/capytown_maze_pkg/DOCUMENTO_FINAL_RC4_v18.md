# Documento final RC-4 - CapyTown v18 final

Paquete final: `capytown_maze_pkg_loop_boxes_v18_origin_laps.zip`

## 1. Que se implemento

El proyecto resuelve el reto RC-4 "El Censo y el Guardian de las Cajas" con ROS2, LiDAR 2D MS200 y odometria. No usa IA en tiempo real. La IA ayudo a depurar y documentar; el robot ejecuta reglas deterministicas en Python/ROS2.

Componentes principales:

- `box_detector.py`: censo de cajas desde `/scan`.
- `maze_navigator.py`: FSM de reaccion/guardian, seguimiento de pared, esquive de cajas y parada al completar vuelta.
- `loop_completion.py`: deteccion de vuelta completa por odometria. En esta version, el robot sigue dando vueltas y cada regreso valido a la posicion de origen cuenta como una vuelta.
- `maze.launch.py`: lanzamiento de `maze_navigator` + `box_detector`.
- `config/maze_params.yaml`: parametros de navegacion, seguridad y esquive.

## 2. Censo de cajas

`box_detector.py` escucha `/scan` y segmenta el LiDAR por clustering 1D. La idea es recorrer los rangos consecutivos y separar un nuevo segmento cuando el salto de distancia supera `RANGE_JUMP = 0.12 m`.

Una caja candidata debe cumplir:

- Ancho fisico cercano a `BOX_SIZE = 0.20 m`.
- Tolerancia `BOX_TOL = 0.08 m`.
- Debe sobresalir del fondo al menos `MIN_PROTRUSION = 0.05 m`.
- Debe tener suficientes rayos reales, estar en el sector util frontal/lateral y estar cerca (`DETECT_MAX_DIST = 1.20 m`).
- Por seguridad de la rubrica RC-4 se limita el censo a 5 cajas por vuelta (`MAX_BOXES_PER_LAP = 5`), porque la pista tiene 5 cajas reales.

Despues, cada deteccion se transforma de polar a mundo usando `/odom`:

`wx = x_robot + dist * cos(yaw + ang)`

`wy = y_robot + dist * sin(yaw + ang)`

Para no contar dos veces la misma caja se usa `BOX_MERGE_DIST = 0.40 m`, consistente con la regla de cajas separadas al menos 40 cm.

Salidas:

- `/cajas_avistadas`: `std_msgs/Int32` con el total censado.
- `/cajas_avistadas_pos`: `PoseArray` con posiciones estimadas en marco `odom`.
- `/tmp/capytown_box_detections.csv`: detecciones con posicion, distancia y angulo.
- `/tmp/metricas_lidar.csv`: resumen por vuelta/corrida. El navegador publica `/capytown_lap` al volver al origen y `box_detector` cierra una fila de metricas por vuelta.

## 3. Guardian / FSM

`maze_navigator.py` toma sectores del LiDAR:

- Frente.
- Derecha.
- Izquierda.
- Hombros frontales.
- Perfil lateral delantero/trasero.

Estados principales:

- `FOLLOW_WALL`: sigue pared derecha con control PD.
- `TURN_IN`: giro de 90 grados cuando el frente esta bloqueado.
- `TURN_OUT`: toma apertura/esquina cuando la pared derecha desaparece.
- `RECOVER`: giro de 180 solo para laberinto normal; en `loop_boxes` queda desactivado.
- `DONE`: se detiene al completar la vuelta.

La v18 final conserva el comportamiento que mejor funciono en el robot fisico y suma dos mejoras de rubrica: cuando detecta una caja localizada y puede rodearla, primero se detiene `box_stop_wait_t = 3.0 s` y despues ejecuta `VEER`; ademas detecta cada vuelta al regresar a la posicion de origen y sigue hasta completar `target_laps = 10`.

Eso apunta al criterio IMP3: "el guardian se detiene y rodea cada caja".

## 4. Correcciones importantes de errores

### Giro falso de 180 grados

El problema era que un giro de 90 grados podia pasarse hasta casi 180. La causa era que el giro solo paraba si `abs(err) < 4 grados`; a 10 Hz podia saltarse esa ventana.

Correccion:

- `run_turn()` corta si `abs(err) < 4 grados`.
- Tambien corta si el signo del error indica que ya sobrepaso el objetivo: `self._turn_sign * err < 0.0`.

Explicacion para defensa:

"El giro se cierra por odometria y por cruce del objetivo, no por tiempo. Si el robot alcanza o sobrepasa los 90 grados, deja de girar."

### Anti-cascada de giros

Dos giros de 90 seguidos podian sumar 180 sin que el robot hubiera escapado realmente. v18 usa:

- `turn_in_accum_deg`.
- `turn_in_max_accum_deg = 135`.
- `turn_in_reset_clear_t = 3.0`.

Si otro `TURN_IN` superaria el limite, no gira otra vez: corta la cascada y usa reversa/restauracion de rumbo.

### No usar RECOVER 180 en loop_boxes

La pista es un lazo con cajas, no un laberinto con callejones reales. Por eso:

- `course_mode = loop_boxes`.
- `disable_recover_180 = true`.

Un bloqueo no dispara 180 automaticamente.

## 5. Como rodea cajas

El rodeo usa `VEER`, no un giro brusco:

1. Detecta caja localizada con `localized_front_obstacle(...)`.
2. Se detiene 3 s frente a la caja (`GUARD_STOP_BOX` en log).
3. Entra a fase `OUT` con un desvio pequeno.
4. Entra a fase `PASS` y avanza hasta pasar una distancia minima.
5. Sale a `POST_VEER_REACQUIRE` para reacoplar la pared sin tomar una falsa esquina.

Parametros clave:

- `obstacle_detect = 0.35`.
- `box_shoulder_margin = 0.12`.
- `min_side_clearance = 0.23`.
- `veer_out_angle = 4.0`.
- `veer_min_dist = 0.85`.
- `veer_min_t = 2.3`.
- `box_stop_wait_t = 3.0`.

## 6. Seguridad

El robot se detiene si:

- No hay `/scan` fresco (`scan_timeout = 0.5`).
- No hay `/odom` fresco (`require_odom = true`, `odom_timeout = 1.0`).
- Un giro tarda demasiado (`turn_timeout = 6.0`).
- El frente esta demasiado cerca (`emerg_dist`).
- No progresa en posicion durante suficiente tiempo (`stuck_t`, `stuck_dpos`).

## 7. Evidencia que genera para la rubrica

Archivos en el robot:

- `/tmp/capytown_maze_report.log`: log de decisiones de la FSM.
- `/tmp/capytown_box_detections.csv`: cada caja censada con posicion.
- `/tmp/metricas_lidar.csv`: resumen de la corrida.

Comandos utiles despues de probar:

```bash
cat /tmp/metricas_lidar.csv
cat /tmp/capytown_box_detections.csv
grep -E "GUARD_STOP_BOX|VEER_START|VEER_PASS_FINISH|TURN_IN|EMERGENCY" /tmp/capytown_maze_report.log | tail -80
```

## 8. Como correr

Instalar en el workspace ROS2:

```bash
cd ~/ros2_ws/src
unzip capytown_maze_pkg_loop_boxes_v18_origin_laps.zip
cd ~/ros2_ws
colcon build --packages-select capytown_maze_pkg
source install/setup.bash
```

Lanzar:

```bash
ros2 launch capytown_maze_pkg maze.launch.py
```

Por defecto corre 10 vueltas (`target_laps:=10`). Cada vuelta se cuenta cuando el robot se aleja del origen (`loop_away_dist=0.30`), recorre una distancia minima (`loop_min_path=1.0`) y vuelve cerca de la posicion inicial. Para una demo corta:

```bash
ros2 launch capytown_maze_pkg maze.launch.py target_laps:=2
```

Si se tienen coordenadas reales de cajas medidas con cinta, se puede pasar `ground_truth` al nodo `box_detector` manualmente como lista plana `[x1,y1,x2,y2,...]` y el CSV calculara VP/FP/FN/error.

## 9. Que cumple contra la rubrica

- IMP1: cumple censo funcional en codigo: filtra `/scan`, clusteriza y publica `/cajas_avistadas`.
- IMP2: cumple posiciones, deduplicacion y filtro anti-falsos positivos: publica `/cajas_avistadas_pos` y CSV de detecciones. El error <=30 cm debe validarse con medicion real.
- IMP3: cumple guardian: se detiene 3 s frente a caja y la rodea con `VEER`. La condicion "0 colisiones en 10 corridas" depende de la prueba fisica.
- IMP4: cumple codigo modular, documentado, tests y logs. Para puntaje completo agregar capturas RViz/video y conservar los CSV generados durante la prueba fisica.
- DEF1-DEF4: hay base tecnica para explicar LaserScan, clustering, transformacion a odom, FSM y correcciones.
- DEF5: depende de que el grupo exponga de forma ordenada.

## 10. Pruebas ejecutadas antes de entregar

```bash
python3 tools/capytown_maze_pkg/test_maze_logic.py
# RESULT: ALL TESTS PASSED

python3 tools/capytown_maze_pkg/test_maze_scenarios.py
# RESULT: ALL SCENARIO TESTS PASSED

python3 -m py_compile tools/capytown_maze_pkg/capytown_maze_pkg/maze_navigator.py \
  tools/capytown_maze_pkg/capytown_maze_pkg/box_detector.py \
  tools/capytown_maze_pkg/capytown_maze_pkg/loop_completion.py \
  tools/capytown_maze_pkg/launch/maze.launch.py
# OK
```

## 11. Que decir si preguntan por IA

"La IA ayudo como asistente de depuracion y documentacion. El robot no consulta IA mientras corre. Todo el comportamiento esta implementado en ROS2/Python con reglas deterministicas: LiDAR, odometria, clustering, FSM y control PD."
