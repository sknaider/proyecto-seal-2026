# Integración del FSM del laberinto — handoff para el equipo de Henry
**Autor:** JARVIS · CapyTown Escenario D · 10-jul-2026
**Para:** el equipo de Henry que hace el nodo ROS que envuelve el FSM.

El FSM de navegación (recorrer el laberinto → salida, con PARE) ya está construido, probado
y endurecido. **Ustedes hacen el nodo ROS**; el FSM es una clase PURA que solo necesitan
alimentar con sensores y de la que sacan el comando. Sin dependencias de ROS adentro.

## Archivos
- `capytown_maze_pkg/maze_fsm.py` — el FSM. Clase `MazeFSM`, self-test 7/7 verde + verificado adversarialmente por FABLE (builder≠verifier).
- `capytown_maze_pkg/granprix_metrics.py` — logger de métricas E1 (tiempo/colisiones/PARE/exploración → CSV). Opcional, self-test verde.
- `test_maze_fsm_integration.py` — recorrido end-to-end de ejemplo.

## Contrato (lo único que su nodo ROS tiene que hacer)
```python
from capytown_maze_pkg.maze_fsm import MazeFSM, SensorFrame

fsm = MazeFSM()

# ...en su callback de control (~10-20 Hz), armen un SensorFrame con lo que ya publican:
frame = SensorFrame(
    front = dist_frontal,      # m, del /scan (sector delantero, mínimo)
    right = dist_derecha,      # m, del /scan (sector ~-90°)
    left  = dist_izquierda,    # m, del /scan (sector ~+90°)
    pare  = pare_camara,       # bool: True cuando la cámara detecta el PARE (rojo) — de su nodo de visión
    boxes = cajas_avistadas,   # int: opcional, del box_detector existente
    x = odom_x, y = odom_y, yaw = odom_yaw,   # de /odom_raw
    at_goal = llego_a_meta,    # bool: True al detectar la celda META (marker/última celda)
)

d = fsm.step(frame, now_segundos)   # now = tiempo actual en segundos (float)

# publiquen el comando:
twist.linear.x  = d.v      # m/s (0 = detenido)
twist.angular.z = d.w      # rad/s
cmd_vel_pub.publish(twist)

# eventos útiles para métricas/RViz:
if d.log_pare:   metrics.update(now, pare_respetado=True)   # respetó un PARE
if d.mark_cell:  publicar_marker(d.mark_cell)               # celda visitada (RViz)
```

## Qué YA resuelve el FSM (cumple la matriz de ALICE)
- **F1 arbitraje:** si `pare=True`, PARA aunque el pasillo esté libre (cámara manda sobre LiDAR).
- **V3:** parada completa ~3s ante el PARE, luego reanuda (y no re-para en la misma celda).
- **F2:** máquina de estados completa (EXPLORAR→INTERSECCION→PARAR_PARE→ESPERAR_3S→GIRAR→DEAD_END→META).
- **N2:** decide en intersección (regla de mano-derecha sobre la pared seguida).
- **N3:** dead-end → giro 180° + marca celda visitada.

## Lo que necesitan del lado de ustedes (fuera del FSM)
1. **Nodo de VISIÓN** que publique `pare` (True al detectar el rojo del PARE) — la otra pieza core que pidió Henry. (El equipo SEAL puede pasar el detector RC si ayuda.)
2. Mapear los sectores del `/scan` a front/right/left (umbral de sector; en `maze_navigator.py` ya hay helpers de sector reutilizables).
3. El QoS de `/scan` = `qos_profile_sensor_data` (BEST_EFFORT) — si no, el LiDAR no matchea. (Lección ya aprendida en el maze_solver previo.)
4. Ajuste fino sobre el ROBOT REAL: los umbrales (FRONT_BLOCK, SIDE_OPEN...) están calibrados a robot 9 pero **el gate final es el hardware** — validar por efecto sobre el robot, no en teoría.

Cualquier ajuste del FSM que necesiten al probar en el robot, me dicen y lo endurezco.
