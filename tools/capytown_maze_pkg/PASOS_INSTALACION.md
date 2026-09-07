# Pasos de instalación — capytown_maze_pkg (robot nuevo, desde cero)

Plataforma: **Yahboom MicroROS-Pi5 Robot Car** (RPi5-4GB, LiDAR MS200). Solo usa
`/scan` (LiDAR 2D) + `/odom_raw`; publica `/cmd_vel`. Sin cámara, sin mapa.

---

## 0) Requisitos previos (en la Raspberry Pi 5 del robot)
1. **ROS 2 Humble** instalado y funcionando en la RPi5 (Ubuntu 22.04 / el que trae el Yahboom).
   ```bash
   ros2 --version          # debe responder (Humble)
   ```
2. **La base Yahboom corriendo** y publicando sus tópicos. Encendé el carrito y arrancá
   su stack base (driver del chasis + LiDAR). Verificá que existan:
   ```bash
   ros2 topic list | grep -E "scan|odom|cmd_vel"
   # deben aparecer:  /scan   /odom_raw   /cmd_vel
   ros2 topic echo /scan --once     # debe salir un LaserScan (si cuelga = LiDAR no publica)
   ros2 topic echo /odom_raw --once # debe salir nav_msgs/Odometry
   ```
   Si `/scan` no sale → el LiDAR MS200 no está arrancado; arrancá el launch del LiDAR del Yahboom primero.

## 1) Usar el workspace correcto del robot 9
```bash
mkdir -p ~/yahboomcar_ws/src
cd ~/yahboomcar_ws
```

## 2) Limpiar solo el paquete viejo y copiar el nuevo
No borres `build/`, `install/` ni `log/` completos: ahí puede estar el stack base de Yahboom.
Descomprimí el zip y copiá la carpeta `capytown_maze_pkg/` dentro de `~/yahboomcar_ws/src/`:
```bash
cd ~/yahboomcar_ws
rm -rf src/capytown_maze_pkg build/capytown_maze_pkg install/capytown_maze_pkg

cd ~
unzip -o capytown_maze_pkg_loop_boxes_v13_20260701.zip
cp -r capytown_maze_pkg ~/yahboomcar_ws/src/
ls ~/yahboomcar_ws/src/capytown_maze_pkg   # debe verse package.xml, setup.py, capytown_maze_pkg/, config/, launch/
```

## 3) Instalar dependencias ROS (una vez)
```bash
cd ~/yahboomcar_ws
rosdep install --from-paths src --ignore-src -r -y   # rclpy, sensor_msgs, nav_msgs, geometry_msgs
```
(Si `rosdep` no está: `sudo apt install python3-rosdep && sudo rosdep init && rosdep update`.)

## 4) Compilar
```bash
cd ~/yahboomcar_ws
colcon build --packages-select capytown_maze_pkg
source install/setup.bash          # ¡cada terminal nueva necesita este source!
```

## 5) Configuración actual: reto de cajas/lazo
Editá `~/yahboomcar_ws/src/capytown_maze_pkg/config/maze_params.yaml`. Para el robot 9 ya queda:
```yaml
course_mode: "loop_boxes"    # reto actual: circuito con isla central + cajas
odom_topic: "/odom_raw"
wall_clearance: 0.12         # borde->pared; produce ~20cm LiDAR->pared
corridor_width: 0.60         # pasillo aproximado del robot 9
wall_target: 0.20            # lectura LiDAR objetivo confirmada
wall_align_enabled: true     # usa derecha-delantera/derecha-trasera para no pasarse de giro
wall_align_tol: 0.04         # rf/rb casi iguales = paralelo a pared derecha
wall_align_max_range: 0.50   # solo confiar si la pared derecha está cerca
corner_align_enabled: true   # esquina correcta: frente libre + pared atrás + pared derecha
corner_rear_max: 0.45        # pared atrás visible
corner_side_max: 0.38        # pared derecha visible
corner_min_turn_t: 0.45      # no cortar giro al primer instante
front_sector: 12.0           # evita falso TURN_IN por paredes laterales
front_block_persist: 0.35    # bloqueo frontal debe persistir antes de girar
enable_obstacle_veer: true    # esquiva cajas sin hacer 180
disable_recover_180: true     # este reto NO tiene callejones; caja no dispara RECOVER
recover_persist: 3.0          # margen extra contra falso dead-end
veer_resume_t: 0.0            # devuelve control inmediato al wall-follower
veer_grace_t: 0.8             # solo evita re-detectar la misma caja
obstacle_detect: 0.35         # detecta caja más cerca, no con tanta anticipación
box_shoulder_margin: 0.12     # distingue caja localizada vs pared ancha
veer_min_dist: 0.85           # no vuelve a ruta hasta pasar la protrusión
veer_min_t: 2.3               # tiempo mínimo de paso
veer_out_angle: 4.0           # cambio de carril mínimo; no rodeo con giro
veer_out_speed: 0.10          # avanza durante OUT; no gira en seco
veer_turn_speed: 0.08         # giro máximo durante OUT
veer_pass_speed: 0.08         # velocidad al pasar por el costado
veer_max_yaw_delta: 25.0      # si se desvía demasiado, corta el rodeo
veer_finish_yaw_tol: 25.0     # no termina esquive mirando al sentido contrario
veer_force_away_from_wall: true # en pared derecha, esquiva hacia izquierda
veer_back_enabled: false      # no hacer giro fuerte de regreso; reacopla wall-follower
post_veer_reacquire_t: 3.0    # bloquea falso TURN_OUT al salir de la caja
post_veer_reacquire_dist: 0.30 # avanza antes de permitir giros normales otra vez
post_veer_wall_max: 0.42      # pared derecha visible = reacoplado
post_veer_w_max: 0.10         # corrección suave; nunca giro de 90 grados
debug_report_enabled: true    # crea informe de decisiones del robot
debug_report_path: "/tmp/capytown_maze_report.log"
straight_when_no_wall: true   # avanza recto si no ve pared lateral
```
(Detalle de cada perilla en `GUIA_AJUSTES_NUEVO_MAZE.md`.) Recompilá tras editar el yaml
si lo instalaste por `colcon` (o usá `--ros-args -p ...` para probar al vuelo sin recompilar).

