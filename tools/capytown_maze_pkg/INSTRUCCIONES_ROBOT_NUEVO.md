# Iniciar capytown_maze_pkg en un ROBOT NUEVO (paso a paso)

Robot objetivo: **Yahboom MicroROS-Pi5 Robot Car** (Raspberry Pi 5, LiDAR MS200).
El sistema usa solo `/scan` (LiDAR 2D) + `/odom_raw` y publica `/cmd_vel`. Sin cámara, sin mapa.
Mantiene **wall-follower**. Para el reto actual de cajas/lazo, las cajas sueltas se
tratan como **obstáculos** que se rodean sin hacer 180 grados. Para un laberinto
con callejones reales se puede volver a `course_mode:=maze`.

Versión corregida 2026-07-01:
- Wall-follower conservado.
- `/odom_raw` permanente por defecto para el robot 9.
- Calibración robot 9: 20 cm de LiDAR a pared = `wall_target: 0.20`;
  pasillo aproximado 60 cm = `wall_lost` ~0.55 m.
- Anti-falso `TURN_IN`: el frente debe estar bloqueado de forma continua por
  `front_block_persist: 0.35` s antes de girar.
- Sector frontal reducido a +/-12 grados para no confundir paredes laterales con
  obstáculo frontal.
- `course_mode: loop_boxes` por defecto para el reto actual.
- `enable_obstacle_veer: true`: esquiva cajas avanzando, sin invertir rumbo.
- `disable_recover_180: true`: una caja no dispara `RECOVER` de 180 grados.
- `VEER_RESUME`: después de rodear una caja recupera el rumbo previo por odometría
  y aplica una gracia corta para no re-detectar la misma caja como otra ruta.
- Esquive por fases `OUT -> PASS -> BACK`: sale de la ruta, pasa la caja una
  distancia mínima y vuelve al rumbo previo antes de retomar wall-follow.
- Compuerta caja-vs-pared: solo esquiva si el obstáculo frontal es localizado;
  una pared/corner ancho lo deja al wall-follower.
- Si no ve pared lateral útil, avanza recto hasta reacoplar una pared en vez de girar en círculo.
- Menos ondulación: `kp` más bajo, `kd` más alto y `deadband` mayor.
- `box_detector` se lanza junto al navegador para censar cajas en `/cajas_avistadas`.

## 0. Requisitos previos (en el robot)
- Raspberry Pi 5 con **ROS 2** instalado (la imagen de fábrica de Yahboom ya lo trae).
- El **base node de Yahboom** corriendo (el que mueve el robot escuchando `/cmd_vel`).
- El **LiDAR MS200** publicando en `/scan`.

Verifica que los datos llegan ANTES de lanzar el nuestro:
```bash
ros2 topic echo /scan --once     # deben salir rangos del LiDAR
ros2 topic echo /odom_raw --once # debe salir la odometría del robot 9
```
Si `/scan` no muestra nada → arranca primero el driver del LiDAR de Yahboom.

## 1. Copiar el paquete al workspace
```bash
# Robot 9 usa el workspace de fábrica:
mkdir -p ~/yahboomcar_ws/src

# Si existe una copia vieja, borra SOLO este paquete y su cache de build.
# No borres build/ install/ log/ completos porque son del stack base Yahboom.
cd ~/yahboomcar_ws
rm -rf src/capytown_maze_pkg build/capytown_maze_pkg install/capytown_maze_pkg

# Copia ESTA carpeta (capytown_maze_pkg) dentro de src/
cp -r ~/capytown_maze_pkg ~/yahboomcar_ws/src/
```

## 2. Compilar
```bash
cd ~/yahboomcar_ws
colcon build --packages-select capytown_maze_pkg
source install/setup.bash
```
(Agrega `source ~/yahboomcar_ws/install/setup.bash` a tu ~/.bashrc para no repetirlo.)

## 3. Ejecutar
```bash
ros2 launch capytown_maze_pkg maze.launch.py
```
El robot empieza a seguir la pared (por defecto, mano DERECHA), esquiva cajas como
obstáculos y completa el lazo. El launch levanta dos nodos:
- `maze_navigator`: controla `/cmd_vel`.
- `box_detector`: cuenta cajas y publica `/cajas_avistadas`.

## 4. Ajustes frecuentes
- Si la odometría está en otro topic:
  ```bash
  ros2 launch capytown_maze_pkg maze.launch.py odom_topic:=/odometry/filtered
  ```
- Cambiar de mano (izquierda) o distancias: usar launch args o editar `config/maze_params.yaml`
  (`side`, `wall_target`, `front_block`, `v_max`, etc.).
  ```bash
  ros2 launch capytown_maze_pkg maze.launch.py side:=left
  ```
- Para robot 9, usar `wall_target: 0.20`: Henry confirmó 20 cm de LiDAR a pared.
- Para el reto de cajas/lazo, mantener `course_mode: loop_boxes`,
  `enable_obstacle_veer: true` y `disable_recover_180: true`.
- Para volver al laberinto con dead-ends reales:
  ```bash
  ros2 launch capytown_maze_pkg maze.launch.py course_mode:=maze enable_obstacle_veer:=false disable_recover_180:=false recover_persist:=1.0
  ```
- Si ondula siguiendo pared: bajar `kp` o subir `deadband`.
- Mantener `open_space_straight: false`: el lazo debe resolverse con wall-follower,
  no como pista abierta.

## 5. Si "no se mueve y no da error" (la trampa #1)
Casi siempre es el **QoS del LiDAR**. Los drivers publican `/scan` como BEST_EFFORT;
un suscriptor RELIABLE recibe CERO mensajes en silencio. Este nodo ya suscribe
BEST_EFFORT (compatible con ambos), pero verifica:
```bash
ros2 topic echo /scan        # ¿llega data?
ros2 topic hz /scan          # ¿~10 Hz?
```
Si `/scan` llega pero el robot no arranca, revisa que el base node de Yahboom esté
vivo y escuchando `/cmd_vel` (`ros2 topic echo /cmd_vel` mientras corre el nodo).

## 6. Guardas de seguridad ya integradas
- Freeze si el LiDAR deja de publicar (`scan_timeout`).
- Watchdog de giro si la odometría se congela (`turn_timeout`).
- Parada de emergencia si algo entra a `emerg_dist` (0.15 m) al frente.
- Recuperación si avanza pero no progresa (retrocede).

## 7. Probar la lógica SIN robot (en cualquier PC)
```bash
cd capytown_maze_pkg
python3 test_maze_logic.py
python3 test_maze_scenarios.py
python3 -m py_compile capytown_maze_pkg/maze_navigator.py capytown_maze_pkg/box_detector.py capytown_maze_pkg/loop_completion.py capytown_maze_pkg/split_merge.py launch/maze.launch.py
```
Todos deben terminar sin errores.

## 8. Comandos útiles durante la prueba
Ver que el robot recibe velocidad:
```bash
ros2 topic echo /cmd_vel
```

Ver si el detector está contando cajas:
```bash
ros2 topic echo /cajas_avistadas
```

Ver frecuencia del LiDAR:
```bash
ros2 topic hz /scan
```

Parar el robot de emergencia:
```bash
ros2 topic pub --once /cmd_vel geometry_msgs/msg/Twist '{}'
```

## Nota de topología
El reto actual es un lazo/circuito con isla central, no un laberinto con callejones.
Por eso el modo `loop_boxes` desactiva el 180 de `RECOVER`. Si después vuelven al
reto final de laberinto, cambiar a `course_mode:=maze`.