## 6) Correr
```bash
source ~/yahboomcar_ws/install/setup.bash
ros2 launch capytown_maze_pkg maze.launch.py
```
En otra terminal podés ver el informe en vivo:
```bash
tail -f /tmp/capytown_maze_report.log
```
Si falla, guardá el informe:
```bash
cp /tmp/capytown_maze_report.log ~/capytown_maze_report_$(date +%H%M%S).log
```
Probar parámetros al vuelo sin recompilar:
```bash
ros2 launch capytown_maze_pkg maze.launch.py side:=left
ros2 launch capytown_maze_pkg maze.launch.py front_angle_offset:=90.0
ros2 launch capytown_maze_pkg maze.launch.py obstacle_detect:=0.30 veer_min_dist:=0.90 veer_min_t:=2.5
ros2 launch capytown_maze_pkg maze.launch.py veer_out_angle:=3.0 veer_turn_speed:=0.06 veer_max_yaw_delta:=18.0
ros2 launch capytown_maze_pkg maze.launch.py post_veer_reacquire_t:=3.5 post_veer_reacquire_dist:=0.40 post_veer_w_max:=0.08
ros2 launch capytown_maze_pkg maze.launch.py wall_align_tol:=0.03 wall_align_w_max:=0.22
ros2 launch capytown_maze_pkg maze.launch.py corner_rear_max:=0.50 corner_side_max:=0.42
ros2 launch capytown_maze_pkg maze.launch.py course_mode:=maze enable_obstacle_veer:=false disable_recover_180:=false
```

## 7) Verificar que se mueve
```bash
ros2 topic echo /cmd_vel        # al ver una pared debe publicar velocidades (no ceros)
```
- **Si NO se mueve y NO da error** → casi siempre es QoS del `/scan`: el nodo ya se suscribe
  BEST_EFFORT (correcto). Confirmá que `/scan` publica (`ros2 topic echo /scan --once`).
- **Si gira y choca al girar** → pasillo más angosto que la diagonal del robot; el nodo ya
  retrocede en vez de rotar, pero revisá `robot_width`/`robot_length` en el yaml = tu robot real.
- **Si se pasa de giro tras esquivar** → mirá el log `[DBG] rf/rb=.../...`.
  Si `rf` y `rb` son muy distintos, aún no está paralelo a la pared derecha; si son casi iguales
  y sigue girando, bajá `wall_align_tol` a `0.03`.
- **Si se pasa de giro en una esquina** → mirá `[DBG] front=... right=... rear=...`.
  La esquina correcta debe tener `front` libre, `rear` cerca y `right` cerca.
- **Si el odom va por otro tópico** (p.ej. `/odometry/filtered`):
  `ros2 run capytown_maze_pkg maze_navigator --ros-args -p odom_topic:=/odometry/filtered`

## 8) Parar
`Ctrl+C` en la terminal del launch. El nodo publica cmd_vel=0 al cerrar.

---

### Test sin robot (en cualquier PC, sin ROS) — para confirmar la lógica
```bash
cd capytown_maze_pkg
python3 test_maze_logic.py        # PASS
python3 test_maze_scenarios.py    # scenarios PASS
```
